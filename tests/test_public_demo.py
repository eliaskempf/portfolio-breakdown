"""The user-facing portfolio is reconciled and all seed evidence is invented."""
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from portfolio_app.demo_data.demo import initialize_live_demo, live_demo_pending
from portfolio_app.demo_data.public_demo import create_public_demo
from portfolio_app.ledger.account_journal import load_journal, replay
from portfolio_app.ledger.security_history import effective_holdings
from portfolio_app.market.prices import PriceService, StaticProvider
from portfolio_app.performance.history_observations import HistoryScope, load_observations
from portfolio_app.performance.history_view import history_view
from portfolio_app.portfolio.allocation import load_allocation
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.portfolio.valuation import value_holdings
from portfolio_app.workspace.display_preferences import load_preferences, save_overview_view


END = date(2025, 11, 26)


def test_offline_demo_reconciles_security_and_savings_history(tmp_path):
    directory = create_public_demo(tmp_path / 'public', end=END)
    assert load_preferences(directory).overview_view == 'Allocation'
    snapshot = read_snapshot(directory / 'holdings.csv')
    journal, _ = load_journal(directory)
    store, _ = load_observations(directory)
    assert len(journal.securities) == 6
    cash = replay(journal).cash['demo-savings', 'EUR']
    assert cash == 4400 + 550 - 50 + 100
    assert journal.accounts[0].name == 'Emergency fund'
    assert journal.accounts[0].cash_type == 'Savings'
    config = load_allocation(directory / 'allocation.yaml', snapshot.holdings)
    assert {b.id: b.target for b in config.buckets} == {
        'equity': .60, 'money-market': .20, 'gold': .10, 'crypto': .05, 'cash': .05}
    holdings = effective_holdings(snapshot.holdings, journal.securities)
    assert list(holdings.shares) == pytest.approx(snapshot.holdings.shares)
    assert list(holdings.acquisition_price) == pytest.approx(snapshot.holdings.acquisition_price)
    valued = value_holdings(holdings, PriceService(StaticProvider(directory / 'demo_prices.json')))
    assert valued.current_value_reporting.sum() + float(cash) == pytest.approx(93184.35)
    scope = HistoryScope(tuple(b.position_id for b in journal.securities), ('demo-savings',))
    for period in ('1M', '1Y', 'All'):
        view = history_view(journal, scope, store, period, today=END)
        assert view.useful and view.end == END.isoformat()
        assert view.result.time_weighted is not None and view.result.money_weighted is not None
        assert float(view.result.ending_value) == pytest.approx(93184.35)
    assert date.fromisoformat(view.start) <= date(2023, 5, 26)
    # Verify recorded cash contributions and the external net trade payments
    # independently of the performance engine's flow extraction.
    trades = {e.id: e for b in journal.securities for e in b.records}
    buy, sale, eth = (trades[i] for i in ('world-buy', 'world-sale', 'ethereum-buy'))
    initial_buys = {b.position_id: next(e for e in b.records if e.id.endswith('-first-buy'))
                    for b in journal.securities if b.instrument_id != 'ethereum'}
    initial_cost = sum(e.quantity * e.price for e in initial_buys.values())
    net = initial_cost + 550 - 50 + buy.quantity * buy.price + 2 - sale.quantity * sale.price + 2 + eth.quantity * eth.price
    assert not view.all_known.partial and view.all_known.coverage_note == 'Complete gain history'
    assert len({e.date for e in initial_buys.values()}) == 1
    purchase_day = initial_buys['demo-world'].date
    assert abs(view.result.net_contributions - net) < Decimal('.01')
    assert abs(view.result.gain - (view.result.ending_value - view.result.opening_value - net)) < Decimal('.01')
    # Price the invented units and bank statement independently at each close.
    # Daily contributions enter before the return, withdrawals leave afterwards.
    units = dict.fromkeys(snapshot.holdings.position_id, Decimal(0))
    bank = {e.id: e for e in journal.events}
    flows = {purchase_day: initial_cost, sale.date: -sale.price * sale.quantity + 2, buy.date: buy.price * buy.quantity + 2,
             eth.date: eth.price * eth.quantity, bank['savings-deposit'].date: Decimal(550),
             bank['savings-withdrawal'].date: Decimal(-50)}
    quotes = {}
    for obs in store.observations:
        if obs.kind == 'price':
            quotes.setdefault(obs.date, {})[obs.subject] = obs.amount
    balance, factor = Decimal(4400), Decimal(1)
    previous = None
    for day, prices in sorted(quotes.items()):
        if day == purchase_day:
            units.update({key: e.quantity for key, e in initial_buys.items()})
        elif day == sale.date:
            units['demo-world'] -= sale.quantity
        elif day == buy.date:
            units['demo-world'] += buy.quantity
        elif day == eth.date:
            units['demo-ethereum'] += eth.quantity
        if day in (bank['savings-deposit'].date, bank['savings-withdrawal'].date):
            balance += flows[day]
        elif day == bank['savings-interest'].date:
            balance += 100
        close = balance + sum(quantity * prices[key] for key, quantity in units.items())
        flow = flows.get(day, Decimal(0))
        if previous is None:
            initial = close
        else:
            factor *= (close - min(flow, 0)) / (previous + max(flow, 0))
        previous = close
    assert view.result.time_weighted == pytest.approx(float(factor - 1), abs=1e-10)
    # An investor pays the opening value and each net inflow and receives the
    # terminal portfolio. XIRR discounts that dated schedule to zero (ACT/365).
    origin = date.fromisoformat(min(quotes))
    rate = 1 + view.result.money_weighted
    npv = -float(initial) + float(close) / rate ** ((END - origin).days / 365)
    for day, amount in flows.items():
        npv -= float(amount) / rate ** ((date.fromisoformat(day) - origin).days / 365)
    assert abs(npv) < .0001
    assert {o.source for o in store.observations} == {'Invented demo close', 'Invented demo FX'}
    from portfolio_app.market.history import DemoHistoryProvider
    market = DemoHistoryProvider(directory)
    closes = market.history('XDWD.DE', '1mo')
    assert len(closes.dates) == 30 and closes.dates[-1] == END.isoformat()
    assert closes.prices[-1] == 100 and market.history('USDEUR=X', '1mo').prices[-1] == .9
    assert not market.history('UNKNOWN', '1mo').dates
    assert not (directory / '.live-demo').exists()
    # Adding a bank account activates the actual-account exposure engine. The
    # overnight ETF still reveals its benchmark and preserves its funded value.
    from portfolio_app.exposure.portfolio_exposure import calculate_portfolio_exposure
    from portfolio_app.funds.etf import load_funds
    exposure = calculate_portfolio_exposure(valued, load_funds(directory / 'etfs'), allocation=config, reporting_currency='EUR')
    overnight, = exposure.exposures.loc[exposure.exposures.source_position_id.eq('demo-money-market')].itertuples()
    assert 'overnight rate' in overnight.asset_name
    assert overnight.value == pytest.approx(15441.67) and overnight.exposure_reporting == 0
    assert overnight.market_exposure_reporting == pytest.approx(overnight.value)


