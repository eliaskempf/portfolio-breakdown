"""Currency-aware futures experiments; never actual journal transactions.

All runners use the same close-to-close cash engine. Inputs are immutable
snapshots, prices are native contract points, and FX means reporting units per
currency unit. An observation's price is the OLD contract close; a roll supplies
the NEW entry separately. Notional is exposure and never an FX cash asset.
"""
from dataclasses import dataclass
from datetime import date
import math
from types import MappingProxyType
from typing import Mapping

import numpy as np
import pandas as pd

from portfolio_app.holdings import DataError

CURRENCIES = ('EUR', 'USD', 'GBP')
MODEL_VERSION = 'generalized-funding-v1'


def _number(value, label, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DataError(f'{label} must be finite.')
    if minimum is not None and value < minimum:
        raise DataError(f'{label} must be at least {minimum}.')


def _currency(value):
    if value not in CURRENCIES:
        raise DataError('Studies support EUR, USD and GBP.')


def _day(value):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError) as exc:
        raise DataError('Study dates must use YYYY-MM-DD.') from exc


@dataclass(frozen=True)
class ScenarioContract:
    product: str
    currency: str
    multiplier: float
    expiry: str
    tick: float = .01
    return_convention: str = 'unknown'

    def __post_init__(self):
        _currency(self.currency)
        _day(self.expiry)
        if not self.product.strip():
            raise DataError('Choose an identified contract.')
        _number(self.multiplier, 'Point multiplier', minimum=1e-12)
        _number(self.tick, 'Tick', minimum=1e-12)

    @classmethod
    def from_spec(cls, spec):
        return cls(spec.product, spec.currency, float(spec.multiplier),
                   spec.last_trade_date or spec.expiry, float(spec.tick), spec.return_convention)


@dataclass(frozen=True)
class CashPool:
    currency: str
    amount: float

    def __post_init__(self):
        _currency(self.currency)
        _number(self.amount, 'Cash balance')


@dataclass(frozen=True)
class Collateral:
    """Dedicated noncash assets, included in scenario wealth exactly once."""
    currency: str
    value: float
    haircut: float = .2

    def __post_init__(self):
        _currency(self.currency)
        _number(self.value, 'Collateral value', minimum=0)
        _number(self.haircut, 'Collateral haircut', minimum=0)
        if self.haircut > 1:
            raise DataError('Collateral haircut cannot exceed 100%.')


@dataclass(frozen=True)
class InterestRate:
    currency: str
    bank_credit: float = 0.
    broker_credit: float = 0.
    broker_debit: float = 0.
    effective_on: str = '0001-01-01'
    day_count: int = 360

    def __post_init__(self):
        _currency(self.currency)
        _day(self.effective_on)
        if self.day_count not in (360, 365):
            raise DataError('Interest day count must be 360 or 365.')
        for value in (self.bank_credit, self.broker_credit, self.broker_debit):
            _number(value, 'Annual interest rate', minimum=-.99)


@dataclass(frozen=True)
class ScheduledPayment:
    date: str
    currency: str
    amount: float
    kind: str = 'tax'
    pool: str = 'broker'

    def __post_init__(self):
        _day(self.date)
        _currency(self.currency)
        _number(self.amount, 'Payment', minimum=0)
        if self.kind not in ('tax', 'fee') or self.pool not in ('bank', 'broker'):
            raise DataError('Payments must be manual taxes or fees from bank or broker cash.')


