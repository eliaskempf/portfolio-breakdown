"""Synthetic asset history setup, persistence and rendering."""
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.account_journal import load_journal
from portfolio_app.asset_history_store import load_settings, save_settings, evidence_rows
from portfolio_app.history import HistoryResult
from portfolio_app.holdings import DataError
from portfolio_app.transaction_import_commands import prepare_positions, save_transaction_import
from test_asset_history import row


_TEST_MAIN = sys.modules['__main__']


@pytest.fixture(autouse=True)
def restore_test_runner_main():
    # AppTest installs its generated script as __main__. Leaving that module
    # behind makes later multiprocessing spawn execute an incomplete script.
    yield
    sys.modules['__main__'] = _TEST_MAIN


def setup(directory, *, active=False):
    snapshot=prepare_positions(directory,[dict(position_key='aur',id='AUR',name='Aurora',account='Combined',instrument_type='crypto')],expected_holdings_revision=None)
    reward=row('reward','reward',valuation_amount='20',valuation_currency='EUR',valuation_quality='estimated',valuation_source='Invented valuation')
    opening=row('opening','opening',timestamp_utc='2024-01-01T00:00:00Z',effective_date='2024-01-01',quantity='10')
    rows=[opening,reward] if active else [reward]
    save_transaction_import(directory,rows,{('Wallet','AUR'):'aur'} if active else {},expected_journal_revision=None,
        expected_holdings_revision=snapshot.revision,selected_ids=[r.identity for r in rows] if active else (),
        allow_incomplete=True,accept_reconciliation=active,comparison_date='2024-01-03' if active else '')
    journal,revision=load_journal(directory)
    raw=dict(version=2,method='forward-daily-dietz-v1',source_id='invented-asset-ledger',owned_accounts=['Wallet','Stake'],
        currency='EUR',anchors={'aur':dict(asset='AUR',ticker='AUR-EUR')})
    save_settings(directory,raw,expected_revision=None,expected_journal_revision=revision,expected_holdings_revision=snapshot.revision)
    return snapshot,raw,revision


def script(directory):
    from pathlib import Path
    from portfolio_app.account_journal import load_journal
    from portfolio_app.history_observations import HistoryScope
    from portfolio_app.history_ui import render_history_results
    directory=Path(directory)
    render_history_results(directory,load_journal(directory)[0],HistoryScope(('aur',)),key='synthetic_',automatic=True)


def test_asset_history_metrics_and_views(monkeypatch,tmp_path):
    setup(tmp_path,active=True)
    class StaticHistory:
        def get(self,*args,**kwargs):
            return HistoryResult(('2024-01-01','2024-01-02','2024-01-03'),(10.,10.,10.),'EUR','cached')
        def pending(self,*args):return False
    monkeypatch.setattr('portfolio_app.market_data.history_for',lambda _:StaticHistory())
    app=AppTest.from_function(script,args=(str(tmp_path),)).run()
    assert not app.exception
    metrics={m.label:m.value for m in app.metric}
    assert '20' in metrics['Known gain'] and '20' in metrics['Recorded income']
    assert '20' in metrics['Approx. return']
    assert len(app.get('plotly_chart'))==1
    for view in ('Gain','Return','Units'):
        app.segmented_control(key='synthetic_asset_measure').set_value(view).run()
        assert not app.exception and len(app.get('plotly_chart'))==1


def test_settings_check_revisions_and_survive_backup(tmp_path):
    snapshot,raw,revision=setup(tmp_path)
    settings,current=load_settings(tmp_path)
    assert settings.anchors['aur']=={'asset':'AUR','ticker':'AUR-EUR'}
    with pytest.raises(DataError,match='changed'):
        save_settings(tmp_path,raw,expected_revision=current,expected_journal_revision='stale',expected_holdings_revision=snapshot.revision)
    from portfolio_app.backup import create_backup, inspect_backup
    content=create_backup(tmp_path)
    draft=inspect_backup(content)
    try:assert load_settings(draft.workspace)[0]==settings
    finally:draft.close()


