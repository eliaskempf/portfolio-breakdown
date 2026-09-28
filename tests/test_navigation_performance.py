"""Execution boundaries and state preservation, with synthetic offline inputs."""
import json
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from portfolio_app.demo import create_demo_data
from portfolio_app.positions import read_snapshot
from test_ui import activate, by_label


def app_for(path):
    return AppTest.from_string(
        f'from pathlib import Path\nfrom portfolio_app.ui import render_app\nrender_app(Path({str(path)!r}), demo=True)',
        default_timeout=10).run()


def test_overview_and_category_saves_skip_hidden_views(tmp_path):
    create_demo_data(tmp_path)
    with patch('portfolio_app.ui.render_analysis') as exposure, patch('portfolio_app.ui.render_rebalancing') as rebalance:
        app = app_for(tmp_path)
        assert not app.exception
        assert not exposure.called and not rebalance.called
        activate(app, 'Rebalance', 'Targets')
        assert not exposure.called and not rebalance.called
        by_label(app.button, 'Enable reviewed allocation').click().run()
        assert not app.exception
        by_label(app.button, 'Save categories').click().run()
        assert not app.exception
        assert not exposure.called and not rebalance.called


def test_filters_category_and_planning_inputs_survive_unmount(tmp_path):
    create_demo_data(tmp_path)
    app = app_for(tmp_path)
    by_label(app.selectbox, 'Category').set_value('bucket-1').run()
    activate(app, 'Exposure')
    by_label(app.text_input, 'Search exposure').set_value('Invented search').run()
    by_label(app.multiselect, 'Portfolio').set_value(['Core']).run()
    activate(app, 'Rebalance', 'Plan')
    by_label(app.number_input, 'Allowed deviation (pp)').set_value(2.).run()
    activate(app, 'Overview')
    assert by_label(app.selectbox, 'Category').value == 'bucket-1'
    activate(app, 'Exposure')
    assert by_label(app.text_input, 'Search exposure').value == 'Invented search'
    assert by_label(app.multiselect, 'Portfolio').value == ['Core']
    activate(app, 'Rebalance', 'Plan')
    assert by_label(app.number_input, 'Allowed deviation (pp)').value == 2.


def test_close_callback_skips_history_loader(tmp_path):
    create_demo_data(tmp_path)
    app = app_for(tmp_path)
    for key, value in dict(position_edit_selected='position-0', position_edit_action='Details', position_edit_dialog=True).items():
        app.session_state[key] = value
    with patch('portfolio_app.position_detail.render_position_detail') as detail:
        app.run()
        assert detail.call_count == 1
        detail.reset_mock()
        by_label(app.button, 'Close').click().run()
        assert not app.exception and not detail.called
        assert not app.session_state['position_edit_dialog']


def test_cached_inputs_reload_external_edits(tmp_path):
    create_demo_data(tmp_path)
    with patch('portfolio_app.input_cache.read_snapshot', wraps=read_snapshot) as reads:
        app = app_for(tmp_path)
        app.run()
        assert reads.call_count == 1
        path = tmp_path / 'holdings.csv'
        path.write_text(path.read_text().replace(',2,11.25,', ',3,11.25,'))
        app.run()
        assert reads.call_count == 2
        assert by_label(app.metric, 'Priced value').value == '€824.00'


def test_editor_rows_survive_lazy_tab_roundtrip(tmp_path):
    create_demo_data(tmp_path)
    app = app_for(tmp_path)
    activate(app, 'Rebalance', 'Targets')
    by_label(app.button, 'Enable reviewed allocation').click().run()
    key = next(k for k in app.session_state.filtered_state if k.startswith('targets_categories_') and '_rows_' in k)
    app.session_state[key] = {'edited_rows': {0: {'Name': 'Invented new category'}}, 'added_rows': [], 'deleted_rows': []}
    app.run()
    activate(app, 'Overview')
    activate(app, 'Rebalance', 'Targets')
    by_label(app.button, 'Save categories').click().run()
    assert not app.exception
    assert 'Invented new category' in (tmp_path / 'allocation.yaml').read_text()


def test_scoped_plan_survives_age_changes_but_not_changed_values(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('position_key,id,name,ticker,shares,bucket_id,within_bucket_target\n'
                    'p1,a,Invented A,SYN-A,1,core,0.5\np2,b,Invented B,SYN-B,1,core,0.5\n')
    (tmp_path / 'allocation.yaml').write_text('version: 2\nbuckets:\n- id: core\n  name: Invented Core\n  target: 1\n')
    (tmp_path / 'demo_prices.json').write_text(json.dumps({'prices': {
        ticker: dict(price=100, currency='EUR', observed_at='2026-01-01T12:00:00+00:00')
        for ticker in ['SYN-A', 'SYN-B']}, 'fx': {}}))
    app = app_for(tmp_path)
    activate(app, 'Rebalance', 'Plan')
    by_label(app.button, 'Calculate plan').click().run()
    assert not app.exception and not app.error
    assert by_label(app.metric, 'Unallocated cash').value == '€0.00'
    app.run()  # FX identity timestamps and quote ages change on every rerun.
    assert by_label(app.metric, 'Unallocated cash').value == '€0.00'
    activate(app, 'Overview')
    activate(app, 'Rebalance', 'Plan')
    assert by_label(app.metric, 'Unallocated cash').value == '€0.00'
    path.write_text(path.read_text().replace('SYN-A,1,', 'SYN-A,2,'))
    app.run()
    assert not any(item.label == 'Unallocated cash' for item in app.metric)
