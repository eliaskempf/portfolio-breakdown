"""Integration against temporary, explicitly invented sandbox data."""
from dataclasses import replace
import json

import pytest

from portfolio_app.allocation import save_allocation
from portfolio_app.futures_workspace import fork_futures_workspace, load_sandbox, save_sandbox
from portfolio_app.exposure_targets import load_exposure_targets
from portfolio_app.leverage_settings import LeverageSettings, MarginLiability, save_leverage_settings
from portfolio_app.positions import read_snapshot
from test_futures_ui import launch
from test_portfolio_exposure import CONFIG
from test_ui import activate, by_label, list_frame


def synthetic_workspace(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source/'holdings.csv').write_text('position_key,id,name,ticker,shares,instrument_type,exposure_kind,bucket_id,within_bucket_target\n'
        'fund,fund,Invented fund,SYNTH-F,1,etf,equity,core,1\n'
        'stock,stock,Invented stock,SYNTH-S,1,equity,equity,stocks,1\n'
        'cash,cash,Invented cash,SYNTH-C,1,cash,non_equity,cash,1\n')
    (source/'classifications.yaml').write_text('stock:\n  classifications:\n    sector: [[Invented sector]]\n')
    (source/'demo_prices.json').write_text(json.dumps({'prices': {ticker: {'price': value, 'currency': 'EUR', 'observed_at': '2026-01-01T12:00:00+00:00'} for ticker, value in [('SYNTH-F', 60000.), ('SYNTH-S', 30000.), ('SYNTH-C', 10000.)]}, 'fx': {}}))
    save_allocation(source/'allocation.yaml', CONFIG, read_snapshot(source/'holdings.csv').holdings, None)
    sandbox = fork_futures_workspace(source, tmp_path/'sandbox')
    pos, rev = load_sandbox(sandbox)
    save_sandbox(sandbox, replace(pos, bucket_id='core'), rev)
    return source, sandbox


def test_saved_overlay_default_allocation_signed_views_and_navigation(tmp_path):
    source, sandbox = synthetic_workspace(tmp_path)
    original = (source/'holdings.csv').read_bytes()
    app = launch(sandbox)
    by_label(app.number_input, 'Signed contracts').set_value(-2).run()
    activate(app, 'Overview')
    assert not app.exception
    assert by_label(app.get('button_group'), 'Allocation measure').value == 'Capital allocation'
    assert by_label(app.metric, 'Portfolio leverage · net').value == '1.40×'
    assert not any(item.label == 'Portfolio leverage · gross' for item in app.metric)
    by_label(app.get('button_group'), 'Allocation measure').set_value('Market exposure').run()
    assert not app.exception
    assert app.get('plotly_chart')
    by_label(app.selectbox, 'Category').set_value('core').run()
    table = list_frame(app, 'Market exposure')
    assert table['Exposure'].sum() == 110000.
    activate(app, 'Exposure')
    for view in ['Assets & companies', 'Themes & sectors', 'Geography', 'ETF sources']:
        by_label(app.get('button_group'), 'Market exposure view').set_value(view).run()
        assert not app.exception
    activate(app, 'Rebalance')
    assert any('Capital plan' in x.value for x in app.caption)
    activate(app, 'Rebalance', 'Targets')
    by_label(app.get('button_group'), 'Target measure').set_value('Exposure targets').run()
    assert not app.exception
    assert by_label(app.number_input, 'Desired total exposure (× net equity)').value is None
    by_label(app.selectbox, 'Exposure policy').set_value('overlay_only')
    by_label(app.number_input, 'Desired total exposure (× net equity)').set_value(1.2)
    by_label(app.selectbox, 'Overlay destination (overlay-only policy)').set_value('core')
    by_label(app.button, 'Save exposure targets').click().run()
    assert not app.exception and not app.error, [x.value for x in app.error]
    assert load_exposure_targets(sandbox, CONFIG)[0].multiple == 1.2
    assert (source/'holdings.csv').read_bytes() == original
    assert not (source/'exposure-targets.json').exists()
    assert load_sandbox(sandbox)[0].contracts == 1