@dataclass(frozen=True)
class StudyPolicy:
    target: float
    trigger: float
    sources: tuple[str, ...] = CURRENCIES
    delay: int = 3
    conversion_cost: float = 0.
    transfer_fee: float = 0.
    reenter: bool = True
    reset: str = 'monthly'
    mode: str = 'target'
    proportional: bool = False
    withdraw_excess: bool = False

    def __post_init__(self):
        for label, value in (('Target', self.target), ('Trigger', self.trigger),
                             ('Transfer fee', self.transfer_fee), ('Conversion cost', self.conversion_cost)):
            _number(value, label, minimum=0)
        if self.conversion_cost >= 1 or self.trigger > self.target:
            raise DataError('Conversion cost must be below 100%; trigger cannot exceed target.')
        if type(self.delay) is not int or not 1 <= self.delay <= 21:
            raise DataError('Transfer delay must be 1–21 observations.')
        if len(set(self.sources)) != len(self.sources) or not self.sources:
            raise DataError('Use unique, ordered source currencies.')
        for currency in self.sources:
            _currency(currency)
        if self.reset not in ('monthly', 'quarterly', 'never') or type(self.reenter) is not bool:
            raise DataError('Choose monthly, quarterly or no scheduled resets.')
        if self.mode not in ('target', 'buffer') or type(self.proportional) is not bool or type(self.withdraw_excess) is not bool:
            raise DataError('Choose a target or maintenance buffer and explicit scaling/withdrawal settings.')


@dataclass(frozen=True)
class ScenarioInput:
    contract: ScenarioContract
    quantity: int
    initial_price: float
    reporting_currency: str = 'EUR'
    bank: tuple[CashPool, ...] = ()
    broker: tuple[CashPool, ...] = ()
    collateral: tuple[Collateral, ...] = ()
    initial_margin: float = 0.
    maintenance_margin: float = 0.
    fee_per_side: float = 0.
    slippage_points: float = 0.
    foreign_cash_haircut: float = .1
    settlement_credit_limit: float = 0.
    rates: tuple[InterestRate, ...] = ()
    payments: tuple[ScheduledPayment, ...] = ()
    scale_margin: bool = False

    def __post_init__(self):
        _currency(self.reporting_currency)
        if type(self.quantity) is not int or not 0 < abs(self.quantity) <= 10000:
            raise DataError('Use a nonzero whole signed contract quantity, at most 10,000.')
        _number(self.initial_price, 'Initial price', minimum=1e-12)
        for name in ('initial_margin', 'maintenance_margin', 'fee_per_side', 'slippage_points',
                     'foreign_cash_haircut', 'settlement_credit_limit'):
            _number(getattr(self, name), name, minimum=0)
        if self.maintenance_margin > self.initial_margin or self.foreign_cash_haircut > 1:
            raise DataError('Maintenance cannot exceed initial margin; haircut cannot exceed 100%.')
        for pools in (self.bank, self.broker):
            if len({p.currency for p in pools}) != len(pools):
                raise DataError('Use one cash balance per currency and location.')
        if any(p.amount < 0 for p in self.bank):
            raise DataError('Initial bank reserves cannot be negative.')
        rate_keys = [(r.currency, r.effective_on) for r in self.rates]
        if len(set(rate_keys)) != len(rate_keys):
            raise DataError('Interest schedules require unique currency/effective dates.')


@dataclass(frozen=True)
class Observation:
    date: str
    price: float
    fx: Mapping[str, float]
    roll_entry_price: float | None = None
    next_expiry: str = ''
    source: str = 'Manual scenario'
    index_level: float | None = None

    def __post_init__(self):
        _day(self.date)
        _number(self.price, 'Settlement price', minimum=1e-12)
        if self.index_level is not None:
            _number(self.index_level, 'Underlying index level', minimum=1e-12)
        if self.roll_entry_price is not None:
            _number(self.roll_entry_price, 'New contract entry', minimum=1e-12)
            if not self.next_expiry or _day(self.next_expiry) <= _day(self.date):
                raise DataError('A roll needs a later next-contract expiry.')
        elif self.next_expiry:
            raise DataError('A next expiry needs an explicit roll entry price.')
        copied = dict(self.fx)
        for currency, rate in copied.items():
            _currency(currency)
            _number(rate, 'FX rate', minimum=1e-12)
        object.__setattr__(self, 'fx', MappingProxyType(copied))


@dataclass(frozen=True)
class StudyResult:
    events: pd.DataFrame
    initial_wealth: float
    ending_wealth: float
    attribution: Mapping[str, float]
    issues: tuple[str, ...]
    reporting_currency: str
    model: str = MODEL_VERSION

    @property
    def reconciliation_error(self):
        return self.ending_wealth - self.initial_wealth - sum(self.attribution.values())


