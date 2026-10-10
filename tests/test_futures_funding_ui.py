"""Synthetic funding-study UI, isolated from all working portfolio data."""
from dataclasses import replace
from pathlib import Path
import json

import numpy as np
import pandas as pd

from portfolio_app.demo import create_demo_data
from portfolio_app.futures_workspace import fork_futures_workspace, load_sandbox, save_sandbox, save_comparison
from portfolio_app.futures_comparison import ComparisonSettings
from portfolio_app.futures_funding_data import load_funding_settings
from test_futures_ui import launch, open_analysis
from test_ui import by_label, activate


def synthetic_funding_workspace(directory):
    sandbox = fork_futures_workspace(create_demo_data(directory/'source'), directory/'study')
    position, revision = load_sandbox(sandbox)
    position = replace(position, quote_date='2025-01-02', contract_expiry='2025-03-21',
                       reference_price=100., entry_price=100., mark_price=100.,
                       margin_per_contract=200., maintenance_per_contract=100.)
    save_sandbox(sandbox, position, revision)
    save_comparison(sandbox, ComparisonSettings(financing_source='manual', benchmark_source='manual',
        financing_rate=.01, savings_rate=.02, historical_drift=False, index_return=.03), None)
    dates = pd.bdate_range('2001-01-02', periods=310)
    pd.Series(100+np.sin(np.arange(len(dates))/10), index=dates, name='eur_index').rename_axis('date').to_csv(sandbox/'funding-history.csv')
    return sandbox


def enter_study(app):
    by_label(app.number_input, 'Total study reserve (€)').set_value(1000.).run()
    by_label(app.number_input, 'Baseline cash target or loss buffer (€)').set_value(700.).run()
    by_label(app.text_input, 'Candidate broker targets (€; comma-separated)').set_value('400, 600').run()
    by_label(app.selectbox, 'Start spacing (observations)').set_value(21).run()
    return app


def test_default_setup_run_inspect_stale_save_and_no_portfolio_mutation(tmp_path):
    sandbox = synthetic_funding_workspace(tmp_path)
    protected = {p.name: p.read_bytes() for p in sandbox.iterdir() if p.is_file()}
    app = launch(sandbox)
    assert not app.exception
    assert by_label(app.get('button_group'), 'Futures analysis').value == 'Funding policies'
    assert by_label(app.number_input, 'Total study reserve (€)').value is None
    assert by_label(app.button, 'Run funding study').disabled
    enter_study(app)
    by_label(app.button, 'Run funding study').click().run(timeout=30)
    assert not app.exception and not app.error
    assert len(app.get('plotly_chart')) == 3
    assert any('overlapping windows' in item.value for item in app.caption)
    by_label(app.button, 'Save funding study settings').click().run()
    (study, source), _ = load_funding_settings(sandbox)
    assert study.reserve == 1000 and len(study.candidates) == 2
    by_label(app.get('button_group'), 'Futures analysis').set_value('Stress & history').run()
    assert not app.exception and not app.error
    assert len(app.get('plotly_chart')) == 2
    assert by_label(app.metric, 'Actual opening broker cash').value == '€400.00'
    by_label(app.number_input, 'Total study reserve (€)').set_value(1100.).run()
    assert any('stale' in item.value for item in app.warning)
    # Old run remains internally coherent until rerun, including drill-down.
    assert by_label(app.metric, 'Actual opening broker cash').value == '€400.00'
    activate(app, 'Overview')
    assert not app.exception
    activate(app, 'Futures')
    assert by_label(app.number_input, 'Total study reserve (€)').value == 1100
    open_analysis(app, 'Collateral & savings')
    assert not app.exception
    for name, content in protected.items():
        assert (sandbox/name).read_bytes() == content
    restarted = launch(sandbox)
    assert by_label(restarted.number_input, 'Total study reserve (€)').value == 1000
    assert not restarted.exception


def test_changed_source_and_invalid_inputs_never_silently_reuse_results(tmp_path):
    sandbox = synthetic_funding_workspace(tmp_path)
    app = enter_study(launch(sandbox))
    by_label(app.button, 'Run funding study').click().run(timeout=30)
    path = sandbox/'funding-history.csv'
    frame = pd.read_csv(path)
    frame['eur_index'] *= 1.001
    frame.to_csv(path, index=False)
    app.run()
    assert any('stale' in item.value for item in app.warning)
    by_label(app.text_input, 'Candidate broker targets (€; comma-separated)').set_value('nan').run()
    assert app.error and by_label(app.button, 'Run funding study').disabled
    by_label(app.text_input, 'Candidate broker targets (€; comma-separated)').set_value('400').run()
    by_label(app.text_input, 'History CSV, EUR return snapshot, or handoff directory').set_value(str(sandbox/'missing.csv')).run()
    assert by_label(app.button, 'Run funding study').disabled
    assert not app.exception


def test_external_settings_change_requires_reload(tmp_path):
    from portfolio_app.futures_funding import FundingPolicy, FundingStudy
    from portfolio_app.futures_funding_data import save_funding_settings
    sandbox = synthetic_funding_workspace(tmp_path)
    app = enter_study(launch(sandbox))
    other = FundingStudy(1200., FundingPolicy(800.), (FundingPolicy(500.),))
    save_funding_settings(sandbox, other, str(sandbox/'funding-history.csv'), None)
    by_label(app.button, 'Save funding study settings').click().run()
    assert any('changed' in item.value for item in app.error)
    assert load_funding_settings(sandbox)[0][0] == other


def test_funding_study_preserves_funded_position_capital_and_lifetime_gains(tmp_path):
    sandbox = synthetic_funding_workspace(tmp_path)
    position, revision = load_sandbox(sandbox)
    funded = replace(position, reference_capital_eur=2000., net_funding_eur=150.,
                     closed_pnl_eur=75., fees_eur=5.)
    save_sandbox(sandbox, funded, revision)
    app = enter_study(launch(sandbox))
    assert not app.exception
    assert by_label(app.number_input, 'Reference capital (€)').value == 2000.
    assert by_label(app.number_input, 'Net additional funding (€)').value == 150.
    assert by_label(app.metric, 'Cumulative futures P&L').value == '€70.00'
    by_label(app.button, 'Run funding study').click().run(timeout=30)
    assert not app.exception and not app.error
    open_analysis(app, 'Price scenarios')
    assert not app.exception
    assert by_label(app.number_input, 'Previous contracts P&L (€)').value == 75.
    assert by_label(app.number_input, 'Lifetime transaction fees (€)').value == 5.
    assert load_sandbox(sandbox)[0] == funded
