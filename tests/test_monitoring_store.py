"""Private synthetic monitoring persistence and episode lifecycle."""
from dataclasses import replace
import json

import pytest

from portfolio_app.holdings import DataError
from portfolio_app.monitoring import Evaluation
from portfolio_app.monitoring_store import (save_rule, delete_rule, load_monitoring, record_evaluations, mark_events)
from test_monitoring import rule, NOW


def test_episode_dedup_acknowledgment_escalation_and_recovery(tmp_path):
    r = rule()
    save_rule(tmp_path,r)
    warning = Evaluation('2026-10-09',-1.5,100,98.5,'approaching')
    assert record_evaluations(tmp_path,r,[warning],now=NOW)
    assert not record_evaluations(tmp_path,r,[warning],now=NOW)
    e = load_monitoring(tmp_path)[0]['events'][0]
    mark_events(tmp_path,[e['id']],acknowledge=True)
    trigger = replace(warning,value=-2.,condition='triggered')
    record_evaluations(tmp_path,r,[trigger],now=NOW)
    record_evaluations(tmp_path,r,[warning,trigger],now=NOW)
    doc,_ = load_monitoring(tmp_path)
    assert len(doc['events']) == 2
    record_evaluations(tmp_path,r,[Evaluation('',quality='stale')],now=NOW)
    state = load_monitoring(tmp_path)[0]['states'][r.id]
    assert state['condition'] == 'triggered' and state['quality'] == 'stale'
    record_evaluations(tmp_path,r,[replace(warning,value=2.,condition='normal'),trigger],now=NOW)
    assert len(load_monitoring(tmp_path)[0]['events']) == 3
    assert load_monitoring(tmp_path)[0]['events'][0]['resolved']


def test_rule_revision_conflicts_pause_delete_and_history_are_preserved(tmp_path):
    r=rule()
    save_rule(tmp_path,r)
    record_evaluations(tmp_path,r,[Evaluation('2026-10-09',-3.,condition='triggered')],now=NOW)
    with pytest.raises(DataError,match='another editor'):
        save_rule(tmp_path,replace(r,revision=2))
    newer=replace(r,revision=2,enabled=False)
    save_rule(tmp_path,newer,expected_rule_revision=1)
    assert not record_evaluations(tmp_path,r,[Evaluation('2026-10-12',-5.,condition='triggered')],now=NOW)
    assert not record_evaluations(tmp_path,newer,[Evaluation('2026-10-12',-5.,condition='triggered')],now=NOW)
    delete_rule(tmp_path,r.id,2)
    doc,_=load_monitoring(tmp_path)
    assert not doc['rules'] and len(doc['definitions']) == 2
    assert len(doc['observations']) == 1 and doc['events'][0]['resolved']


def test_corrupt_monitoring_is_retained(tmp_path):
    path=tmp_path/'monitoring.json'
    path.write_text('{bad')
    with pytest.raises(DataError):load_monitoring(tmp_path)
    assert path.read_text() == '{bad'


def test_workspace_isolation_and_concurrent_writes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    a,b=tmp_path/'a',tmp_path/'b'
    r=rule()
    save_rule(a,r);save_rule(b,r)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _:record_evaluations(a,r,[Evaluation('2026-10-09',-3.,condition='triggered')],now=NOW),range(4)))
    assert len(load_monitoring(a)[0]['events']) == 1
    assert load_monitoring(b)[0]['events'] == []


def test_monitoring_is_included_in_portable_backups_and_restore_validation(tmp_path):
    from portfolio_app.demo import create_demo_data
    from portfolio_app.backup import create_backup, inspect_backup
    root=tmp_path/'synthetic'
    create_demo_data(root)
    save_rule(root,rule())
    record_evaluations(root,rule(),[Evaluation('2026-10-09',-3.,condition='triggered')],now=NOW)
    draft=inspect_backup(create_backup(root))
    try:
        assert 'monitoring.json' in draft.digests
    finally:
        draft.close()


@pytest.mark.parametrize('section', ['observations','events','states'])
def test_malformed_saved_evaluations_are_rejected_before_rendering(tmp_path,section):
    r=rule()
    save_rule(tmp_path,r)
    record_evaluations(tmp_path,r,[Evaluation('2026-10-09',-3.,condition='triggered')],now=NOW)
    path=tmp_path/'monitoring.json'
    raw=json.loads(path.read_text())
    if section=='states':
        raw['states'][r.id]['latest']['quality']='invented-invalid-state'
    else:
        raw[section][0]['evaluation']={}
    content=json.dumps(raw)
    path.write_text(content)
    with pytest.raises(DataError):load_monitoring(tmp_path)
    assert path.read_text()==content


def test_not_applicable_clears_episode_without_mistaking_missing_data_for_recovery(tmp_path):
    r=rule()
    save_rule(tmp_path,r)
    record_evaluations(tmp_path,r,[Evaluation('2026-10-09',60,condition='triggered')],now=NOW)
    record_evaluations(tmp_path,r,[Evaluation('2026-10-10',quality='unavailable')],now=NOW)
    assert not load_monitoring(tmp_path)[0]['events'][0]['resolved']
    record_evaluations(tmp_path,r,[Evaluation('2026-10-11',quality='not_applicable',note='No debt or backing assets.')],now=NOW)
    doc,_=load_monitoring(tmp_path)
    assert doc['states'][r.id]['condition']=='normal' and doc['events'][0]['resolved']