def _validate_path(scenario, observations):
    observations = tuple(observations)
    if len(observations) < 2 or len(observations) > 20000:
        raise DataError('Use 2–20,000 dated observations.')
    dates = [o.date for o in observations]
    if dates != sorted(set(dates)):
        raise DataError('Observations must have unique increasing dates.')
    if not math.isclose(observations[0].price, scenario.initial_price, rel_tol=1e-10):
        raise DataError('The first observation must equal the scenario opening price.')
    currencies = {scenario.contract.currency, scenario.reporting_currency}
    currencies.update(p.currency for p in (*scenario.bank, *scenario.broker, *scenario.collateral, *scenario.payments))
    expiry = scenario.contract.expiry
    for i, observation in enumerate(observations):
        if currencies - observation.fx.keys() or observation.fx.get(scenario.reporting_currency) != 1.:
            raise DataError('Every observation needs positive FX for each cash/collateral currency and 1 for reporting currency.')
        if observation.date > expiry:
            raise DataError('The path passes contract expiry without an explicit roll.')
        if observation.roll_entry_price is not None:
            if i == 0 or i == len(observations)-1 or observation.next_expiry <= expiry:
                raise DataError('Rolls must occur inside the path and advance expiry.')
            expiry = observation.next_expiry
    if any(p.date not in dates for p in scenario.payments):
        raise DataError('Every scheduled payment needs an observation on its exact date.')
    return observations


