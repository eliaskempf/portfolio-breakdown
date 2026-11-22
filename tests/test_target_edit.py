"""Scoped target commands and dialogs, using deliberately invented portfolios."""
from dataclasses import replace

import pandas as pd
import pytest

from portfolio_app.exposure.exposure_targets import ExposureTargets, load_exposure_targets, save_exposure_targets
from portfolio_app.portfolio.allocation import Allocation, Bucket, allocation_content, load_allocation
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.target_edit import prepare_target_edit, save_target_edit
from test_flexible_category_ui import workspace  # noqa: F401
from test_mixed_exposure_targets import exposure_workspace, mixed  # noqa: F401
from test_ui import launch, by_label


def test_capital_save_preserves_members_priorities_and_other_fields(workspace):
    config = load_allocation(workspace/'allocation.yaml')
    before = pd.read_csv(workspace/'holdings.csv')
    draft = prepare_target_edit(workspace,config,'group')
    assert draft.scope == 'sleeve' and draft.requested_scope == 'group'
    assert draft.rows.identity.tolist() == ['group','p3']
    save_target_edit(draft,draft.rows.assign(Target=[60.,40.]))
    saved = load_allocation(workspace/'allocation.yaml')
    assert saved.buckets[0] == config.buckets[0]
    assert saved.buckets[1] == replace(config.buckets[1],target=.6)
    after = pd.read_csv(workspace/'holdings.csv')
    expected = before.copy()
    expected.loc[expected.position_key.eq('p3'),'within_bucket_target'] = .4
    pd.testing.assert_frame_equal(after,expected)


@pytest.mark.parametrize('targets', [[60,50],[-10,110],[float('nan'),100]])
def test_invalid_split_does_not_write(workspace,targets):
    paths = [workspace/'holdings.csv',workspace/'allocation.yaml']
    before = [p.read_bytes() for p in paths]
    draft = prepare_target_edit(workspace,load_allocation(paths[1]),'sleeve')
    with pytest.raises(DataError):
        save_target_edit(draft,draft.rows.assign(Target=targets))
    assert before == [p.read_bytes() for p in paths]


def test_stale_edit_cannot_overwrite_new_configuration(workspace):
    path = workspace/'allocation.yaml'
    draft = prepare_target_edit(workspace,load_allocation(path),'sleeve')
    path.write_text(path.read_text()+'\n# another edit\n')
    newer = path.read_bytes()
    with pytest.raises(DataError,match='changed while editing'):
        save_target_edit(draft,draft.rows.assign(Target=[60,40]))
    assert path.read_bytes() == newer


def test_mixed_exposure_edit_keeps_capital_and_multiple(exposure_workspace):
    config, _, policy, result = mixed()
    paths = [exposure_workspace/name for name in ('holdings.csv','allocation.yaml')]
    before = [p.read_bytes() for p in paths]
    draft = prepare_target_edit(exposure_workspace,config,'group',market=True,exposure_result=result)
    assert draft.scope == 'sleeve' and not draft.edits_multiple
    save_target_edit(draft,draft.rows.assign(Target=[65,35]),multiple=9.)
    saved = load_exposure_targets(exposure_workspace,config)[0]
    assert saved == replace(policy,composition={'sleeve':1.,'group':.65},members={'sleeve':{'p3':.35}})
    assert before == [p.read_bytes() for p in paths]
    root = prepare_target_edit(exposure_workspace,config,'',market=True,exposure_result=result)
    save_target_edit(root,root.rows,multiple=1.25)
    assert load_exposure_targets(exposure_workspace,config)[0] == replace(saved,multiple=1.25)


def test_legacy_leaf_edit_preserves_other_scopes_and_nested_split(workspace):
    config = Allocation((Bucket('sleeve','Sleeve',target=.6),Bucket('group','Group','sleeve',.5),
        Bucket('a','A','group',.5),Bucket('b','B','group',.5),Bucket('c','C','sleeve',.5),Bucket('other','Other',target=.4)))
    rows = pd.read_csv(workspace/'holdings.csv')
    rows['bucket_id'] = ['a','b','c']
    rows['within_bucket_target'] = 1.
    rows.to_csv(workspace/'holdings.csv',index=False)
    (workspace/'allocation.yaml').write_text(allocation_content(config))
    policy = ExposureTargets(1.2,'composition',{'a':.1,'b':.2,'c':.3,'other':.4},version=2,universe='market')
    save_exposure_targets(workspace,policy,config,None)
    draft = prepare_target_edit(workspace,config,'sleeve',market=True)
    save_target_edit(draft,draft.rows.assign(Target=[75,25]))
    saved = load_exposure_targets(workspace,config)[0]
    assert saved.composition == pytest.approx({'a':.15,'b':.30,'c':.15,'other':.4})
    assert saved.version == 2 and saved.weights == 'leaves'


