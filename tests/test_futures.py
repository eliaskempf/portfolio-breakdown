"""Invented portfolios only; no working portfolio data is read."""
from dataclasses import replace
import json
import sys

import pandas as pd
import pytest

from portfolio_app.app import main
from portfolio_app.demo import create_demo_data
from portfolio_app.futures import FuturePosition, futures_scenarios, summarize_future
from portfolio_app.futures_workspace import SANDBOX_FILE, fork_futures_workspace, load_sandbox, save_sandbox
from portfolio_app.holdings import DataError


def invented_holdings():
    return pd.DataFrame({'name': ['Synthetic investment', 'Synthetic cash'], 'shares': [1., 1.],
                         'current_value_reporting': [80000., 20000.], 'reporting_currency': ['EUR', 'EUR'], 'instrument_type': ['equity', 'cash']})


def test_futures_notional_is_not_wealth_and_collateral_is_not_a_cost():
    position = FuturePosition(contracts=2, entry_price=500, mark_price=525, collateral_budget=15000)
    summary = summarize_future(invented_holdings(), position)
    assert position.notional == 105000
    assert position.pnl == 5000
    assert position.margin == 10000
    assert summary.base_value == 100000
    assert summary.equity == 105000
    assert summary.gross_multiple == pytest.approx(185000 / 105000)
    assert summary.beta == pytest.approx(205000 / 105000)
    assert summarize_future(invented_holdings(), replace(position, collateral_budget=0)) == summary


def test_short_future_increases_gross_but_reduces_net_and_beta():
    summary = summarize_future(invented_holdings(), FuturePosition(contracts=-1, base_beta=.8))
    assert summary.gross_multiple == 1.3
    assert summary.net_multiple == .3
    assert summary.beta == .3
    assert summary.equity == 100000


def test_cash_backed_future_and_zero_contracts():
    cash = invented_holdings().iloc[[1]].copy()
    cash['current_value_reporting'] = 50000
    summary = summarize_future(cash, FuturePosition(base_beta=0))
    assert summary.gross_multiple == summary.net_multiple == summary.beta == 1
    removed = summarize_future(cash, FuturePosition(contracts=0, entry_price=450, mark_price=550, base_beta=0))
    assert removed.pnl == removed.notional == removed.gross_multiple == removed.beta == 0
    assert removed.equity == 50000


def test_scenarios_distinguish_cumulative_and_incremental_pnl_and_equity():
    position = FuturePosition(mark_price=550, collateral_budget=5000)
    held = invented_holdings()
    before = held.copy(deep=True)
    summary = summarize_future(held, position)
    row = futures_scenarios(summary, position, [-.1]).iloc[0]
    assert row['Futures price (points)'] == 495
    assert row['Additional futures P&L (EUR)'] == -5500
    assert row['Cumulative futures P&L (EUR)'] == -500
    assert row['Without futures (EUR)'] == 100000
    assert row['With futures (EUR)'] == 99500
    assert row['Change from current equity (%)'] == pytest.approx(-5500 / 105000 * 100)
    assert row['Illustrative collateral shortfall (EUR)'] == 500
    pd.testing.assert_frame_equal(before, held)
    assert futures_scenarios(summary, position, [0]).iloc[0]['With futures (EUR)'] == summary.equity


def test_global_market_scenario_applies_betas_and_short_direction():
    position = FuturePosition(contracts=-1, base_beta=1.2, futures_beta=.8)
    row = futures_scenarios(summarize_future(invented_holdings(), position), position, [-.1], move_base=True).iloc[0]
    assert row['Without futures (EUR)'] == 88000
    assert row['Additional futures P&L (EUR)'] == pytest.approx(4000)
    assert row['With futures (EUR)'] == 92000


def test_missing_valuation_never_creates_complete_ratios():
    held = invented_holdings()
    held.loc[0, 'current_value_reporting'] = float('nan')
    position = FuturePosition(mark_price=525)
    summary = summarize_future(held, position)
    assert summary.missing_prices == 1
    assert summary.base_value is summary.equity is summary.beta is summary.gross_multiple is None
    assert summary.pnl == 2500
    row = futures_scenarios(summary, position, [-.1]).iloc[0]
    assert pd.isna(row['With futures (EUR)'])
    assert row['Additional futures P&L (EUR)'] == -5250
    held.loc[0, 'shares'] = 0
    assert summarize_future(held, position).missing_prices == 0


def test_nonpositive_equity_has_no_leverage_or_beta_ratios():
    position = FuturePosition(contracts=3, entry_price=500, mark_price=100)
    summary = summarize_future(invented_holdings(), position)
    assert summary.equity == -20000
    assert summary.beta is summary.gross_multiple is summary.net_multiple is None
    assert pd.isna(futures_scenarios(summary, position, [0]).iloc[0]['Change from current equity (%)'])


@pytest.mark.parametrize('fields', [dict(contracts=1.5), dict(contracts=True), dict(entry_price=0),
    dict(mark_price=float('nan')), dict(margin_per_contract=-1), dict(collateral_budget=-1), dict(base_beta=float('inf'))])
def test_invalid_position(fields):
    with pytest.raises(DataError):
        FuturePosition(**fields)


def test_fork_is_independent_preserves_inputs_and_rejects_overwrites(tmp_path):
    source = create_demo_data(tmp_path / 'synthetic-source')
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    destination = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    for name, content in before.items():
        assert (destination / name).read_bytes() == content
        assert (destination / name).stat().st_ino != (source / name).stat().st_ino
    position, revision = load_sandbox(destination)
    changed = replace(position, mark_price=525)
    save_sandbox(destination, changed, revision)
    assert load_sandbox(destination)[0] == changed
    with pytest.raises(DataError, match='changed'):
        save_sandbox(destination, position, revision)
    with pytest.raises(DataError, match='already exists'):
        fork_futures_workspace(source, destination)
    assert {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()} == before
    assert not (source / SANDBOX_FILE).exists()


def test_fork_rejects_nested_paths_links_and_external_etfs(tmp_path):
    source = create_demo_data(tmp_path / 'synthetic-source')
    with pytest.raises(DataError, match='separate'):
        fork_futures_workspace(source, source / 'nested')
    (source / 'linked').symlink_to(source / 'holdings.csv')
    with pytest.raises(DataError, match='symbolic'):
        fork_futures_workspace(source, tmp_path / 'copy')
    (source / 'linked').unlink()
    (source / 'etfs' / 'external.yaml').write_text('holdings_file: ../../external.csv\n')
    with pytest.raises(DataError, match='inside the workspace'):
        fork_futures_workspace(source, tmp_path / 'copy')
    assert not (tmp_path / 'copy').exists()


def test_missing_or_invalid_sandbox_marker(tmp_path):
    assert load_sandbox(tmp_path) is None
    (tmp_path / SANDBOX_FILE).write_text(json.dumps({'version': 2}))
    with pytest.raises(DataError, match='Cannot load'):
        load_sandbox(tmp_path)


def test_cli_forks_then_launches_destination(tmp_path, monkeypatch):
    source = create_demo_data(tmp_path / 'synthetic-source')
    destination = tmp_path / 'synthetic-sandbox'
    def run(command, directory, **kwargs):
        assert command[command.index('--data-dir') + 1] == str(destination)
        assert load_sandbox(destination)[0].contracts == 1
        assert '--server.port=8599' in command
        return 0
    monkeypatch.setattr(sys, 'argv', ['portfolio-app', '--data-dir', str(source), '--fork-futures', str(destination), '--server.port=8599'])
    monkeypatch.setattr('portfolio_app.app.run_server', run)
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    assert not (source / SANDBOX_FILE).exists()
