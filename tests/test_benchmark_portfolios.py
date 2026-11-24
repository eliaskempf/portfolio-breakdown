from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from portfolio_app.funds.etf import load_funds, matching_fund
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.performance.history_observations import load_observations
from portfolio_app.portfolio.positions import read_snapshot


@pytest.mark.parametrize('profile,positions,etfs,components', [('small', 12, 0, 0), ('etf-heavy', 48, 24, 16400),
    ('mixed-history', 48, 24, 16400), ('multi-account', 120, 24, 16400)])
def test_navigation_benchmark_portfolio(tmp_path, profile, positions, etfs, components):
    path = Path(__file__).resolve().parents[1] / 'tools/perf_benchmark/fixture.py'
    spec = spec_from_file_location('benchmark_fixture', path)
    fixture = module_from_spec(spec)
    spec.loader.exec_module(fixture)
    workspace = tmp_path / 'synthetic'
    checklist = profile == 'mixed-history'
    fixture.create_workspace(workspace, profile, checklist=checklist)
    holdings = read_snapshot(workspace / 'holdings.csv').holdings
    funds = load_funds(workspace / 'etfs')
    assert len(holdings) == positions
    fund_positions = holdings.loc[holdings.instrument_type.eq('etf')]
    assert len(funds) == etfs
    if profile == 'multi-account':
        assert fund_positions.groupby('id').account.nunique().tolist() == [4] * etfs
        assert holdings.position_id.is_unique and len(holdings.id.unique()) == 48
    else:
        assert len(fund_positions) == etfs
    assert {matching_fund(p, funds).fund_id for p in fund_positions.to_dict('records')} == {f.fund_id for f in funds}
    assert sum(len(f.constituents) for f in funds) == components
    if funds:
        shared = set.intersection(*(set(f.constituents['isin']) for f in funds))
        stocks = set(holdings.loc[holdings.instrument_type.eq('equity'), 'isin'])
        assert len(stocks) == 24 and stocks <= shared
        assert sum(f.constituents.weight.sum() < .99 for f in funds) == 8
    if profile in {'mixed-history', 'multi-account'}:
        assert {sector for f in funds for sector in f.constituents.sector.unique()} == {f'Invented sector {i}' for i in range(24)}
        from portfolio_app.exposure.company_merges import build_plan, MergeSettings
        from portfolio_app.monitoring.monitoring_store import load_monitoring
        plan = build_plan(holdings, funds, {}, MergeSettings(frozenset()))
        assert any(g.enabled and g.basis == 'Estimated name match' for g in plan.groups)
        doc, _ = load_monitoring(workspace)
        assert len(doc['observations']) == 4000 and not doc['rules']
    journal, _ = load_journal(workspace)
    observations, _ = load_observations(workspace)
    assert all(sum(event.kind == 'buy' for event in book.records) == 25 for book in journal.securities)
    unchecked = [book.position_id for book in journal.securities for event in book.records
                 if event.kind == 'buy' and not event.charges_known]
    assert unchecked == (holdings.position_id.tolist()[::4] if checklist else [])
    assert len(journal.securities) == positions
    assert len(observations.observations) == 731 * positions
    if profile == 'multi-account':
        # Each account holds its own lots at the same underlying listing price.
        same_fund = set(fund_positions.loc[fund_positions.id == 'fund-0', 'position_id'])
        closes = {position: [] for position in same_fund}
        for obs in observations.observations:
            if obs.subject in closes:
                closes[obs.subject].append((obs.date, obs.amount))
        assert all(len(series) == 731 for series in closes.values())
        assert len({tuple(sorted(series)) for series in closes.values()}) == 1
    if profile in {'mixed-history', 'multi-account'}:
        from portfolio_app.analytics.analytics_cache import AnalyticsCache
        from portfolio_app.analytics.risk import DEFAULT_BENCHMARK
        cold = profile == 'multi-account'
        listings = fixture.prepare_risk_check(workspace, scenario='risk-cold' if cold else 'risk')
        assert listings == [DEFAULT_BENCHMARK + ':EUR', *dict.fromkeys(holdings.ticker)]
        assert read_snapshot(workspace / 'holdings.csv').holdings.manual_price.isna().all()
        cache = AnalyticsCache(workspace / '.cache' / 'risk-history')
        missing = [ticker for ticker in listings if cache.cached(f'adjusted-daily-v1:{ticker}:3')[1]]
        assert missing == (listings if cold else [listings[-1]])


def test_large_risk_matrix_uses_distinct_listings_even_when_names_match(tmp_path):
    from datetime import date
    path = Path(__file__).resolve().parents[1] / 'tools/perf_benchmark/fixture.py'
    spec = spec_from_file_location('benchmark_fixture', path)
    fixture = module_from_spec(spec)
    spec.loader.exec_module(fixture)
    rows = list(fixture.holdings_and_funds(tmp_path / 'etfs', 'mixed-history', date(2026, 5, 31),
                                          large_risk_matrix=True))
    assert len(rows) == len({row[0] for row in rows}) == len({row[2] for row in rows}) == 120
    assert sum(row[4] == 'etf' for row in rows) == 24
    assert rows[0][1] == rows[1][1] and len(rows[0][1]) > 28


