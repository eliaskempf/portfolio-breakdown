"""Synthetic category edits conserve holdings and recover interrupted saves."""
from dataclasses import replace
import json

import pytest

from portfolio_app.portfolio.allocation import Allocation, Bucket, allocation_content, load_allocation
from portfolio_app.portfolio.category_commands import create_category, publish_category_edit, INTENT, save_category_settings
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.workspace.storage import revision
from portfolio_app.workspace.workspace_lock import workspace_lock


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path/'holdings.csv'
    path.write_text('position_key,id,name,shares,bucket_id,within_bucket_target\np1,a,Invented A,2,parent,.2\np2,b,Invented B,3,parent,.3\np3,c,Invented C,4,parent,.5\n')
    config = Allocation((Bucket('parent','Invented parent',target=1.),))
    (tmp_path/'allocation.yaml').write_text(allocation_content(config))
    return tmp_path, config


def test_create_category_and_priority_settings_preserve_financial_facts(workspace):
    directory, config = workspace
    before = read_snapshot(directory/'holdings.csv')
    key = create_category(directory, config, ['p1','p2'], 'Invented shared target', .5,
                          holdings_revision=before.revision, allocation_revision=revision(directory/'allocation.yaml'))
    saved = read_snapshot(directory/'holdings.csv')
    loaded = load_allocation(directory/'allocation.yaml', saved.holdings)
    assert loaded.version == 3 and loaded.flexible(key)
    assert saved.holdings.shares.tolist() == before.holdings.shares.tolist()
    assert saved.holdings.within_bucket_target.tolist() == [.2,.3,.5]
    assert saved.holdings.bucket_id.tolist() == [key,key,'parent']
    save_category_settings(directory, loaded, key, 'flexible', ['p2','p1'], ['p1','p2'],
                           holdings_revision=saved.revision, allocation_revision=revision(directory/'allocation.yaml'))
    loaded = load_allocation(directory/'allocation.yaml', saved.holdings)
    assert next(b for b in loaded.buckets if b.id == key).buy_priority == ('p2','p1')
    with pytest.raises(DataError, match='changed'):
        create_category(directory, config, ['p1','p2'], 'Stale edit', .5,
                        holdings_revision=before.revision, allocation_revision=None)


def test_failed_pair_save_rolls_back_both_documents(workspace, monkeypatch):
    from portfolio_app.portfolio import category_commands
    directory, config = workspace
    originals = {name:(directory/name).read_bytes() for name in ('holdings.csv','allocation.yaml')}
    real = category_commands.save_document
    def fail(path, content, expected_revision):
        if path.name == 'allocation.yaml':
            raise OSError('invented interruption')
        return real(path, content, expected_revision)
    monkeypatch.setattr(category_commands, 'save_document', fail)
    with pytest.raises(OSError, match='invented interruption'):
        create_category(directory, config, ['p1','p2'], 'Invented shared target', .5,
                        holdings_revision=revision(directory/'holdings.csv'), allocation_revision=revision(directory/'allocation.yaml'))
    assert not (directory/INTENT).exists()
    assert {name:(directory/name).read_bytes() for name in originals} == originals


def test_next_workspace_access_recovers_pending_category_save(workspace):
    directory, config = workspace
    originals = {name:(directory/name).read_text() for name in ('holdings.csv','allocation.yaml')}
    documents = {name: {'before':content, 'after':content+'\n'} for name,content in originals.items()}
    (directory/INTENT).write_text(json.dumps({'version':1, 'documents':documents}))
    (directory/'holdings.csv').write_text(documents['holdings.csv']['after'])
    read_snapshot(directory/'holdings.csv')
    assert not (directory/INTENT).exists()
    assert {name:(directory/name).read_text() for name in originals} == originals


def test_moving_members_removes_stale_priorities_atomically(workspace):
    from portfolio_app.portfolio.category_commands import move_category_positions
    directory, config = workspace
    key = create_category(directory, config, ['p1','p2'], 'Invented group', .5,
                          holdings_revision=revision(directory/'holdings.csv'), allocation_revision=revision(directory/'allocation.yaml'))
    config = load_allocation(directory/'allocation.yaml')
    save_category_settings(directory, config, key, 'flexible', ['p1','p2'], ['p2','p1'],
                           holdings_revision=revision(directory/'holdings.csv'), allocation_revision=revision(directory/'allocation.yaml'))
    config = load_allocation(directory/'allocation.yaml')
    move_category_positions(directory, config, ['p1'], 'parent', holdings_revision=revision(directory/'holdings.csv'), allocation_revision=revision(directory/'allocation.yaml'))
    positions = read_snapshot(directory/'holdings.csv').holdings
    config = load_allocation(directory/'allocation.yaml', positions)
    assert next(b for b in config.buckets if b.id == key).buy_priority == ('p2',)
    assert positions.loc[positions.position_id.eq('p1'), 'bucket_id'].item() == 'parent'


def test_regular_position_editor_updates_category_priorities(workspace):
    from portfolio_app.portfolio.positions import save_position
    directory, config = workspace
    key = create_category(directory, config, ['p1','p2'], 'Invented group', .5,
                          holdings_revision=revision(directory/'holdings.csv'), allocation_revision=revision(directory/'allocation.yaml'))
    config = load_allocation(directory/'allocation.yaml')
    save_category_settings(directory, config, key, 'flexible', ['p1','p2'], ['p2','p1'],
                           holdings_revision=revision(directory/'holdings.csv'), allocation_revision=revision(directory/'allocation.yaml'))
    save_position(directory/'holdings.csv', {'bucket_id':'parent'}, position_id='p1', expected_revision=revision(directory/'holdings.csv'))
    snapshot = read_snapshot(directory/'holdings.csv')
    loaded = load_allocation(directory/'allocation.yaml', snapshot.holdings)
    group = next(b for b in loaded.buckets if b.id == key)
    assert group.buy_priority == group.sell_priority == ('p2',)
    assert snapshot.holdings.shares.tolist() == [2.,3.,4.]
    assert snapshot.holdings.loc[snapshot.holdings.position_id.eq('p1'),'bucket_id'].item() == 'parent'
