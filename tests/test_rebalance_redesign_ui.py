"""Synthetic offline workflows for plan presentation and target drafts."""
import json
import re

import pytest
import yaml

from test_ui import activate, by_label, launch
from portfolio_app.allocation import load_allocation
from portfolio_app.positions import read_snapshot


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / 'holdings.csv').write_text(
        'position_key,id,name,ticker,shares,account,bucket_id,within_bucket_target\n'
        'first,a,Invented Asset,SYN-A,2,First,core,1\n'
        'second,b,Invented Asset,SYN-B,1,Second,active,0.5\n'
        'third,c,Invented Third,SYN-C,1,First,active,0.5\n')
    (tmp_path / 'allocation.yaml').write_text(yaml.safe_dump({'version': 2, 'buckets': [
        dict(id='core', name='Core category', target=.5), dict(id='active', name='Active category', target=.5),
        dict(id='empty', name='Planned category', target=0.)]}))
    (tmp_path / 'demo_prices.json').write_text(json.dumps({'prices': {
        symbol: dict(price=value, currency='EUR', observed_at='2026-09-01T12:00:00+00:00')
        for symbol, value in [('SYN-A', 100.), ('SYN-B', 40.), ('SYN-C', 60.)]}, 'fx': {}}))
    return tmp_path


def editor_key(app, prefix):
    return next(key for key in app.session_state.filtered_state if key.startswith(prefix) and re.search(r'_rows_\d+$', key))


def edit(app, prefix, changes):
    key = editor_key(app, prefix)
    app.session_state[key] = {'edited_rows': changes, 'added_rows': [], 'deleted_rows': []}
    app.run()
    assert not app.exception


def test_portfolio_results_are_trades_only_readable_and_display_changes_keep_plan(workspace):
    before = {name: (workspace / name).read_bytes() for name in ['holdings.csv', 'allocation.yaml']}
    app = launch(workspace, tab='Rebalance', subtab='Plan')
    assert not app.exception
    assert len(app.radio) == 0
    assert len(app.get('popover')) == 1
    by_label(app.number_input, 'Maximum trades').set_value(1).run()
    by_label(app.button, 'Calculate plan').click().run()
    assert not app.exception and not app.error
    trades = next(item.value for item in app.dataframe if 'Action' in item.value)
    assert len(trades) == 1
    assert trades['Trade (EUR)'].ne(0).all()
    assert trades.Category.iloc[0].endswith('category')
    for item in app.dataframe:
        assert not {'position_id', 'id', 'ticker', 'Bucket ID', 'Parent'} & set(item.value.columns)
    full = next(item.value for item in app.dataframe if 'Investment' in item.value and 'After %' in item.value)
    assert len(full) == 3
    assert full.Action.eq('Hold').sum() == 2
    impact = next(item.value for item in app.dataframe if 'Category' in item.value and 'Gap (pp)' in item.value and 'Investment' not in item.value)
    assert 'Unassigned' not in impact.Category.tolist()
    assert 'Planned category' in impact.Category.tolist()
    activate(app, 'Rebalance', 'Targets')
    activate(app, 'Rebalance', 'Plan')
    assert any(item.label == 'Trades' for item in app.metric)
    by_label(app.number_input, 'Contribution (EUR)').set_value(600).run()
    assert not any(item.label == 'Trades' for item in app.metric)
    assert all((workspace / name).read_bytes() == content for name, content in before.items())


def test_filtered_target_edits_survive_switches_and_save_by_identity(workspace):
    app = launch(workspace, tab='Rebalance', subtab='Targets')
    assert not app.exception
    by_label(app.selectbox, 'Position category').set_value('active').run()
    edit(app, 'targets_positions_', {0: {'within_bucket_target': 40.}, 1: {'within_bucket_target': 60.}})
    by_label(app.selectbox, 'Position category').set_value('core').run()
    edit(app, 'targets_positions_', {0: {'within_bucket_target': None}})
    activate(app, 'Overview')
    activate(app, 'Rebalance', 'Targets')
    assert by_label(app.selectbox, 'Position category').value == 'core'
    by_label(app.selectbox, 'Position category').set_value('active').run()
    rows = next(item.value for item in app.dataframe if 'within_bucket_target' in item.value)
    assert rows.within_bucket_target.tolist() == [40., 60.]
    by_label(app.button, 'Save position targets').click().run()
    assert not app.exception and not app.error
    saved = read_snapshot(workspace / 'holdings.csv').holdings
    assert saved.loc[saved.id.eq('b'), 'within_bucket_target'].item() == .4
    assert saved.loc[saved.id.eq('c'), 'within_bucket_target'].item() == .6
    assert saved.loc[saved.id.eq('a'), 'within_bucket_target'].isna().all()
    assert saved.shares.tolist() == [2, 1, 1]