def test_context_dialog_cancel_save_and_full_editor(workspace):
    app = launch(workspace,tab='Overview')
    by_label(app.selectbox,'Category').set_value('sleeve').run()
    path = workspace/'allocation.yaml'
    before = path.read_bytes()
    app.button(key='overview_capital_targets').click().run()
    assert not app.exception and not app.error
    assert not any(x.label == 'Allocation category' for x in app.selectbox)
    app.button(key='overview_target_cancel').click().run()
    assert path.read_bytes() == before
    app.button(key='overview_capital_targets').click().run()
    key = next(key for key in app.session_state.to_dict() if key.startswith('overview_target_editor_'))
    app.session_state[key] = dict(edited_rows={0:{'Target':1.},1:{'Target':1.}},added_rows=[],deleted_rows=[])
    app.run()
    app.button(key='overview_target_save').click().run()
    assert any('must total 100%' in error.value for error in app.error)
    assert any('needs 100%' in note.value for note in app.caption)
    assert path.read_bytes() == before
    app.session_state[key] = dict(edited_rows={0:{'Target':60.},1:{'Target':40.}},added_rows=[],deleted_rows=[])
    app.run()
    assert app.button(key='overview_target_manage').label == 'Discard edits & manage targets'
    app.button(key='overview_target_save').click().run()
    assert not app.exception and not app.error
    assert load_allocation(path).buckets[1].target == .6
    assert 'overview_target_dialog' not in app.session_state
    app.button(key='overview_capital_targets').click().run()
    app.button(key='overview_target_manage').click().run()
    assert not app.exception
    assert by_label(app.selectbox,'Allocation category')


@pytest.mark.parametrize('origin', ['Overview','Rebalance'])
def test_legacy_whole_portfolio_targets_do_not_enable_categories(tmp_path, origin):
    from portfolio_app.portfolio.positions import read_snapshot
    (tmp_path/'holdings.csv').write_text(
        'id,name,ticker,shares,portfolio,target_allocation\na,Invented A,AAA,1,One,.3\nb,Invented B,BBB,2,Two,.7\n')
    (tmp_path/'demo_prices.json').write_text('{"prices": {}, "fx": {}}')
    app = launch(tmp_path,tab=origin,subtab='Targets' if origin == 'Rebalance' else None)
    key = 'overview_capital_targets' if origin == 'Overview' else 'targets_edit_capital'
    app.button(key=key).click().run()
    assert app.session_state['overview_target_dialog']['draft'].rows.Target.tolist() == [30.,70.]
    editor = next(k for k in app.session_state.to_dict() if k.startswith('overview_target_editor_'))
    app.session_state[editor] = dict(edited_rows={0:{'Target':25.},1:{'Target':75.}},added_rows=[],deleted_rows=[])
    app.button(key='overview_target_save').click().run()
    assert not app.exception and not app.error
    assert read_snapshot(tmp_path/'holdings.csv').holdings.target_allocation.tolist() == [.25,.75]
    assert not (tmp_path/'allocation.yaml').exists()


def test_minimal_legacy_csv_supplies_missing_target_and_account_defaults(tmp_path):
    (tmp_path/'holdings.csv').write_text('id,name,shares\na,Invented A,1\n')
    draft = prepare_target_edit(tmp_path,None,'')
    assert draft.rows.Account.tolist() == ['']
    assert draft.rows.Target.isna().all()
    save_target_edit(draft,draft.rows.assign(Target=[100.]))
    assert pd.read_csv(tmp_path/'holdings.csv').target_allocation.tolist() == [1.]
    assert not (tmp_path/'allocation.yaml').exists()


def test_unset_category_target_column_is_blank_until_saved(tmp_path):
    path = tmp_path/'holdings.csv'
    path.write_text('id,name,shares,bucket_id\na,Invented A,1,held\n')
    config = Allocation((Bucket('held','Held',target=1.),))
    (tmp_path/'allocation.yaml').write_text(allocation_content(config))
    draft = prepare_target_edit(tmp_path,config,'held')
    assert draft.rows.Target.isna().all()
    save_target_edit(draft,draft.rows.assign(Target=[100.]))
    assert pd.read_csv(path).within_bucket_target.tolist() == [1.]
    assert load_allocation(tmp_path/'allocation.yaml').version == 2


def test_equity_only_leaf_edit_preserves_universe_version_and_zero_branch(workspace):
    config = Allocation((Bucket('sleeve','Sleeve',target=.6),Bucket('group','Group','sleeve',.5),
        Bucket('a','One','group',.5),Bucket('b','Two','group',.5),Bucket('c','Other',target=.4)))
    path = workspace/'holdings.csv'
    rows = pd.read_csv(path)
    rows['bucket_id'] = ['a','b','c']
    rows.to_csv(path,index=False)
    (workspace/'allocation.yaml').write_text(allocation_content(config))
    before = path.read_bytes()
    saved = ExposureTargets(1.2,'composition',{'c':1.})
    save_exposure_targets(workspace,saved,config,None)
    with pytest.raises(DataError,match='equity only'):
        prepare_target_edit(workspace,config,'',market=True)
    draft = prepare_target_edit(workspace,config,'',market=True,policy_settings=True,whole_leaf_split=True)
    edited = draft.rows.copy()
    edited['Target'] = edited.identity.map({'a':20.,'b':30.,'c':50.})
    save_target_edit(draft,edited,multiple=1.25)
    result = load_exposure_targets(workspace,config)[0]
    assert result == replace(saved,composition={'a':.2,'b':.3,'c':.5},multiple=1.25)
    assert path.read_bytes() == before


def test_changed_exposure_source_rejects_open_scope(exposure_workspace):
    config, _, policy, result = mixed()
    draft = prepare_target_edit(exposure_workspace,config,'sleeve',market=True,exposure_result=result)
    (exposure_workspace/'futures-sandbox.json').write_text('{}')
    with pytest.raises(DataError,match='changed while editing'):
        save_target_edit(draft,draft.rows)
    assert load_exposure_targets(exposure_workspace,config)[0] == policy