def test_reviewed_corrections_replace_history_instead_of_adding(tmp_path):
    snapshot,_,revision=setup(tmp_path)
    changed=row('reward','reward',quantity='3')
    save_transaction_import(tmp_path,[changed],{},expected_journal_revision=revision,
        expected_holdings_revision=snapshot.revision,correction_ids=[changed.identity])
    journal,_=load_journal(tmp_path);settings,_=load_settings(tmp_path)
    selected=evidence_rows(journal,settings)
    assert len(selected)==1 and selected[0].facts['quantity']=='3'


def test_invalid_settings_rejected_on_workspace_validation(tmp_path):
    setup(tmp_path)
    path=Path(tmp_path)/'asset-performance.json'
    path.write_text('{"version":99}')
    from portfolio_app.workspace import validate_workspace
    with pytest.raises(DataError,match='asset performance'):
        validate_workspace(tmp_path)


def setup_script(directory):
    from pathlib import Path
    from portfolio_app.account_journal import load_journal
    from portfolio_app.positions import read_snapshot
    from portfolio_app.asset_history_store import load_settings,evidence_rows
    from portfolio_app.asset_history_setup_ui import render_asset_history_setup
    directory=Path(directory);journal,revision=load_journal(directory)
    settings,_=load_settings(directory)
    render_asset_history_setup(directory,journal,revision,read_snapshot(directory/'holdings.csv'),evidence_rows(journal,settings))


def test_setup_does_not_copy_current_units_into_history_settings(tmp_path):
    setup(tmp_path)
    app=AppTest.from_function(setup_script,args=(str(tmp_path),)).run()
    assert not app.exception
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label=='Enable asset history').click().run()
    assert not app.exception
    settings,_=load_settings(tmp_path)
    assert settings.anchor_date=='' and 'quantity' not in settings.anchors['aur']


def detail_script(directory):
    from pathlib import Path
    from portfolio_app.positions import read_snapshot
    from portfolio_app.position_detail import render_position_detail
    directory=Path(directory)
    render_position_detail(read_snapshot(directory/'holdings.csv').holdings.iloc[0],directory)


def test_position_opens_approximate_history_without_activating_fifo(monkeypatch,tmp_path):
    setup(tmp_path)
    class StaticHistory:
        def get(self,*args,**kwargs):return HistoryResult(('2024-01-01','2024-01-02','2024-01-03'),(10.,10.,10.),'EUR','cached')
        def pending(self,*args):return False
    monkeypatch.setattr('portfolio_app.market_data.history_for',lambda _:StaticHistory())
    app=AppTest.from_function(detail_script,args=(str(tmp_path),)).run()
    assert not app.exception
    assert app.segmented_control(key='position_edit_detail_view_aur').value=='History'
    assert any('Opening inventory unknown' in c.value for c in app.caption)
    assert not any(m.label=='Approx. return' for m in app.metric)
    assert not load_journal(tmp_path)[0].securities


def test_legacy_settings_survive_but_cannot_route_backwards_history(tmp_path):
    import json
    from portfolio_app.asset_history_store import settings_for_scope
    from portfolio_app.history_observations import HistoryScope
    snapshot,_,revision=setup(tmp_path)
    legacy=dict(version=1,method='closing-balance-daily-dietz-v1',source_id='invented-asset-ledger',
        owned_accounts=['Wallet'],anchor_date='2024-01-03',currency='EUR',
        anchors={'aur':dict(asset='AUR',quantity='91',ticker='AUR-EUR')})
    path=tmp_path/'asset-performance.json'
    path.write_text(json.dumps(legacy))
    content=path.read_bytes()
    assert load_settings(tmp_path)[0].legacy
    assert settings_for_scope(tmp_path,HistoryScope(('aur',))) is None
    assert path.read_bytes()==content
    with pytest.raises(DataError,match='retired'):
        save_settings(tmp_path,legacy,expected_revision=load_settings(tmp_path)[1],
            expected_journal_revision=revision,expected_holdings_revision=snapshot.revision)
    app=AppTest.from_function(setup_script,args=(str(tmp_path),)).run()
    assert not app.exception
    assert any('retired' in c.value for c in app.caption)
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label=='Enable asset history').click().run()
    assert not app.exception and not load_settings(tmp_path)[0].legacy
    assert not load_journal(tmp_path)[0].securities