def replay(scenario: ScenarioInput, observations, policy: StudyPolicy | None = None) -> StudyResult:
    """Replay a copied reserve, with maintenance checked before transfer receipt.

    Foreign cash and noncash assets can support margin after haircuts but cannot
    pay settlement. A negative settlement-currency balance is explicit borrowing;
    exceeding the declared credit limit closes the position without clipping debt.
    Same-currency internal movements and roll price gaps create no gain.
    """
    path = _validate_path(scenario, observations)
    native = scenario.contract.currency
    bank = {c: 0. for c in CURRENCIES}
    broker = bank.copy()
    bank.update({p.currency: p.amount for p in scenario.bank})
    broker.update({p.currency: p.amount for p in scenario.broker})
    assets = {c: sum(p.value for p in scenario.collateral if p.currency == c) for c in CURRENCIES}
    transit = []  # (arrival observation, native amount, destination); converted at dispatch.
    attribution = dict(market_pnl=0., fx_pnl=0., bank_interest=0., broker_interest=0.,
                       fees=0., taxes=0., transfer_costs=0.)
    issues = []
    rows = []
    count = abs(scenario.quantity)
    fee = count * (scenario.fee_per_side + scenario.slippage_points * scenario.contract.multiplier)
    held = False
    previous_price = scenario.initial_price
    paid = set()
    first = path[0]

    def balances():
        amounts = {c: bank[c] + broker[c] + assets[c] for c in CURRENCIES}
        amounts[native] += sum(amount for _, amount, _ in transit)
        return amounts

    def wealth(fx):
        return sum(amount * fx.get(c, 0.) for c, amount in balances().items())

    def eligible(fx):
        result = 0.
        for currency, amount in broker.items():
            haircut = scenario.foreign_cash_haircut if currency != native and amount > 0 else 0.
            result += amount * fx.get(currency, 0.) / fx[native] * (1-haircut)
        return result + sum(a.value * fx[a.currency]/fx[native] * (1-a.haircut) for a in scenario.collateral)

    def charge(amount, kind, observation, currency=native, pool='broker'):
        (bank if pool == 'bank' else broker)[currency] -= amount
        attribution[kind] -= amount * observation.fx[currency]

    initial_wealth = wealth(first.fx)
    previous_balances = balances()
    for i, observation in enumerate(path):
        fx = observation.fx
        opening_broker = dict(broker)
        opening_bank = dict(bank)
        events = []
        if i:
            previous = path[i-1]
            attribution['fx_pnl'] += sum(amount * (fx.get(c, 0.) - previous.fx.get(c, 0.))
                                         for c, amount in previous_balances.items())
            # Rates are selected on each accrual day, never from a future observation.
            for day in pd.date_range(previous.date, observation.date, inclusive='left'):
                stamp = day.date().isoformat()
                for currency in CURRENCIES:
                    applicable = [r for r in scenario.rates if r.currency == currency and r.effective_on <= stamp]
                    rate = max(applicable, key=lambda r: r.effective_on) if applicable else None
                    if rate is None:
                        continue
                    interest = max(bank[currency], 0.) * rate.bank_credit/rate.day_count
                    bank[currency] += interest
                    attribution['bank_interest'] += interest * fx.get(currency, 0.)
                    interest = broker[currency] * (rate.broker_credit if broker[currency] >= 0 else rate.broker_debit)/rate.day_count
                    broker[currency] += interest
                    attribution['broker_interest'] += interest * fx.get(currency, 0.)
            if held:
                pnl = scenario.quantity * scenario.contract.multiplier * (observation.price - previous_price)
                broker[native] += pnl
                attribution['market_pnl'] += pnl * fx[native]
        else:
            if eligible(fx)-fee >= count*scenario.initial_margin and broker[native]-fee >= -scenario.settlement_credit_limit:
                held = True
                charge(fee, 'fees', observation)
                events.append('open')
            else:
                issues.append('Opening reserve cannot meet initial margin and settlement-cash requirements.')

        for p, payment in enumerate(scenario.payments):
            if p not in paid and payment.date <= observation.date:
                charge(payment.amount, 'taxes' if payment.kind == 'tax' else 'fees', observation,
                       payment.currency, payment.pool)
                paid.add(p)
                events.append(payment.kind)
        scale = observation.price/scenario.initial_price if scenario.scale_margin else 1.
        maintenance = count * scenario.maintenance_margin * scale
        # Strictly BELOW maintenance breaches in this model (legacy is unchanged).
        if held and (eligible(fx) < maintenance or broker[native] < -scenario.settlement_credit_limit):
            charge(fee, 'fees', observation)
            held = False
            events.append('liquidation')
        received = sum(amount for due, amount, _ in transit if due <= i)
        broker[native] += sum(amount for due, amount, destination in transit if due <= i and destination == 'broker')
        bank[native] += sum(amount for due, amount, destination in transit if due <= i and destination == 'bank')
        transit = [(due, amount, destination) for due, amount, destination in transit if due > i]
        if received:
            events.append('transfer received')

        if observation.roll_entry_price is not None:
            if held:
                charge(fee, 'fees', observation)
                held = False
                next_scale = observation.roll_entry_price/scenario.initial_price if scenario.scale_margin else 1.
                if eligible(fx)-fee >= count*scenario.initial_margin*next_scale and broker[native]-fee >= -scenario.settlement_credit_limit:
                    charge(fee, 'fees', observation)
                    held = True
                    events.append('roll')
                else:
                    events.append('roll failure')
            previous_price = observation.roll_entry_price
        else:
            previous_price = observation.price

        if i == len(path)-1:
            if held:
                charge(fee, 'fees', observation)
                held = False
                events.append('endpoint close')
        elif policy:
            entry_scale = previous_price/scenario.initial_price if scenario.scale_margin else 1.
            if not held and policy.reenter and eligible(fx)-fee >= count*scenario.initial_margin*entry_scale and broker[native]-fee >= -scenario.settlement_credit_limit:
                charge(fee, 'fees', observation)
                held = True
                events.append('reentry')
            month_change = i and path[i-1].date[:7] != observation.date[:7]
            quarter_change = month_change and (int(observation.date[5:7])-1)//3 != (int(path[i-1].date[5:7])-1)//3
            reset = month_change if policy.reset == 'monthly' else quarter_change if policy.reset == 'quarterly' else False
            if reset or broker[native] < policy.trigger:
                target = policy.target*(previous_price/scenario.initial_price if policy.proportional else 1.)
                if policy.mode == 'buffer':
                    target += maintenance
                incoming = sum(amount for _, amount, destination in transit if destination == 'broker')
                need = max(0., target-broker[native]-incoming)
                for currency in policy.sources:
                    if need <= 1e-10 or bank[currency] <= 0:
                        continue
                    rate = fx.get(currency)
                    if rate is None:
                        raise DataError('Transfer source currency needs FX on every observation.')
                    cross = rate/fx[native]
                    cost_rate = policy.conversion_cost if currency != native else 0.
                    available = bank[currency]*cross*(1-cost_rate)-policy.transfer_fee
                    received_amount = min(need, max(0., available))
                    if received_amount <= 0:
                        continue
                    debit = (received_amount+policy.transfer_fee)/(1-cost_rate)/cross
                    bank[currency] -= debit
                    cost = debit*cross-received_amount
                    attribution['transfer_costs'] -= cost*fx[native]
                    transit.append((i+policy.delay, received_amount, 'broker'))
                    need -= received_amount
                    events.append('transfer dispatched')
                if need > 1e-8:
                    issues.append('Bank reserve cannot fully fund the requested transfer.')
                if reset and policy.withdraw_excess and not incoming:
                    # Retain enough cash to meet the declared target and margin.
                    margin_room = max(0., eligible(fx)-count*scenario.initial_margin*entry_scale) if held else max(0., broker[native])
                    debit = min(max(0., broker[native]-target), margin_room)
                    if debit > policy.transfer_fee:
                        broker[native] -= debit
                        transit.append((i+policy.delay, debit-policy.transfer_fee, 'bank'))
                        attribution['transfer_costs'] -= policy.transfer_fee*fx[native]
                        events.append('withdrawal dispatched')
        if any(value < -1e-8 for value in bank.values()):
            issues.append('A scheduled bank payment exceeds the bank reserve; the deficit remains visible.')
        row = dict(date=observation.date, price=observation.price, held=held,
                   notional=scenario.quantity*scenario.contract.multiplier*previous_price if held else 0.,
                   bank=sum(bank[c]*fx.get(c, 0.) for c in CURRENCIES),
                   broker=sum(broker[c]*fx.get(c, 0.) for c in CURRENCIES),
                   transit=sum(amount for _, amount, _ in transit)*fx[native],
                   collateral=sum(assets[c]*fx.get(c, 0.) for c in CURRENCIES),
                   eligible_collateral=eligible(fx), settlement_cash=broker[native],
                   maintenance=maintenance, wealth=wealth(fx), events=', '.join(events),
                   source=observation.source, **attribution)
        for currency in CURRENCIES:
            row[f'bank_{currency}'] = bank[currency]
            row[f'broker_{currency}'] = broker[currency]
            row[f'opening_bank_{currency}'] = opening_bank[currency]
            row[f'opening_broker_{currency}'] = opening_broker[currency]
        rows.append(row)
        previous_balances = balances()
    return StudyResult(pd.DataFrame(rows), initial_wealth, rows[-1]['wealth'],
                       MappingProxyType(attribution.copy()), tuple(dict.fromkeys(issues)), scenario.reporting_currency)


