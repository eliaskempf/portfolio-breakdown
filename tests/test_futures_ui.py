"""Offline UI smoke tests with explicitly synthetic portfolio copies."""
import pytest
from streamlit.testing.v1 import AppTest

from synthetic_sample import create_sample_data
from portfolio_app.futures_workspace import fork_futures_workspace, load_sandbox
from test_ui import activate, by_label


def open_analysis(app, mode):
    view = 'Stress & history' if mode == 'Price scenarios' else 'Investment outcomes'
    by_label(app.get('button_group'), 'Futures analysis').set_value(view).run()
    label = 'Stress analysis' if mode == 'Price scenarios' else 'Investment analysis'
    by_label(app.radio, label).set_value(mode).run()
    return app


def launch(directory, *, with_history=True, open_futures=True):
    from datetime import date
    from test_futures_returns import write_synthetic_return_history
    from portfolio_app.futures_returns import RETURN_FILE
    from portfolio_app.futures_reference import REFERENCE_FILE
    import json
    sandbox = load_sandbox(directory)
    if sandbox:
        as_of = sandbox[0].quote_date or date.today().isoformat()
        if not (directory/REFERENCE_FILE).exists():
            initial = sandbox[0].margin_per_contract
            maintenance = sandbox[0].maintenance_per_contract if sandbox[0].maintenance_per_contract is not None else initial
            (directory/REFERENCE_FILE).write_text(json.dumps(dict(estr_rate=.0125, estr_reference_date=as_of,
                long_initial=initial, long_maintenance=maintenance, short_initial=initial, short_maintenance=maintenance,
                fetched_at=as_of+'T12:00:00+00:00')))
        if with_history and not (directory/RETURN_FILE).exists():
            write_synthetic_return_history(directory, as_of)

    app = AppTest.from_string(
        'from pathlib import Path\n'
        'from portfolio_app.ui import render_app\n'
        f'render_app(Path({str(directory)!r}), demo=True, ignore_selection=True)\n', default_timeout=20,
    ).run()
    if sandbox and open_futures:
        activate(app, 'Futures')
    return app


def test_sandbox_starts_on_overview_and_preserves_navigation(tmp_path):
    source = create_sample_data(tmp_path / 'synthetic-source')
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    app = launch(sandbox, open_futures=False)
    assert not app.exception
    assert app.session_state['main_tabs'] == 'Overview'
    assert by_label(app.get('button_group'), 'Allocation measure').value == 'Market exposure'
    activate(app, 'Futures')
    app.run()
    assert app.session_state['main_tabs'] == 'Futures'
    assert launch(sandbox, open_futures=False).session_state['main_tabs'] == 'Overview'


def test_simulation_date_draft_survives_navigation(tmp_path):
    from datetime import date
    source = create_sample_data(tmp_path / 'synthetic-source')
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    app = launch(sandbox)
    chosen = date(2024, 1, 2)
    by_label(app.date_input, 'Simulation start date').set_value(chosen).run()
    activate(app, 'Overview')
    activate(app, 'Futures')
    assert not app.exception
    assert by_label(app.date_input, 'Simulation start date').value == chosen


def test_edit_save_restart_and_shock_without_touching_source(tmp_path):
    source = create_sample_data(tmp_path / 'synthetic-source')
    # Drop the explicitly unpriced demo row to exercise complete equity metrics.
    path = source / 'holdings.csv'
    path.write_text('\n'.join(line for line in path.read_text().splitlines() if not line.startswith('unpriced,')) + '\n')
    original = path.read_bytes()
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    app = launch(sandbox)
    assert not app.exception
    assert app.session_state['main_tabs'] == 'Futures'
    assert by_label(app.get('button_group'), 'Futures analysis').value == 'Funding policies'
    assert by_label(app.metric, 'Net equity').value == '€744.00'
    by_label(app.number_input, 'Current mock price (points)').set_value(501.).run()
    assert by_label(app.metric, 'Cumulative futures P&L').value == '€100.00'
    assert by_label(app.metric, 'Net equity').value == '€844.00'
    by_label(app.button, 'Save mock position').click().run()
    assert not app.exception
    assert load_sandbox(sandbox)[0].mark_price == 501.
    open_analysis(app, 'Price scenarios')
    by_label(app.slider, 'Market move from current price (%)').set_value(-10.).run()
    assert by_label(app.metric, 'Additional futures gain / loss').value == '€-5,010.00'
    by_label(app.radio, 'Scenario').set_value('Global market; both respond to assumed beta').run()
    assert by_label(app.metric, 'Scenario net equity').value == '€-4,240.40'
    activate(app, 'Overview')
    assert not app.exception
    assert by_label(app.metric, 'Portfolio value').value == '€844.00'
    activate(app, 'Futures')
    assert by_label(app.slider, 'Market move from current price (%)').value == -10.
    restarted = launch(sandbox)
    open_analysis(restarted, 'Collateral & savings')
    assert not restarted.exception
    assert by_label(restarted.number_input, 'Current mock price (points)').value == 501.
    assert path.read_bytes() == original
    assert (sandbox / 'holdings.csv').read_bytes() == original