def test_live_seed_publishes_history_once_after_quote_sizing(tmp_path, monkeypatch):
    live = create_public_demo(tmp_path / 'live', live=True, end=END)
    static = create_public_demo(tmp_path / 'offline', end=END)
    service = PriceService(StaticProvider(static / 'demo_prices.json'))
    holdings = read_snapshot(live / 'holdings.csv').holdings
    valued = value_holdings(holdings, service)
    assert load_preferences(live).overview_view == 'Allocation'
    assert live_demo_pending(live)
    assert not initialize_live_demo(live, valued.iloc[1:])
    assert not load_journal(live)[0].securities
    assert initialize_live_demo(live, valued)
    assert not live_demo_pending(live)
    journal, revision = load_journal(live)
    assert len(journal.securities) == 6 and replay(journal).cash['demo-savings', 'EUR'] == 5000
    current = effective_holdings(read_snapshot(live / 'holdings.csv').holdings, journal.securities)
    assert value_holdings(current, service).current_value_reporting.sum() + 5000 == pytest.approx(93184.35, abs=.05)
    assert not (live / 'demo_prices.json').exists() and not load_observations(live)[0].observations
    assert not list((live / 'etfs').iterdir())
    assert not initialize_live_demo(live, valued)
    save_overview_view(live, 'Performance')
    assert create_public_demo(live, end=END) == live
    assert load_preferences(live).overview_view == 'Performance'
    assert load_journal(live)[1] == revision

    # The normal background downloader gets every holding from the persisted
    # public seed. A missing listing stays missing until a subsequent download.
    from portfolio_app.performance import personal_market
    offline, _ = load_observations(static)
    fail = True
    requested = set()
    def provider(ticker, identity, start, end):
        requested.add(ticker)
        if fail and identity == 'demo-world':
            raise ValueError('Invented provider outage')
        return tuple(replace(o, source='Invented provider response', listing=ticker)
                     for o in offline.observations if o.subject == identity and start <= o.date <= end)
    monkeypatch.setattr(personal_market, 'fetch_security_prices', provider)
    scope = HistoryScope(tuple(book.position_id for book in journal.securities), ('demo-savings',))
    start = journal.securities[0].records[0].date
    store = load_observations(live)[0]
    partial, errors = personal_market.download_scope_observations(live, journal, scope, store, start, END.isoformat(), 'EUR')
    assert errors and not any(o.subject == 'demo-world' for o in partial.observations)
    fail = False
    fetched, errors = personal_market.download_scope_observations(live, journal, scope, partial, start, END.isoformat(), 'EUR')
    assert not errors and requested == set(holdings.ticker)
    assert {o.source for o in fetched.observations} == {'Invented provider response'}
    result = history_view(journal, scope, fetched, 'All', today=END).all_result
    assert result.money_weighted is not None and result.time_weighted is not None


def test_public_demo_refuses_occupied_directory(tmp_path):
    preserved = tmp_path / 'invented.txt'
    preserved.write_text('Invented existing file')
    with pytest.raises(DataError, match='empty directory'):
        create_public_demo(tmp_path)
    assert list(tmp_path.iterdir()) == [preserved]