@dataclass(frozen=True)
class Roll:
    date: str
    next_expiry: str

    def __post_init__(self):
        if _day(self.next_expiry) <= _day(self.date):
            raise DataError('Roll expiry must follow the roll date.')


def reconstruct(scenario, proxy: pd.Series, fx: Mapping[str, Mapping[str, float]], *,
                rolls=(), net_carry=0., day_count=360, source='Explicit proxy reconstruction',
                proxy_currency=None, proxy_convention=None):
    """Construct approximate futures from an explicitly qualified index path.

    `net_carry` is an explicit effective carry AFTER dividend/return-convention
    adjustment. This function never assumes that an adjusted ETF is a price index.
    It anchors to today's scenario price, not to imaginary historical fills.
    """
    _number(net_carry, 'Effective carry', minimum=-.99)
    if day_count not in (360, 365):
        raise DataError('Carry day count must be 360 or 365.')
    if proxy_currency != scenario.contract.currency or proxy_convention != scenario.contract.return_convention:
        raise DataError('Explicit proxy currency and return convention must match the contract.')
    if (not isinstance(proxy.index, pd.DatetimeIndex) or proxy.index.has_duplicates or
            not proxy.index.is_monotonic_increasing or len(proxy) < 2 or
            not np.isfinite(proxy.to_numpy(dtype=float)).all() or proxy.le(0).any()):
        raise DataError('Proxy history needs ordered, unique dates and finite positive values.')
    dates = [stamp.date().isoformat() for stamp in proxy.index]
    schedule = {roll.date: roll.next_expiry for roll in rolls}
    if len(schedule) != len(rolls) or set(schedule)-set(dates):
        raise DataError('Every roll must match one unique observation date.')
    expiry = scenario.contract.expiry
    carry = lambda day, maturity: (1+net_carry/day_count)**((_day(maturity)-_day(day)).days)
    initial_spot = scenario.initial_price/carry(dates[0], expiry)
    result = []
    for stamp, value in zip(dates, proxy.to_numpy(dtype=float)):
        spot = initial_spot*value/float(proxy.iloc[0])
        price = spot*carry(stamp, expiry)
        next_expiry = schedule.get(stamp, '')
        entry = spot*carry(stamp, next_expiry) if next_expiry else None
        if stamp not in fx:
            raise DataError('Explicit FX is required for every observation date.')
        result.append(Observation(stamp, price, fx[stamp], entry, next_expiry, source, spot))
        if next_expiry:
            expiry = next_expiry
    return _validate_path(scenario, result)