def test_missing_valuation_still_allows_futures_pnl_and_ordinary_workspace_has_no_tab(tmp_path):
    source = create_sample_data(tmp_path / 'synthetic-source')
    app = launch(source)
    assert not app.exception
    assert 'Futures' not in [tab.label for tab in app.tabs]
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    app = launch(sandbox)
    open_analysis(app, 'Collateral & savings')
    assert not app.exception
    assert by_label(app.metric, 'Net equity').value == '—'
    assert any('missing valuations' in item.value for item in app.warning)
    by_label(app.number_input, 'Signed contracts').set_value(-1).run()
    open_analysis(app, 'Price scenarios')
    by_label(app.slider, 'Market move from current price (%)').set_value(-10.).run()
    assert not app.exception
    assert by_label(app.metric, 'Additional futures gain / loss').value == '€5,000.00'


def test_comparison_uses_reference_direction_and_saves_inputs(tmp_path):
    from portfolio_app.futures_reference import refresh_references
    from test_futures_comparison import synthetic_reference_html
    from portfolio_app.futures_workspace import load_comparison
    source = create_sample_data(tmp_path / 'synthetic-source')
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    ecb, ibkr = synthetic_reference_html()
    refresh_references(sandbox, fetch=lambda url: ecb if 'ecb.europa' in url else ibkr)
    app = launch(sandbox)
    open_analysis(app, 'Collateral & savings')
    assert by_label(app.number_input, 'Initial margin per contract (€)').value == 2000
    assert by_label(app.number_input, 'Maintenance margin per contract (€)').value == 1500
    by_label(app.number_input, 'Signed contracts').set_value(-1).run()
    assert by_label(app.number_input, 'Initial margin per contract (€)').value == 1800
    open_analysis(app, 'Collateral & savings')
    assert not app.exception
    assert by_label(app.number_input, 'Cash benchmark rate (% per year)').value == 1.25
    by_label(app.number_input, 'Holding period (months)').set_value(6).run()
    by_label(app.button, 'Save shared futures assumptions').click().run()
    assert load_comparison(sandbox)[0].months == 6
    by_label(app.radio, 'Cash benchmark source').set_value('manual').run()
    by_label(app.number_input, 'Cash benchmark rate (% per year)').set_value(2.).run()
    by_label(app.button, 'Use saved ECB €STR rate').click().run()
    assert not app.exception
    assert by_label(app.number_input, 'Cash benchmark rate (% per year)').value == 1.25
    activate(app, 'Positions')
    activate(app, 'Futures')
    assert not app.exception
    assert by_label(app.number_input, 'Holding period (months)').value == 6


