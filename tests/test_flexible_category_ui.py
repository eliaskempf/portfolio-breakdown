"""Synthetic UI acceptance for mixed categories, flat views and shared targets."""
import json

import pytest

from portfolio_app.portfolio.allocation import allocation_content
from test_flexible_categories import example
from test_ui import launch, activate, by_label
from list_helpers import list_frame


@pytest.fixture
def workspace(tmp_path):
    config, positions = example()
    rows = positions.rename(columns={'position_id':'position_key'}).drop(columns='current_value_reporting')
    rows['ticker'] = ['SYNTH-A','SYNTH-B','SYNTH-C']
    rows.to_csv(tmp_path/'holdings.csv',index=False)
    (tmp_path/'allocation.yaml').write_text(allocation_content(config))
    (tmp_path/'demo_prices.json').write_text(json.dumps({'prices':{ticker:dict(price=value,currency='EUR',observed_at='2026-09-01T12:00:00+00:00') for ticker,value in zip(rows.ticker,[20,20,60])},'fx':{}}))
    return tmp_path


def test_overview_flat_view_keeps_scope_and_persists(workspace):
    app = launch(workspace, tab='Overview')
    by_label(app.selectbox,'Category').set_value('sleeve').run()
    assert not app.exception
    grouped = list_frame(app, 'Allocation')
    assert len(grouped) == 2 and grouped.Value.sum() == 100
    by_label(app.checkbox,'Flatten groups').set_value(True).run()
    assert not app.exception
    flat = list_frame(app,'Allocation')
    assert len(flat) == 3 and flat.Value.sum() == 100
    assert flat['Target (%)'].isna().sum() == 2
    assert by_label(app.selectbox,'Category').value == 'sleeve'
    reloaded = launch(workspace, tab='Overview')
    assert by_label(reloaded.checkbox,'Flatten groups').value is True


def test_mixed_editor_and_all_planning_modes(workspace):
    app = launch(workspace, tab='Rebalance', subtab='Targets')
    by_label(app.selectbox,'Allocation category').set_value('sleeve').run()
    assert not app.exception
    frame = next(item.value for item in app.dataframe if 'kind' in item.value)
    assert frame.kind.tolist() == ['category','position']
    assert frame.Target.sum() == 100
    by_label(app.selectbox,'Allocation category').set_value('group').run()
    assert by_label(app.get('button_group'),'Internal allocation').value == 'flexible'
    assert not app.exception
    activate(app,'Rebalance','Plan')
    by_label(app.button,'Calculate plan').click().run()
    assert not app.exception and not app.error
    by_label(app.get('button_group'),'Planning scope').set_value('Within a category').run()
    by_label(app.selectbox,'Planning category').set_value('sleeve').run()
    for mode in ['Fewest trades (buys and sells)','Minimum new money (no sells)','Allocate new money']:
        by_label(app.selectbox,'Rebalancing mode').set_value(mode).run()
        by_label(app.button,'Calculate plan').click().run()
        assert not app.exception and not app.error
        assert any(item.label == 'Trades' for item in app.metric)


def test_create_category_in_legacy_workspace_and_edit_priorities(workspace):
    from portfolio_app.portfolio.allocation import Allocation, Bucket, load_allocation
    import pandas as pd
    rows = pd.read_csv(workspace/'holdings.csv')
    rows['bucket_id'] = 'sleeve'
    rows['within_bucket_target'] = [.2,.3,.5]
    rows.to_csv(workspace/'holdings.csv',index=False)
    (workspace/'allocation.yaml').write_text(allocation_content(Allocation((Bucket('sleeve','Example sleeve',target=1.),))))
    app = launch(workspace, tab='Rebalance',subtab='Targets')
    app.session_state['targets_structure_open'] = True
    app.run()
    by_label(app.multiselect,'Investments to combine').set_value(['p1','p2']).run()
    by_label(app.text_input,'New category name').set_value('Invented combined').run()
    assert by_label(app.number_input,'Combined target (% of parent)').value == 50.
    by_label(app.button,'Create category').click().run()
    assert not app.exception and not app.error
    config = load_allocation(workspace/'allocation.yaml')
    group = next(b for b in config.buckets if b.name == 'Invented combined')
    by_label(app.selectbox,'Allocation category').set_value(group.id).run()
    editor = next(key for key in app.session_state.to_dict() if key.startswith('targets_member_editor_') and key.endswith('flexible'))
    app.session_state[editor] = {'edited_rows':{0:{'Buy':1,'Sell':2},1:{'Buy':2,'Sell':1}},'added_rows':[],'deleted_rows':[]}
    app.run()
    by_label(app.button,'Save priorities').click().run()
    assert not app.exception and not app.error
    saved = next(b for b in load_allocation(workspace/'allocation.yaml').buckets if b.id == group.id)
    assert saved.buy_priority == ('p1','p2') and saved.sell_priority == ('p2','p1')
    # Retained member targets use the old denominator; switching to fixed needs review.
    by_label(app.get('button_group'),'Internal allocation').set_value('targets').run()
    by_label(app.button,'Review member targets').click().run()
    app.button(key='overview_target_save').click().run()
    assert any('100%' in item.value for item in app.error)
    assert not any(button.label == 'Save incomplete split' for button in app.button)
    editor = next(key for key in app.session_state.to_dict() if key.startswith('overview_target_editor_'))
    app.session_state[editor] = {'edited_rows':{0:{'Target':40},1:{'Target':60}},'added_rows':[],'deleted_rows':[]}
    app.run()
    app.button(key='overview_target_save').click().run()
    assert not app.exception and not app.error
    assert not load_allocation(workspace/'allocation.yaml').flexible(group.id)


def test_view_switch_does_not_invoke_trade_optimizer(workspace, monkeypatch):
    from portfolio_app.portfolio import category_rebalancing
    def unexpected(*args, **kwargs):
        raise AssertionError('Browsing allocation must not calculate trades')
    monkeypatch.setattr(category_rebalancing, 'category_plan', unexpected)
    app = launch(workspace, tab='Overview')
    by_label(app.selectbox,'Category').set_value('sleeve').run()
    for view in (True, False):
        by_label(app.checkbox,'Flatten groups').set_value(view).run()
        assert not app.exception
    activate(app,'Rebalance','Plan')
    by_label(app.get('button_group'),'Planning scope').set_value('Within a category').run()
    by_label(app.selectbox,'Planning category').set_value('sleeve').run()
    assert not app.exception