def forward_paths(scenario, dates, fx, *, paths=100, annual_return=0., volatility=.2,
                  net_carry=0., day_count=360, rolls=(), seed=104729):
    """Yield seeded native-currency GBM scenarios with explicit FX and rolls."""
    _number(annual_return, 'Annual index growth')
    _number(volatility, 'Annual volatility', minimum=0)
    if type(paths) is not int or not 1 <= paths <= 5000 or volatility > 3:
        raise DataError('Use 1–5,000 paths and volatility at most 300%.')
    dates = pd.DatetimeIndex(dates)
    if len(dates) < 2 or len(dates) > 4000 or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise DataError('Use 2–4,000 increasing forward observation dates.')
    dt = np.diff(dates.values).astype('timedelta64[D]').astype(int)/365.25
    rng = np.random.default_rng(seed)
    for _ in range(paths):
        factors = np.exp((annual_return-volatility**2/2)*dt + volatility*np.sqrt(dt)*rng.standard_normal(len(dt)))
        proxy = pd.Series(np.r_[1., np.cumprod(factors)], index=dates)
        yield reconstruct(scenario, proxy, fx, rolls=rolls, net_carry=net_carry, day_count=day_count,
                          source='Hypothetical GBM; supplied FX and roll assumptions',
                          proxy_currency=scenario.contract.currency, proxy_convention=scenario.contract.return_convention)


def compare_policies(scenario, paths, policies: Mapping[str, StudyPolicy | None]):
    """Same input paths and reserve for every policy; keep infeasible runs."""
    if not policies or len(policies) > 12:
        raise DataError('Choose 1–12 policies.')
    rows, examples = [], {}
    steps = 0
    for path_index, observations in enumerate(paths):
        if path_index >= 5000:
            raise DataError('At most 5,000 paths are supported.')
        observations = tuple(observations)
        steps += len(observations)*len(policies)
        if steps > 2_000_000:
            raise DataError('Reduce paths, policies or horizon to at most two million observation-policy steps.')
        for name, policy in policies.items():
            result = replay(scenario, observations, policy)
            examples.setdefault(name, result)
            events = result.events.events
            cash, index = benchmark_outcomes(scenario, observations, result.initial_wealth)
            rows.append(dict(policy=name, path=path_index, ending_wealth=result.ending_wealth,
                             gain=result.ending_wealth-result.initial_wealth,
                             liquidated=events.str.contains('liquidation').any(),
                             roll_failed=events.str.contains('roll failure').any(),
                             infeasible=bool(result.issues), reconciliation_error=result.reconciliation_error,
                             cash_benchmark=cash, index_benchmark=index,
                             **result.attribution))
    if not rows:
        raise DataError('No study paths were supplied.')
    runs = pd.DataFrame(rows)
    summary = runs.groupby('policy', sort=False).agg(paths=('path', 'size'),
        median_wealth=('ending_wealth', 'median'), p05=('ending_wealth', lambda s: s.quantile(.05)),
        p95=('ending_wealth', lambda s: s.quantile(.95)), liquidation_fraction=('liquidated', 'mean'),
        roll_failure_fraction=('roll_failed', 'mean'), issue_fraction=('infeasible', 'mean')).reset_index()
    return summary, runs, examples


