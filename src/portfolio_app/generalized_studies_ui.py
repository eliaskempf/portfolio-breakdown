"""Normal-workspace studies; settings writes never touch the actual journal."""
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from portfolio_app.generalized_studies import (
    CURRENCIES, CashPool, Collateral, InterestRate, Observation, Roll,
    ScenarioContract, ScenarioInput, ScheduledPayment, StudyPolicy,
    compare_policies, forward_paths, funding_cohorts, reconstruct,
)
from portfolio_app.holdings import DataError
from portfolio_app.storage import save_document

SETTINGS_FILE = 'generalized-study-settings.json'
MODES = ('Forward Monte Carlo', 'Rolling Monte Carlo', 'Historical settlements',
         'Historical proxy reconstruction', 'Funding policy cohorts')


def load_study_settings(directory):
    path = Path(directory)/SETTINGS_FILE
    if not path.exists():
        return {}, None
    try:
        content = path.read_bytes()
        raw = json.loads(content)
        if raw.get('version') != 1 or not isinstance(raw.get('scenarios'), dict):
            raise ValueError('Unsupported study settings')
        if any(not isinstance(key, str) or not isinstance(value, dict) for key, value in raw['scenarios'].items()):
            raise ValueError('Invalid scenario settings')
        json.dumps(raw, allow_nan=False)
        return raw['scenarios'], sha256(content).hexdigest()
    except (OSError, ValueError, TypeError) as exc:
        raise DataError(f'Cannot load generalized study settings: {exc}') from exc


def save_study_settings(directory, key, settings, expected_revision):
    current, _ = load_study_settings(directory)
    current[key] = settings
    try:
        content = json.dumps(dict(version=1, scenarios=current), indent=2, allow_nan=False)+'\n'
    except (ValueError, TypeError) as exc:
        raise DataError('Study settings contain invalid values.') from exc
    save_document(Path(directory)/SETTINGS_FILE, content, expected_revision)


def _records(frame):
    return frame.astype(object).where(pd.notna(frame), None).to_dict('records')


def _integer(value, label):
    try:
        if isinstance(value, bool) or float(value) != int(value):
            raise ValueError
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataError(f'{label} must be a whole number.') from exc