def test_dated_settlement_entry_expiry_and_proxy_survive_save(tmp_path):
    from datetime import date, timedelta
    import json
    from portfolio_app.futures_market import QUOTE_FILE, VOL_FILE
    from portfolio_app.futures_workspace import load_comparison
    source = create_sample_data(tmp_path / 'synthetic-source')
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    today = date.today()
    (sandbox / QUOTE_FILE).write_text(json.dumps(dict(instrument='FMWN', price=410., quote_date=today.isoformat(),
        contract_expiry=(today+timedelta(days=90)).isoformat(), source='https://www.eurex.com/synthetic',
        kind='Daily settlement', fetched_at=today.isoformat())))
    (sandbox / VOL_FILE).write_text(json.dumps(dict(ticker='EUNL.DE', volatility=.123,
        start_date=(today-timedelta(days=900)).isoformat(), end_date=today.isoformat(), observations=600,
        fetched_at=today.isoformat())))
    app = launch(sandbox)
    open_analysis(app, 'Collateral & savings')
    by_label(app.button, 'Start mock at saved settlement').click().run()
    assert not app.exception
    assert by_label(app.number_input, 'Entry price (points)').value == 410
    assert by_label(app.metric, 'Cumulative futures P&L').value == '€0.00'
    open_analysis(app, 'Collateral & savings')
    assert by_label(app.date_input, 'Simulation start date').value == today
    assert by_label(app.checkbox, 'End at contract last trading day').value
    assert any('90 calendar days' in item.value for item in app.info)
    by_label(app.button, 'Use historical volatility proxy').click().run()
    assert by_label(app.number_input, 'Annual futures volatility (%)').value == 12.3
    by_label(app.button, 'Save shared futures assumptions').click().run()
    saved, _ = load_comparison(sandbox)
    assert saved.volatility == pytest.approx(.123)
    by_label(app.number_input, 'Current mock price (points)').set_value(411.).run()
    by_label(app.button, 'Save mock position').click().run()
    assert not app.exception
    assert load_sandbox(sandbox)[0].reference_price == 410
    assert any('Mark price override' in item.value for item in app.warning)
    restarted = launch(sandbox)
    open_analysis(restarted, 'Collateral & savings')
    assert by_label(restarted.date_input, 'Simulation start date').value == today
    assert by_label(restarted.checkbox, 'End at contract last trading day').value


def test_historical_quarters_change_dates_and_persist_without_changing_position(tmp_path):
    import json
    from test_futures_history import synthetic_history_snapshot, historical_position
    from portfolio_app.futures_history_data import HISTORY_FILE
    from portfolio_app.futures_workspace import save_sandbox, load_historical
    source = create_sample_data(tmp_path / 'synthetic-source')
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    _, revision = load_sandbox(sandbox)
    save_sandbox(sandbox, historical_position(), revision)
    before = load_sandbox(sandbox)[0]
    (sandbox / HISTORY_FILE).write_text(json.dumps(synthetic_history_snapshot()))
    app = launch(sandbox)
    open_analysis(app, 'Collateral & savings')
    by_label(app.radio, 'Financing source').set_value('ecb').run()
    open_analysis(app, 'Historical quarters')
    assert not app.exception
    by_label(app.number_input, 'Quarterly contracts in sequence').set_value(1).run()
    assert any('1 completed quarterly legs' in item.value for item in app.caption)
    assert by_label(app.metric, 'Historical cash end value').value == '€10,000.00'
    by_label(app.number_input, 'Quarterly contracts in sequence').set_value(3).run()
    by_label(app.number_input, 'Starting cash (€)').set_value(12000.).run()
    by_label(app.button, 'Save historical comparison').click().run()
    saved, _ = load_historical(sandbox)
    assert saved.quarters == 3
    assert by_label(app.number_input, 'Starting cash (€)').value == 12000
    assert load_sandbox(sandbox)[0] == before
    activate(app, 'Positions')
    activate(app, 'Futures')
    assert by_label(app.number_input, 'Quarterly contracts in sequence').value == 3
    assert not app.exception