def test_short_overlay_and_liability_visible_without_area_chart_errors(tmp_path):
    _, sandbox = synthetic_workspace(tmp_path)
    position, revision = load_sandbox(sandbox)
    save_sandbox(sandbox, replace(position, contracts=-2), revision)
    save_leverage_settings(sandbox, LeverageSettings((MarginLiability('loan', 'Invented loan', 30000.),)), None)
    app = launch(sandbox)
    activate(app, 'Overview')
    assert not app.exception
    assert by_label(app.metric, 'Portfolio value').value == '€70,000.00'
    by_label(app.get('button_group'), 'Allocation measure').set_value('Market exposure').run()
    table = list_frame(app, 'Market exposure')
    assert table.loc[table.Category.eq('Core'), 'Exposure'].item() == -40000
    assert not app.exception


def test_large_exposure_chart_keeps_largest_signed_contributions():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
import pandas as pd
from portfolio_app.portfolio_exposure_ui import signed_chart
frame = pd.DataFrame({'Category': ['Invented ' + str(i) for i in range(100)], 'Exposure': [-1000.] + list(range(1, 100))})
signed_chart(frame, 'Category', 'Exposure', 'synthetic_chart')
''').run()
    assert not app.exception
    spec = json.loads(app.get('plotly_chart')[0].proto.spec)
    assert len(spec['data'][0]['x']) == 30
    assert -1000. in spec['data'][0]['x']
    assert any('table retains every row' in x.value for x in app.caption)


def test_overview_summary_scope_and_future_visibility(tmp_path):
    from test_ui import list_frame
    _, sandbox = synthetic_workspace(tmp_path)
    position, revision = load_sandbox(sandbox)
    save_sandbox(sandbox, replace(position, mark_price=510.), revision)
    app = launch(sandbox)
    activate(app, 'Overview')
    assert not app.exception
    assert [metric.label for metric in app.metric] == ['Portfolio value', 'Portfolio share', 'Portfolio leverage · net']
    assert by_label(app.metric, 'Portfolio value').value == '€101,000.00'
    assert 'future:overlay' in set(list_frame(app, 'Positions').id)
    by_label(app.selectbox, 'Category').set_value('core').run()
    assert by_label(app.metric, 'Category value').value == '€60,000.00'
    assert by_label(app.metric, 'Category leverage · net').value == '1.82×'
    assert by_label(app.metric, 'Portfolio share').value == '60.0%'
    assert 'future:overlay' in set(list_frame(app, 'Positions').id)
    by_label(app.selectbox, 'Category').set_value('stocks').run()
    assert by_label(app.metric, 'Category leverage · net').value == '1.00×'
    assert by_label(app.metric, 'Portfolio share').value == '30.0%'
    assert 'future:overlay' not in set(list_frame(app, 'Positions').id)
    by_label(app.get('button_group'), 'Allocation measure').set_value('Market exposure').run()
    assert list_frame(app, 'Positions').shape[0] == 1
    assert list_frame(app, 'Market exposure')['Exposure'].sum() == 30000.
    assert not app.exception


def test_ordinary_workflow_enablement_and_chart_policy_independence(tmp_path):
    from portfolio_app.exposure_targets import ExposureTargets, save_exposure_targets
    from portfolio_app.leverage_settings import load_leverage_settings
    source, _ = synthetic_workspace(tmp_path)
    before = {name: (source/name).read_bytes() for name in ['holdings.csv', 'allocation.yaml']}
    app = launch(source, open_futures=False)
    activate(app, 'Overview')
    assert not any(x.label == 'Allocation measure' for x in app.get('button_group'))
    assert not any('leverage' in x.label for x in app.metric)
    activate(app, 'Rebalance', 'Targets')
    assert not any(x.label == 'Target measure' for x in app.get('button_group'))
    assert not any(x.label == 'Active planning basis' for x in app.selectbox)
    by_label(app.checkbox, 'Enable leverage').check()
    by_label(app.button, 'Save active policy').click().run()
    assert not app.error and load_leverage_settings(source)[0].enabled
    saved = ExposureTargets(1.1, 'composition', {'core': .6, 'stocks': .4}, version=2, universe='market')
    save_exposure_targets(source, saved, CONFIG, None)
    app.run()
    by_label(app.selectbox, 'Active planning basis').set_value('exposure')
    by_label(app.button, 'Save active policy').click().run()
    assert not app.error
    activate(app, 'Rebalance', 'Plan')
    assert by_label(app.selectbox, 'Active planning basis').value == 'exposure'
    by_label(app.multiselect, 'Permitted instruments').set_value(['fund']).run()
    by_label(app.number_input, 'External contribution (EUR)').set_value(1000.).run()
    by_label(app.button, 'Calculate exposure plan').click().run()
    assert not app.exception and not app.error, [x.value for x in app.error]
    assert any('Remaining target gaps' in x.value for x in app.warning)
    policy_bytes = (source/'exposure-targets.json').read_bytes()
    activate(app, 'Overview')
    for measure in ['Market exposure', 'Capital allocation']:
        by_label(app.get('button_group'), 'Allocation measure').set_value(measure).run()
        assert not app.exception
    assert load_leverage_settings(source)[0].active_basis == 'exposure'
    assert (source/'exposure-targets.json').read_bytes() == policy_bytes
    assert all((source/name).read_bytes() == data for name, data in before.items())
    activate(app, 'Rebalance', 'Targets')
    by_label(app.get('button_group'), 'Target measure').set_value('Exposure targets').run()
    by_label(app.button, 'Clear exposure targets').click().run()
    assert any('Activate the capital policy' in x.value for x in app.error)


def test_active_future_cannot_disable_and_planner_requires_declared_collateral(tmp_path):
    from portfolio_app.exposure_targets import ExposureTargets, save_exposure_targets
    from portfolio_app.leverage_settings import load_leverage_settings
    _, sandbox = synthetic_workspace(tmp_path)
    pos, rev = load_sandbox(sandbox)
    save_sandbox(sandbox, replace(pos, reference_capital_eur=10000.), rev)
    save_exposure_targets(sandbox, ExposureTargets(1.5, 'composition', {'core': 1.}, version=2, universe='market'), CONFIG, None)
    app = launch(sandbox, open_futures=False)
    activate(app, 'Rebalance', 'Targets')
    assert by_label(app.checkbox, 'Enable leverage').disabled
    by_label(app.selectbox, 'Active planning basis').set_value('exposure')
    by_label(app.button, 'Save active policy').click().run()
    activate(app, 'Rebalance', 'Plan')
    by_label(app.multiselect, 'Permitted instruments').set_value(['future:overlay']).run()
    by_label(app.button, 'Calculate exposure plan').click().run()
    assert app.error and not app.exception
    by_label(app.number_input, 'Eligible existing future collateral (EUR)').set_value(10000.).run()
    by_label(app.number_input, 'Declared initial margin per contract (EUR)').set_value(5000.).run()
    by_label(app.button, 'Calculate exposure plan').click().run()
    assert not app.error and not app.exception
    assert any('Remaining target gaps' in x.value for x in app.warning)


def test_market_basis_survives_navigation(tmp_path):
    _, sandbox = synthetic_workspace(tmp_path)
    app = launch(sandbox, open_futures=False)
    activate(app, 'Exposure')
    by_label(app.get('button_group'), 'Exposure basis').set_value('Equity only').run()
    activate(app, 'Positions')
    activate(app, 'Exposure')
    assert not app.exception
    assert by_label(app.get('button_group'), 'Exposure basis').value == 'Equity only'