def render_generalized_studies(directory=None, *, positions=(), reporting_currency='EUR'):
    """`positions` supplies copied {id,name,contract,quantity,last_price} records."""
    st.subheader('Futures studies')
    st.caption('Hypothetical reserves and trades. Running a study never changes recorded holdings, cash or transactions. '
               'These scenarios use close-to-close observations and explicit assumptions, not broker liquidation rules.')
    context = sha256(str(directory).encode()).hexdigest()[:10]
    records = {str(p['id']): p for p in positions}
    choices = ['Manual contract', *records]
    selected = st.selectbox('Study position', choices,
        format_func=lambda key: records[key].get('name', key) if key in records else key,
        key=f'generalized_position_{context}')
    position = records.get(selected)
    selected_contract = ScenarioContract.from_spec(position['contract']) if position else None
    identity = selected if position else 'manual'
    try:
        saved_all, revision = load_study_settings(directory) if directory else ({}, None)
    except DataError as exc:
        st.error(str(exc))
        return
    base_key = f'{identity}:{reporting_currency}'
    names = ['Default', *(k[len(base_key)+1:] for k in saved_all if k.startswith(base_key+':'))]
    scenario_name = st.selectbox('Saved scenario', names, accept_new_options=True,
        key=f'generalized_scenario_{context}_{identity}_{reporting_currency}')
    key = base_key if scenario_name == 'Default' else base_key+':'+scenario_name
    saved = saved_all.get(key, {})
    prefix = f'generalized_{context}_{sha256(key.encode()).hexdigest()[:10]}_'
    st.session_state.setdefault(prefix+'draft_revision', revision)
    draft_revision = st.session_state[prefix+'draft_revision']
    if revision != draft_revision:
        st.warning('Saved study settings changed since this draft opened. Reload before saving; your draft is still visible.')
    if directory and st.button('Reload saved study settings', key=prefix+'reload'):
        for state_key in list(st.session_state):
            if state_key.startswith(prefix):
                del st.session_state[state_key]
        st.rerun()
    def number(label, name, default, **kwargs):
        return st.number_input(label, value=saved.get(name, default), key=prefix+name, **kwargs)
    def text_input(label, name, default):
        return st.text_input(label, value=saved.get(name, default), key=prefix+name)
    def table(label, name, defaults, *, columns=None):
        st.caption(label)
        imported = st.session_state.get(prefix+name+'_imported')
        values = imported if imported is not None else saved.get(name, defaults)
        generation = st.session_state.get(prefix+name+'_generation', 0)
        return st.data_editor(pd.DataFrame(values), num_rows='dynamic',
            hide_index=True, key=prefix+name+str(generation), column_config=columns or {})
    with st.expander('Contract and scenario', expanded=not bool(position)):
        if selected_contract:
            contract = selected_contract
            st.write(f'{contract.product} · {contract.expiry} · {contract.currency} · multiplier {contract.multiplier:g}')
        else:
            product = text_input('Product identifier', 'product', '')
            currency = st.selectbox('Contract currency', CURRENCIES,
                index=CURRENCIES.index(saved.get('currency', reporting_currency)), key=prefix+'currency')
            multiplier = number('Point multiplier', 'multiplier', 1., min_value=.000001)
            tick = number('Minimum tick', 'tick', .01, min_value=.000001)
            expiry = text_input('Contract last trading date', 'expiry', (date.today()+timedelta(days=90)).isoformat())
            convention = st.selectbox('Underlying return convention', ['price', 'net', 'gross', 'unknown'],
                index=['price', 'net', 'gross', 'unknown'].index(saved.get('convention', 'unknown')), key=prefix+'convention')
            contract = None
        quantity = number('Signed scenario contracts', 'quantity', int(position['quantity']) if position else 1, step=1)
        mark = number('Opening price in points', 'mark', float(position.get('last_price') or 1.) if position else 1., min_value=.000001)
        start = text_input('Scenario start date', 'start', date.today().isoformat())
        end = text_input('Scenario end date', 'end', selected_contract.expiry if selected_contract else expiry)
        mode = st.selectbox('Study mode', MODES, index=MODES.index(saved.get('mode', MODES[0])), key=prefix+'mode')
    with st.expander('Cash, collateral and currency', expanded=True):
        st.caption(f'All totals report in {reporting_currency}. FX is {reporting_currency} per currency unit. '
                   'These are separate scenario reserves, never additional assets in your portfolio. '
                   'Foreign collateral supports margin after a haircut but does not pay settlement.')
        pools = table('Cash by location and currency', 'pools', [
            dict(currency=c, bank=0., broker=0., fx=1., bank_credit=0., broker_credit=0., broker_debit=0.) for c in CURRENCIES])
        st.caption('Annual interest fields are decimals (0.03 = 3%). These are explicit constant assumptions; '
                   'EUR/USD accrue ACT/360 and GBP ACT/365. Forward FX defaults to the supplied constant rates.')
        collateral = table('Dedicated noncash collateral; exclude amounts already in cash above', 'collateral',
            [dict(currency=reporting_currency, value=0., haircut=.2)])
        initial_margin = number('Initial margin per contract, contract currency', 'initial_margin', 0., min_value=0.)
        maintenance_margin = number('Maintenance margin per contract, contract currency', 'maintenance_margin', 0., min_value=0.)
        haircut = number('Foreign cash haircut (%)', 'haircut', 10., min_value=0., max_value=100.)
        credit = number('Allowed settlement-currency debit', 'credit', 0., min_value=0.)
        scale_margin = st.checkbox('Scale margin with contract price', value=saved.get('scale_margin', False), key=prefix+'scale_margin')
    with st.expander('Funding policies, costs and manual payments'):
        st.caption('Policy target and trigger are settlement-currency cash amounts. Source order determines which '
                   'bank currency funds a transfer first. Currency conversion occurs at dispatch; arrival follows '
                   'the maintenance check. Below maintenance triggers a close; equality does not.')
        policies = table('Baseline and candidate policies; disable funding for a no-transfer baseline', 'policies', [
            dict(name='No transfers', enabled=False, target=0., trigger=0., sources='EUR,USD,GBP', delay=3,
                 conversion_cost=0., transfer_fee=0., reenter=False, reset='never', mode='target', proportional=False, withdraw_excess=False),
            dict(name='Candidate', enabled=True, target=0., trigger=0., sources='EUR,USD,GBP', delay=3,
                 conversion_cost=0., transfer_fee=0., reenter=True, reset='monthly', mode='target', proportional=False, withdraw_excess=False)])
        st.caption('Conversion cost is a decimal fraction; transfer fee is in contract currency. '
                   'Mode is target or buffer above maintenance. Proportional scales the target/buffer with price. '
                   'Optional monthly/quarterly excess withdrawals return settlement-currency cash to the bank with the same delay. '
                   'Add rows for parameter sweeps on identical paths.')
        fee = number('Fee per contract per side, contract currency', 'fee', 0., min_value=0.)
        slippage = number('Slippage per side in points', 'slippage', 0., min_value=0.)
        payments = table('Optional manually scheduled payments; no tax calculations', 'payments',
            [dict(date='', currency=reporting_currency, amount=0., kind='tax', pool='broker')])
    with st.expander('Paths, histories and roll calendar', expanded=True):
        annual_return = number('Annual underlying growth assumption (%)', 'annual_return', 0.)
        volatility = number('Annual underlying volatility (%)', 'volatility', 20., min_value=0., max_value=300.)
        carry = number('Effective carry after dividend/return-convention adjustment (%)', 'carry', 0.)
        day_count = st.selectbox('Carry day count', [360, 365], index=[360, 365].index(saved.get('day_count', 360)), key=prefix+'day_count')
        paths_count = number('Monte Carlo paths', 'paths_count', 100, min_value=1, max_value=5000, step=1)
        st.caption('Historical proxy reconstruction needs matching index identity, native currency and return convention. '
                   'Adjusted ETF returns must not silently stand in for price-index returns. Effective carry is an explicit '
                   'scenario assumption, not a broker financing charge. Monte Carlo uses a fixed reproducible seed.')
        rolls_table = table('Explicit rolling calendar; empty for one contract', 'rolls', [dict(date='', next_expiry='')])
        history_benchmark = text_input('Underlying index identity for this scenario', 'history_benchmark', '')
        ticker = text_input('Optional free proxy ticker', 'ticker', '')
        years = st.selectbox('Proxy history years', [1, 3, 5, 10],
            index=[1, 3, 5, 10].index(saved.get('history_years', 3)), key=prefix+'history_years')
        st.caption('Downloading a proxy does not establish that it tracks this contract. Confirm its index identity, '
                   'currency and return convention below. No fund or index is selected automatically.')
        if st.button('Load free proxy and dated FX', key=prefix+'load_history'):
            try:
                if not ticker.strip():
                    raise DataError('Enter an explicit public listing ticker.')
                rows, provenance = fetch_proxy_rows(ticker, years, reporting_currency, directory)
                st.session_state[prefix+'history_imported'] = rows
                st.session_state[prefix+'history_generation'] = st.session_state.get(prefix+'history_generation', 0)+1
                st.session_state[prefix+'download_provenance'] = provenance
                st.rerun()
            except (DataError, ValueError, OSError) as exc:
                st.error(str(exc))
        if provenance := st.session_state.get(prefix+'download_provenance'):
            st.info(provenance)
        history = table('Historical observations: price = exact old-contract settlement or identified proxy level; '
                        'roll_entry = observed new-contract entry for settlement replay. FX is dated and mandatory.', 'history',
            [dict(date='', price=0., EUR=1., USD=1., GBP=1., roll_entry=None, next_expiry='')])
        history_source = text_input('Historical source / index identifier', 'history_source', '')
        history_currency = st.selectbox('Historical series currency', CURRENCIES,
            index=CURRENCIES.index(saved.get('history_currency', selected_contract.currency if selected_contract else reporting_currency)), key=prefix+'history_currency')
        history_convention = st.selectbox('Historical series return convention', ['price', 'net', 'gross', 'unknown'],
            index=['price', 'net', 'gross', 'unknown'].index(saved.get('history_convention', 'unknown')), key=prefix+'history_convention')
        benchmark_currency = st.selectbox('Optional free bank-rate benchmark currency', CURRENCIES, key=prefix+'rate_currency')
        if st.button('Load free bank benchmark rates', key=prefix+'load_rates'):
            try:
                from portfolio_app.study_market import benchmark_history
                last = min(date.fromisoformat(end), date.today())
                first = min(date.fromisoformat(start), last-timedelta(days=30))
                observed = benchmark_history(benchmark_currency, first.isoformat(), last.isoformat())
                pool_rows = _records(pools)
                current = next(r for r in pool_rows if r['currency'] == benchmark_currency)
                rows = [dict(date=d.date().isoformat(), currency=benchmark_currency, bank_credit=float(rate),
                    broker_credit=float(current['broker_credit']), broker_debit=float(current['broker_debit']),
                    source=observed.source) for d, rate in observed.values.items()]
                st.session_state[prefix+'rate_schedule_imported'] = rows
                st.session_state[prefix+'rate_schedule_generation'] = st.session_state.get(prefix+'rate_schedule_generation', 0)+1
                st.rerun()
            except (DataError, ValueError, OSError, ImportError, StopIteration) as exc:
                st.error(str(exc))
        rate_schedule = table('Optional dated annual rate overrides; known only from their effective date', 'rate_schedule',
            [dict(date='', currency=reporting_currency, bank_credit=0., broker_credit=0., broker_debit=0., source='Manual')])
        st.caption('Downloaded benchmark rates apply only to bank yield; broker credit/debit assumptions remain explicit. '
                   'A prospective run holds the latest known input rate; historical overrides are never applied before their date.')
        cohort_window = number('Cohort observations', 'cohort_window', 253, min_value=2, step=1)
        cohort_step = number('Cohort start step', 'cohort_step', 21, min_value=1, step=1)
        gate = st.checkbox('Restrict cohorts using prior 20-return volatility', value=saved.get('gate', False), key=prefix+'gate')
        threshold = number('Prior volatility limit (%)', 'threshold', 15., min_value=0.)
        st.caption('Cohort dates are translated to the scenario start. FX percentage movements are rebased to opening FX. '
                   'Overlapping cohorts are dependent observations, not independent forecast probabilities.')
    config = dict(quantity=quantity, mark=mark, start=start, end=end, mode=mode, pools=_records(pools),
        collateral=_records(collateral), initial_margin=initial_margin, maintenance_margin=maintenance_margin,
        haircut=haircut, credit=credit, scale_margin=scale_margin, policies=_records(policies),
        fee=fee, slippage=slippage, payments=_records(payments), annual_return=annual_return,
        volatility=volatility, carry=carry, day_count=day_count, paths_count=paths_count,
        rolls=_records(rolls_table), history=_records(history), history_source=history_source,
        history_currency=history_currency, history_convention=history_convention,
        history_benchmark=history_benchmark, ticker=ticker, history_years=years,
        rate_schedule=_records(rate_schedule),
        cohort_window=cohort_window, cohort_step=cohort_step, gate=gate, threshold=threshold)
    if selected_contract:
        config['contract_snapshot'] = dict(product=selected_contract.product, currency=selected_contract.currency,
            multiplier=selected_contract.multiplier, expiry=selected_contract.expiry, tick=selected_contract.tick,
            return_convention=selected_contract.return_convention)
    if not selected_contract:
        config.update(product=product, currency=currency, multiplier=multiplier, tick=tick, expiry=expiry, convention=convention)
    digest = sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()
    if directory and st.button('Save study settings', key=prefix+'save'):
        try:
            save_study_settings(directory, key, config, draft_revision)
            st.session_state[prefix+'draft_revision'] = load_study_settings(directory)[1]
            st.success('Scenario settings saved. Actual portfolio records are unchanged.')
        except (DataError, OSError) as exc:
            st.error(str(exc))
    if st.button('Run study', type='primary', key=prefix+'run'):
        try:
            if not contract:
                contract = ScenarioContract(product, currency, multiplier, expiry, tick, convention)
            scenario, path_source, policy_map = prepare_study(config, contract, reporting_currency)
            with st.spinner('Running identical paths for every policy…'):
                result = compare_policies(scenario, path_source, policy_map)
            st.session_state[prefix+'result'] = (digest, result)
        except (DataError, ValueError, TypeError, KeyError, OverflowError) as exc:
            st.error(str(exc))
    cached = st.session_state.get(prefix+'result')
    if cached:
        if cached[0] != digest:
            st.info('Inputs changed. Run the study to refresh the results.')
            return
        summary, runs, examples = cached[1]
        st.caption(f'Amounts in {reporting_currency}; frequencies are fractions. Every candidate uses identical paths and starting reserves. '
                   'Cash and always-long index alternatives use the same starting capital without futures fees/taxes; '
                   'the index benchmark is unavailable for settlement-only histories.')
        st.dataframe(summary, hide_index=True)
        st.dataframe(runs.groupby('policy', sort=False)[['cash_benchmark', 'index_benchmark', 'market_pnl', 'fx_pnl',
            'bank_interest', 'broker_interest', 'fees', 'taxes', 'transfer_costs']].median())
        chosen = st.selectbox('Inspect first path', list(examples), key=prefix+'inspect')
        example = examples[chosen]
        st.line_chart(example.events.set_index('date')[['wealth', 'bank', 'broker', 'transit']])
        for issue in example.issues:
            st.warning(issue)
        st.caption(f'Cash reconciliation error: {example.reconciliation_error:.8f} {reporting_currency}. '
                   'Notional and eligible collateral columns use contract currency; currency-labelled cash columns use their native currencies.')
        st.dataframe(example.events, hide_index=True)