def test_category_add_discard_delete_and_block_in_use(workspace):
    app = launch(workspace, tab='Rebalance', subtab='Targets')
    original = (workspace / 'allocation.yaml').read_bytes()
    by_label(app.text_input, 'Category name').set_value('Invented future')
    by_label(app.button, 'Add to draft').click().run()
    assert not app.exception
    categories = next(item.value for item in app.dataframe if 'ID' in item.value)
    assert len(categories) == 4
    assert categories.Name.tolist()[-1] == 'Invented future'
    assert (workspace / 'allocation.yaml').read_bytes() == original
    by_label(app.button, 'Save categories').click().run()
    assert not app.exception and not app.error
    assert len(load_allocation(workspace / 'allocation.yaml').buckets) == 4
    by_label(app.multiselect, 'Categories to delete').set_value(['core']).run()
    by_label(app.button, 'Remove from draft').click().run()
    assert any('Move positions' in item.value for item in app.error)
    by_label(app.multiselect, 'Categories to delete').set_value(['empty']).run()
    by_label(app.button, 'Remove from draft').click().run()
    assert not app.exception and not app.error
    app.button(key=next(k for k in app.session_state.filtered_state if k.startswith('targets_categories_') and k.endswith('_discard'))).click().run()
    categories = next(item.value for item in app.dataframe if 'ID' in item.value)
    assert 'empty' in categories.ID.tolist()
    by_label(app.multiselect, 'Categories to delete').set_value(['empty']).run()
    by_label(app.button, 'Remove from draft').click().run()
    by_label(app.button, 'Save categories').click().run()
    assert not app.exception and not app.error
    assert 'empty' not in {b.id for b in load_allocation(workspace / 'allocation.yaml').buckets}


def test_target_reassignment_remains_visible_in_draft_and_discard_restores(workspace):
    original = (workspace / 'holdings.csv').read_bytes()
    app = launch(workspace, tab='Rebalance', subtab='Targets')
    by_label(app.selectbox, 'Position category').set_value('active').run()
    edit(app, 'targets_positions_', {0: {'Category': 'Core category', 'within_bucket_target': 0.}})
    by_label(app.selectbox, 'Position category').set_value('core').run()
    rows = next(item.value for item in app.dataframe if 'within_bucket_target' in item.value)
    assert rows.name.tolist() == ['Invented Asset', 'Invented Asset']
    assert rows.within_bucket_target.tolist() == [100., 0.]
    key = next(k for k in app.session_state.filtered_state if k.startswith('targets_positions_') and k.endswith('_discard'))
    app.button(key=key).click().run()
    assert not app.exception
    rows = next(item.value for item in app.dataframe if 'within_bucket_target' in item.value)
    assert len(rows) == 3
    assert (workspace / 'holdings.csv').read_bytes() == original


def test_stale_target_save_keeps_external_update_and_draft(workspace, monkeypatch):
    import portfolio_app.allocation_ui as allocation_ui
    app = launch(workspace, tab='Rebalance', subtab='Targets')
    edit(app, 'targets_positions_', {0: {'within_bucket_target': 75.}})
    path = workspace / 'holdings.csv'
    external = path.read_text().replace('SYN-A,2,', 'SYN-A,3,')
    save = allocation_ui.patch_holdings

    def concurrent_save(*args, **kwargs):
        path.write_text(external)
        return save(*args, **kwargs)

    monkeypatch.setattr(allocation_ui, 'patch_holdings', concurrent_save)
    by_label(app.button, 'Save position targets').click().run()
    assert not app.exception
    assert app.error
    assert path.read_text() == external
    rows = next(item.value for item in app.dataframe if 'within_bucket_target' in item.value)
    assert rows.within_bucket_target.iloc[0] == 75.


def test_category_plan_respects_ancestor_protection_with_unrelated_missing_prices(workspace):
    path = workspace / 'allocation.yaml'
    config = yaml.safe_load(path.read_text())
    for category in config['buckets']:
        category['parent'] = 'parent'
    config['buckets'].insert(0, dict(id='parent', name='Protected parent', target=1., sell_protected=True))
    path.write_text(yaml.safe_dump(config))
    prices = workspace / 'demo_prices.json'
    data = json.loads(prices.read_text())
    del data['prices']['SYN-A']
    prices.write_text(json.dumps(data))
    app = launch(workspace, tab='Rebalance', subtab='Plan')
    by_label(app.button, 'Calculate plan').click().run()
    assert any('complete EUR valuations' in item.value for item in app.error)
    by_label(app.get('button_group'), 'Planning scope').set_value('Within a category').run()
    by_label(app.selectbox, 'Planning category').set_value('active').run()
    by_label(app.number_input, 'Allowed deviation (pp)').set_value(0.).run()
    by_label(app.button, 'Calculate plan').click().run()
    assert not app.exception
    assert any('feasible' in item.value for item in app.error)
    by_label(app.selectbox, 'Rebalancing mode').set_value('Allocate new money').run()
    by_label(app.button, 'Calculate plan').click().run()
    assert not app.exception and not app.error
    trades = next(item.value for item in app.dataframe if 'Action' in item.value)
    assert trades.Action.eq('Buy').all()
    before = {item.label: item.value for item in app.metric}
    by_label(app.selectbox, 'Compare categories within').set_value('parent').run()
    assert {item.label: item.value for item in app.metric} == before
    impact = next(item.value for item in app.dataframe if 'Category' in item.value and 'Gap (pp)' in item.value)
    assert impact['After %'].isna().all()
