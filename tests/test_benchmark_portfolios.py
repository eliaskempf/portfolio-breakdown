from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from portfolio_app.funds.etf import load_funds, matching_fund
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.performance.history_observations import load_observations
from portfolio_app.portfolio.positions import read_snapshot


@pytest.mark.parametrize('profile,positions,etfs,components', [('small', 12, 0, 0), ('etf-heavy', 48, 24, 16400)])
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
    assert len(fund_positions) == len(funds) == etfs
    assert {matching_fund(p, funds).fund_id for p in fund_positions.to_dict('records')} == {f.fund_id for f in funds}
    assert sum(len(f.constituents) for f in funds) == components
    if funds:
        shared = set.intersection(*(set(f.constituents['isin']) for f in funds))
        stocks = set(holdings.loc[holdings.instrument_type.eq('equity'), 'isin'])
        assert len(stocks) == 24 and stocks <= shared
        assert sum(f.constituents.weight.sum() < .99 for f in funds) == 8
    journal, _ = load_journal(workspace)
    observations, _ = load_observations(workspace)
    assert all(sum(event.kind == 'buy' for event in book.records) == 25 for book in journal.securities)
    assert len(journal.securities) == positions
    assert len(observations.observations) == 731 * positions
