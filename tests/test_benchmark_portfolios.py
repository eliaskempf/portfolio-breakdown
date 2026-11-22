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
    fixture.create_workspace(workspace, profile)
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
    if profile == 'mixed-history':
        from portfolio_app.analytics.analytics_cache import AnalyticsCache
        from portfolio_app.analytics.risk import DEFAULT_BENCHMARK
        listings = fixture.prepare_risk_check(workspace)
        assert listings == [DEFAULT_BENCHMARK + ':EUR', *holdings.ticker]
        assert read_snapshot(workspace / 'holdings.csv').holdings.manual_price.isna().all()
        cache = AnalyticsCache(workspace / '.cache' / 'risk-history')
        missing = [ticker for ticker in listings if cache.cached(f'adjusted-daily-v1:{ticker}:3')[1]]
        assert missing == [listings[-1]]