def test_purchase_fx_benchmark_needs_dated_rates_and_has_partial_closes(tmp_path):
    import json
    from datetime import date
    path = Path(__file__).resolve().parents[1] / 'tools/perf_benchmark/fixture.py'
    spec = spec_from_file_location('benchmark_fixture', path)
    fixture = module_from_spec(spec)
    spec.loader.exec_module(fixture)
    workspace = tmp_path / 'synthetic'
    fixture.create_workspace(workspace, 'mixed-history', today=date(2026, 5, 31), purchase_fx=True)
    expected = json.loads((tmp_path / 'purchase-fx.json').read_bytes())
    journal, _ = load_journal(workspace)
    store, _ = load_observations(workspace)
    assert len(expected['days']) == 25
    assert (expected['positions'], expected['basis'], expected['closing']) == (48, 108000, 160200)
    for book in journal.securities:
        buys = [event for event in book.records if event.kind == 'buy']
        assert [(event.date, event.currency) for event in buys] == [(day, 'USD') for day in expected['days']]
    assert all(obs.kind == 'price' and obs.currency == 'EUR' for obs in store.observations)
    from collections import Counter
    counts = Counter(obs.date for obs in store.observations)
    assert Counter(counts.values()) == {42: 22, 48: 709}
    assert counts[expected['end']] == 48
    assert json.loads((workspace / '.cache/prices.json').read_bytes())['fx:USD']['quote']['price'] == .9


@pytest.mark.parametrize('long_history, purchases, deposit_count, cadence', [(False, 25, 730, 1), (True, 122, 121, 30)])
def test_cash_history_fixture_funds_each_account(tmp_path, long_history, purchases, deposit_count, cadence):
    import json
    from collections import Counter
    from datetime import date, timedelta
    path = Path(__file__).resolve().parents[1] / 'tools/perf_benchmark/fixture.py'
    spec = spec_from_file_location('benchmark_fixture', path)
    fixture = module_from_spec(spec)
    spec.loader.exec_module(fixture)
    workspace = tmp_path / 'synthetic'
    fixture.create_workspace(workspace, today=date(2026, 5, 31), cash_history=True, long_history=long_history)
    expected = json.loads((tmp_path / 'cash-history.json').read_bytes())
    journal, _ = load_journal(workspace)
    assert (expected['accounts'], expected['positions'], expected['purchases']) == (4, 12, purchases)
    openings = [e for e in journal.events if e.kind == 'cash_open']
    assert {e.account_id for e in openings} == {a.id for a in journal.accounts}
    assert all(e.date == expected['start'] and e.amount == 100 for e in openings)
    moves = [e for e in journal.events if e.kind == 'cash_flow']
    assert len(moves) == len({(e.account_id, e.date) for e in moves}) == 4 * deposit_count
    assert all(e.currency == 'EUR' and e.amount == 1 for e in moves)
    assert set(Counter(e.date for e in moves).values()) == {4}
    start = date.fromisoformat(expected['start'])
    assert {e.date for e in moves} == {(start + timedelta(days=cadence*i)).isoformat() for i in range(1, deposit_count+1)}
    for index, book in enumerate(journal.securities):
        buys = [e for e in book.records if e.kind == 'buy']
        assert len(buys) == purchases and buys[0].date == expected['start']
        stagger = index % 12 if long_history else 0
        assert buys[-1].date == (start + timedelta(days=(purchases-1)*30 + stagger)).isoformat()

    if long_history:
        from decimal import Decimal
        store, _ = load_observations(workspace)
        first_quotes = {o.subject: o.currency for o in store.observations if o.kind == 'price' and o.date == expected['start']}
        currencies = [first_quotes[b.position_id] for b in journal.securities]
        assert currencies == expected['quote_currencies']
        assert Counter(currencies) == {'EUR': 4, 'USD': 4, 'GBP': 4}
        assert all(set(currencies[category::3]) == {'EUR', 'USD', 'GBP'} for category in range(3))
        for offset, usd, inverse_gbp in [(0, '.8', '.8'), (29, '.8', '.8'), (30, '.9', '.75')]:
            stamp = (start + timedelta(days=offset)).isoformat()
            assert {(o.currency, o.target): o.amount for o in store.index.fx[stamp]} == {
                ('USD', 'EUR'): Decimal(usd), ('EUR', 'GBP'): Decimal(inverse_gbp)}


def test_market_chart_benchmark_starts_with_durable_price_and_fx_ranges(tmp_path):
    import csv
    from portfolio_app.market.history import HistoryService, convert_price_history
    path = Path(__file__).resolve().parents[1] / 'tools/perf_benchmark/fixture.py'
    spec = spec_from_file_location('benchmark_fixture', path)
    fixture = module_from_spec(spec)
    spec.loader.exec_module(fixture)
    workspace = tmp_path / 'synthetic'
    workspace.mkdir()
    columns = ['position_key', 'instrument_type', 'ticker', 'manual_price', 'manual_price_currency', 'manual_price_date']
    with (workspace / 'holdings.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerow(dict(position_key='invented', instrument_type='equity', ticker='INVENTED', manual_price=100))
    settings = fixture.prepare_market_chart(workspace)
    assert settings['position'] == 'invented' and settings['ranges'] == {'1Y': 252, '5Y': 1260, 'Max': 7560}
    reader = HistoryService(None, workspace / '.cache' / 'history')
    for period, size in settings['ranges'].items():
        stock, due = reader.cached('INVENTED', period)
        fx, fx_due = reader.cached('USDEUR=X', period)
        assert not due and not fx_due
        chart = convert_price_history(stock, 'EUR', {'USD': fx})
        total = settings['ranges']['Max']
        indices = [i for i in range(total-size, total) if i not in settings['missing_fx']]
        assert len(chart.prices) == len(indices) < size and chart.dates[-1] == settings['end'] + 'T00:00:00'
        assert chart.prices == tuple((100 + i * .0001) * (.8 + i * .0001) for i in indices)
    assert {stamp[-6:] for stamp in stock.dates} == {'+01:00', '+02:00'}