def prepare_study(config, contract, reporting_currency):
    """Validate UI values separately from Streamlit, without market or ledger I/O."""
    pools = config['pools']
    fx = {r['currency']: float(r['fx']) for r in pools}
    if fx.get(reporting_currency) != 1.:
        raise DataError('Reporting-currency FX must equal one.')
    scenario = ScenarioInput(contract, _integer(config['quantity'], 'Contracts'), float(config['mark']), reporting_currency,
        bank=tuple(CashPool(r['currency'], float(r['bank'])) for r in pools),
        broker=tuple(CashPool(r['currency'], float(r['broker'])) for r in pools),
        collateral=tuple(Collateral(r['currency'], float(r['value']), float(r['haircut'])) for r in config['collateral']),
        initial_margin=float(config['initial_margin']), maintenance_margin=float(config['maintenance_margin']),
        fee_per_side=float(config['fee']), slippage_points=float(config['slippage']),
        foreign_cash_haircut=float(config['haircut'])/100, settlement_credit_limit=float(config['credit']),
        rates=tuple(InterestRate(r['currency'], float(r['bank_credit']), float(r['broker_credit']),
            float(r['broker_debit']), day_count=365 if r['currency'] == 'GBP' else 360) for r in pools)
            + tuple(InterestRate(r['currency'], float(r['bank_credit']), float(r['broker_credit']),
                float(r['broker_debit']), r['date'], 365 if r['currency'] == 'GBP' else 360)
                for r in config.get('rate_schedule', ()) if r.get('date')),
        payments=tuple(ScheduledPayment(r['date'], r['currency'], float(r['amount']), r['kind'], r['pool'])
                       for r in config['payments'] if r.get('date')),
        scale_margin=bool(config['scale_margin']))
    policies = {}
    for row in config['policies']:
        name = str(row['name']).strip()
        if not name or name in policies:
            raise DataError('Policy names must be nonempty and unique.')
        policies[name] = (StudyPolicy(float(row['target']), float(row['trigger']),
            tuple(c.strip().upper() for c in row['sources'].split(',')), _integer(row['delay'], 'Transfer delay'),
            float(row['conversion_cost']), float(row['transfer_fee']), bool(row['reenter']), row['reset'])
            if row['enabled'] else None)
        if policies[name] is not None:
            from dataclasses import replace
            policies[name] = replace(policies[name], mode=row.get('mode', 'target'),
                proportional=bool(row.get('proportional', False)), withdraw_excess=bool(row.get('withdraw_excess', False)))
    rolls = tuple(Roll(r['date'], r['next_expiry']) for r in config['rolls'] if r.get('date'))
    common = dict(net_carry=float(config['carry'])/100, day_count=int(config['day_count']), rolls=rolls)
    mode = config['mode']
    if mode in MODES[:2]:
        dates = pd.bdate_range(config['start'], config['end']).union(pd.DatetimeIndex([config['start'], config['end']])).sort_values()
        if mode == MODES[0] and rolls:
            raise DataError('Choose Rolling Monte Carlo to use a roll schedule.')
        if mode == MODES[1] and not rolls:
            raise DataError('Rolling Monte Carlo needs an explicit roll calendar.')
        if int(config['paths_count'])*len(dates)*len(policies) > 2_000_000:
            raise DataError('Reduce paths, policies or horizon to at most two million observation-policy steps.')
        paths = forward_paths(scenario, dates, {d.date().isoformat(): fx for d in dates},
            paths=int(config['paths_count']), annual_return=float(config['annual_return'])/100,
            volatility=float(config['volatility'])/100, **common)
    else:
        if not config['history_source'].strip():
            raise DataError('Identify the historical source; no history is generated automatically.')
        rows = [r for r in config['history'] if r.get('date')]
        if len(rows) < 2:
            raise DataError('Enter at least two dated historical observations.')
        if any(date.fromisoformat(r['date']) > date.today() for r in rows):
            raise DataError('Historical observations cannot be in the future.')
        if config['history_currency'] != contract.currency:
            raise DataError('Historical prices must be in contract currency.')
        history_fx = {r['date']: {c: float(r[c]) for c in CURRENCIES} for r in rows}
        if mode == 'Historical settlements':
            paths = [tuple(Observation(r['date'], float(r['price']), history_fx[r['date']],
                float(r['roll_entry']) if r.get('roll_entry') is not None else None,
                r.get('next_expiry') or '', config['history_source']) for r in rows)]
        else:
            proxy = pd.Series([float(r['price']) for r in rows], index=pd.to_datetime([r['date'] for r in rows]))
            if not config.get('history_benchmark', '').strip():
                raise DataError('Identify the underlying index before using a proxy.')
            # Shared adapter validates metadata and protects against substitution.
            from portfolio_app.study_market import QualifiedHistory, validate_history_for_contract
            from portfolio_app.derivative_market import CATALOG
            qualified = QualifiedHistory(proxy, config['history_benchmark'], config['history_currency'],
                config['history_convention'], config['history_source'], 'Explicit study proxy; not actual futures fills.')
            product = CATALOG.get(contract.product)
            validate_history_for_contract(qualified, contract,
                benchmark=product.underlying if product else config['history_benchmark'])
            qualifications = dict(proxy_currency=config['history_currency'], proxy_convention=config['history_convention'])
            if contract.return_convention == 'unknown':
                raise DataError('Identify the contract return convention before proxy reconstruction.')
            if mode == 'Historical proxy reconstruction':
                paths = [reconstruct(scenario, proxy, history_fx, source=config['history_source']+' · approximate reconstruction', **common, **qualifications)]
            elif mode == 'Funding policy cohorts':
                paths = funding_cohorts(scenario, proxy, history_fx, start=config['start'], opening_fx=fx,
                    window=int(config['cohort_window']), step=int(config['cohort_step']),
                    low_volatility=float(config['threshold'])/100 if config['gate'] else None, **common, **qualifications)
            else:
                raise DataError('Unsupported study mode.')
    return scenario, paths, policies