def benchmark_outcomes(scenario, observations, capital):
    """Same initial reporting capital, always-long index and bank-only cash.

    Benchmarks have no futures fees or tax schedule. Index comparison is absent
    for settlement-only paths; settlement prices are not a total-return index.
    """
    native = scenario.contract.currency
    cash = capital/observations[0].fx[native]
    for previous, current in zip(observations, observations[1:]):
        for day in pd.date_range(previous.date, current.date, inclusive='left'):
            rates = [r for r in scenario.rates if r.currency == native and r.effective_on <= day.date().isoformat()]
            if rates:
                rate = max(rates, key=lambda r: r.effective_on)
                cash *= 1+rate.bank_credit/rate.day_count
    first, last = observations[0], observations[-1]
    index = (capital/first.fx[native] * last.index_level/first.index_level * last.fx[native]
             if first.index_level is not None and last.index_level is not None else float('nan'))
    return cash*last.fx[native], index


def funding_cohorts(scenario, proxy, historical_fx, *, start, opening_fx,
                    window=253, step=21, rolls=(), net_carry=0., day_count=360,
                    low_volatility=None, proxy_currency=None, proxy_convention=None):
    """Replay equal historical windows against today's explicitly dated contract.

    FX percentage moves are rebased to the supplied opening rates. The source
    dates remain metadata; calendar dates are translated scenario dates. A
    volatility gate uses only 20 returns BEFORE each cohort, never its future.
    """
    if type(window) is not int or window < 2 or type(step) is not int or step < 1:
        raise DataError('Cohort window and step must be positive whole observations.')
    if low_volatility is not None:
        _number(low_volatility, 'Opening volatility limit', minimum=0)
    _day(start)
    vol = proxy.pct_change().rolling(20, min_periods=20).std().shift(1)*math.sqrt(260)
    produced = 0
    for offset in range(0, len(proxy)-window+1, step):
        if low_volatility is not None and (pd.isna(vol.iloc[offset]) or vol.iloc[offset] > low_volatility):
            continue
        segment = proxy.iloc[offset:offset+window]
        dates = pd.DatetimeIndex(pd.Timestamp(start)+(segment.index-segment.index[0]))
        base_day = segment.index[0].date().isoformat()
        fx = {}
        for actual, translated in zip(segment.index, dates):
            actual_day = actual.date().isoformat()
            if actual_day not in historical_fx or base_day not in historical_fx:
                raise DataError('Every historical cohort observation requires explicit FX.')
            fx[translated.date().isoformat()] = {
                currency: rate*historical_fx[actual_day][currency]/historical_fx[base_day][currency]
                for currency, rate in opening_fx.items()}
        selected_rolls = tuple(r for r in rolls if r.date in {d.date().isoformat() for d in dates})
        source = f'Historical stress cohort {base_day} to {segment.index[-1].date()}; translated calendar and FX moves'
        yield reconstruct(scenario, pd.Series(segment.to_numpy(), index=dates), fx,
            rolls=selected_rolls, net_carry=net_carry, day_count=day_count, source=source,
            proxy_currency=proxy_currency, proxy_convention=proxy_convention)
        produced += 1
    if not produced:
        raise DataError('No complete cohorts meet the history and prior-volatility requirements.')