def test_rolling_charts_horizon_controls_and_saved_restart(tmp_path):
    from portfolio_app.futures_workspace import save_sandbox, load_comparison
    from test_futures_rolling import position
    sandbox = fork_futures_workspace(create_sample_data(tmp_path/'source'), tmp_path/'sandbox')
    _, revision = load_sandbox(sandbox)
    save_sandbox(sandbox, position(), revision)
    from test_futures_returns import write_synthetic_return_history
    estimate = write_synthetic_return_history(sandbox)
    app = launch(sandbox)
    open_analysis(app, 'Collateral & savings')
    assert any(item.value == 'Margin-floor breach versus starting cash' for item in app.subheader)
    by_label(app.radio, 'Holding strategy').set_value('Hold & roll quarterly').run(timeout=40)
    assert not app.exception
    assert any(item.value == 'Cumulative risk over the holding period' for item in app.subheader)
    by_label(app.number_input, 'Rolling holding period (years)').set_value(2).run(timeout=40)
    assert any('2 years' in item.value and '8 planned quarterly rolls' in item.value for item in app.caption)
    assert by_label(app.checkbox, 'End at contract last trading day').disabled
    by_label(app.button, 'Save shared futures assumptions').click().run(timeout=40)
    assert load_comparison(sandbox)[0].rolling_years == 2
    assert load_comparison(sandbox)[0].historical_drift
    assert load_comparison(sandbox)[0].index_return == pytest.approx(estimate['index_return'])
    app = launch(sandbox)
    open_analysis(app, 'Collateral & savings')
    assert not app.exception
    assert by_label(app.radio, 'Holding strategy').value == 'Hold & roll quarterly'
    by_label(app.radio, 'Holding strategy').set_value('Single-contract / custom horizon').run()
    assert not app.exception
    assert not any(item.value == 'Cumulative risk over the holding period' for item in app.subheader)


def test_rolling_requires_history_or_explicit_manual_return(tmp_path):
    from portfolio_app.futures_workspace import save_sandbox
    from test_futures_rolling import position
    sandbox = fork_futures_workspace(create_sample_data(tmp_path/'source'), tmp_path/'sandbox')
    _, revision = load_sandbox(sandbox)
    save_sandbox(sandbox, position(), revision)
    app = launch(sandbox, with_history=False)
    open_analysis(app, 'Collateral & savings')
    by_label(app.radio, 'Holding strategy').set_value('Hold & roll quarterly').run()
    assert not app.exception
    assert any('Historical index growth needs' in item.value for item in app.warning)
    assert not any(item.value == 'Cumulative risk over the holding period' for item in app.subheader)
    by_label(app.radio, 'Index growth source').set_value('Manual assumption').run(timeout=40)
    by_label(app.number_input, 'Annual index net-total-return drift (%)').set_value(6.).run(timeout=40)
    assert not app.exception
    assert any(item.value == 'Cumulative risk over the holding period' for item in app.subheader)
    assert any('index drift 6.0%' in item.value for item in app.caption)


def test_ecb_spread_is_shared_saved_and_available_without_implied_history(tmp_path):
    import json
    from portfolio_app.futures_workspace import save_sandbox, load_comparison
    from portfolio_app.futures_history_data import HISTORY_FILE
    from test_futures_history import historical_position, synthetic_history_snapshot
    sandbox = fork_futures_workspace(create_sample_data(tmp_path/'source'), tmp_path/'sandbox')
    _, revision = load_sandbox(sandbox)
    save_sandbox(sandbox, historical_position(), revision)
    (sandbox/HISTORY_FILE).write_text(json.dumps(synthetic_history_snapshot()))
    app = launch(sandbox)
    open_analysis(app, 'Collateral & savings')
    assert not app.exception
    assert by_label(app.radio, 'Financing source').value == 'ecb'
    assert any('Financing used: 1.750%' in item.value for item in app.caption)
    assert not any('implied' in item.label.lower() for item in app.button)
    by_label(app.number_input, 'Financing spread over €STR (percentage points)').set_value(.75).run()
    assert any('financing 2.00%' in item.value for item in app.caption)
    by_label(app.radio, 'Holding strategy').set_value('Hold & roll quarterly').run()
    assert any('financing 2.00%' in item.value for item in app.caption)
    open_analysis(app, 'Historical quarters')
    assert not app.exception and not app.error
    assert any('Historical ECB €STR + spread (+0.75 percentage points)' in item.value for item in app.caption)
    historical_end = by_label(app.metric, 'Reconstructed futures end value').value
    by_label(app.number_input, 'Financing spread over €STR (percentage points)').set_value(0.).run()
    assert by_label(app.metric, 'Reconstructed futures end value').value != historical_end
    assert by_label(app.metric, 'Historical cash end value').value == '€10,000.00'
    by_label(app.button, 'Save shared futures assumptions').click().run()
    assert load_comparison(sandbox)[0].financing_spread == 0.
    restarted = launch(sandbox)
    open_analysis(restarted, 'Collateral & savings')
    assert not restarted.exception
    assert by_label(restarted.number_input, 'Financing spread over €STR (percentage points)').value == 0.
    assert any('financing 1.25%' in item.value for item in restarted.caption)