def fetch_proxy_rows(ticker, years, reporting_currency, directory=None, *, service=None):
    """Free adjusted history plus exact-date FX; never infer instrument semantics."""
    if service is None:
        from portfolio_app.risk_data import RiskHistoryService, YahooRiskHistoryProvider
        cache = Path(directory)/'.cache'/'generalized-study-history' if directory else None
        service = RiskHistoryService(YahooRiskHistoryProvider(), cache)
    observed = service.get(ticker, years, refresh=True)
    if observed.prices.empty or observed.status not in ('fresh', 'cached') or observed.currency not in CURRENCIES:
        raise DataError('Fresh supported-currency proxy history is unavailable. Enter identified manual observations.')
    fx = {}
    for currency in CURRENCIES:
        if currency == reporting_currency:
            fx[currency] = pd.Series(1., index=observed.prices.index)
        else:
            rates = service.cash(currency, reporting_currency, years, refresh=True)
            if rates.status not in ('fresh', 'cached') or rates.currency != reporting_currency:
                raise DataError('Dated FX is unavailable. Existing observations are retained; manual entry remains available.')
            fx[currency] = rates.prices.reindex(observed.prices.index)
    frame = pd.DataFrame(dict(price=observed.prices, **fx))
    if frame.isna().any().any():
        raise DataError('Proxy and FX dates do not fully align. Supply exact dated observations; no forward fill is applied.')
    rows = [dict(date=stamp.date().isoformat(), **row, roll_entry=None, next_expiry='') for stamp, row in frame.to_dict('index').items()]
    return rows, (f'{ticker}: adjusted history in {observed.currency}; source Yahoo Finance. '
                  'FX supplied on exact price dates. Fund costs and tracking differences remain in the proxy. '
                  'Enter this source and confirm identity/currency/return convention before running.')