def test_all_futures_views_share_live_assumptions_without_saving(tmp_path):
    import json
    from portfolio_app.futures_workspace import save_sandbox
    from portfolio_app.futures_history_data import HISTORY_FILE
    from test_futures_history import historical_position, synthetic_history_snapshot
    sandbox = fork_futures_workspace(create_sample_data(tmp_path/'source'), tmp_path/'sandbox')
    _, revision = load_sandbox(sandbox)
    save_sandbox(sandbox, historical_position(), revision)
    (sandbox/HISTORY_FILE).write_text(json.dumps(synthetic_history_snapshot()))
    app = launch(sandbox)
    open_analysis(app, 'Collateral & savings')
    assert by_label(app.radio, 'Financing source').value == 'ecb'
    assert any('Financing used: 1.750%' in item.value for item in app.caption)
    by_label(app.number_input, 'Starting cash (€)').set_value(14000.).run()
    by_label(app.number_input, 'Commission per contract per side (€)').set_value(3.).run()
    by_label(app.number_input, 'Slippage per contract per side (points)').set_value(.02).run()
    by_label(app.radio, 'Financing source').set_value('manual').run()
    by_label(app.number_input, 'Futures financing rate (% per year)').set_value(1.8).run()
    by_label(app.number_input, 'Futures cash yield (% per year)').set_value(.5).run()
    by_label(app.radio, 'Holding strategy').set_value('Hold & roll quarterly').run()
    assert any('financing 1.80%' in item.value for item in app.caption)
    open_analysis(app, 'Historical quarters')
    assert not app.exception
    assert by_label(app.number_input, 'Starting cash (€)').value == 14000
    assert any('Shared commission €3/side' in item.value and 'Cash yield 0.50%' in item.value for item in app.caption)
    assert len([item for item in app.number_input if item.label == 'Commission per contract per side (€)']) == 1
    by_label(app.number_input, 'Commission per contract per side (€)').set_value(4.).run()
    open_analysis(app, 'Price scenarios')
    assert not app.exception
    assert by_label(app.number_input, 'Starting cash (€)').value == 14000
    open_analysis(app, 'Collateral & savings')
    by_label(app.radio, 'Holding strategy').set_value('Single-contract / custom horizon').run()
    assert not app.exception
    assert by_label(app.number_input, 'Commission per contract per side (€)').value == 4
    assert by_label(app.number_input, 'Futures financing rate (% per year)').value == 1.8
    assert any('financing 1.80%' in item.value for item in app.caption)
    assert any('Growth source: historical MSCI World EUR proxy' in item.value for item in app.caption)


def test_newer_ecb_snapshot_does_not_block_or_leak_into_forward_comparison(tmp_path):
    import json
    from portfolio_app.futures_workspace import save_sandbox
    from portfolio_app.futures_reference import REFERENCE_FILE
    from portfolio_app.futures_history_data import HISTORY_FILE
    from test_futures_history import historical_position, synthetic_history_snapshot
    sandbox = fork_futures_workspace(create_sample_data(tmp_path/'source'), tmp_path/'sandbox')
    _, revision = load_sandbox(sandbox)
    save_sandbox(sandbox, historical_position(), revision)
    (sandbox/HISTORY_FILE).write_text(json.dumps(synthetic_history_snapshot()))
    launch(sandbox)
    path = sandbox/REFERENCE_FILE
    raw = json.loads(path.read_text())
    raw.update(estr_reference_date='2003-10-01', estr_rate=.08)
    path.write_text(json.dumps(raw))
    app = launch(sandbox)
    open_analysis(app, 'Collateral & savings')
    assert not app.exception and not app.error
    assert any('Financing used: 0.500%' in item.value for item in app.caption)
    assert by_label(app.number_input, 'Cash benchmark rate (% per year)').value == 0.
    assert any(item.value == 'Margin-floor breach versus starting cash' for item in app.subheader)
