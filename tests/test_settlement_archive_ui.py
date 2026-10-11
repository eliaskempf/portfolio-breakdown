"""Synthetic source revisions remain separate from recorded settlements."""
from dataclasses import replace

from streamlit.testing.v1 import AppTest

from portfolio_app import account_journal as ledger
from portfolio_app.settlement_archive import SettlementArchive
from portfolio_app.settlement_archive_ui import settlement_notices
from portfolio_app.settlement_collection import scan_dates, read_status
from test_settlement_collection import workspace, quote
from test_ui import by_label


def test_review_source_revision_retains_journal_and_unread_identity(tmp_path):
    journal,contract=workspace(tmp_path)
    journal=ledger.append_event(journal,ledger.Event('settlement','2026-01-05','settlement',position_id='p',price=1010))
    _,revision=ledger.load_journal(tmp_path)
    ledger.save_journal(tmp_path,journal,revision)
    before=(tmp_path/ledger.FILE).read_bytes()
    archive=SettlementArchive(tmp_path)
    archive.record(contract,quote(contract,'2026-01-05'))
    assert settlement_notices(tmp_path,journal)==([],())
    archive.record(contract,quote(contract,'2026-01-05',1020))
    messages,identity=settlement_notices(tmp_path,journal)
    assert len(messages)==1 and identity
    archive.record(contract,replace(quote(contract,'2026-01-05',1020),observed_at='2026-01-08T12:00:00+00:00'))
    assert settlement_notices(tmp_path,journal)[1]==identity
    app=AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.account_journal import load_journal
from portfolio_app.settlement_archive_ui import render_history_checks
root=Path({str(tmp_path)!r})
journal,_=load_journal(root)
render_history_checks(root,journal,['p'],key='test_')
''').run()
    assert not app.exception
    assert app.dataframe[0].value.Status.tolist()==['Source changed']
    by_label(app.button,'Use retrieved price in archive').click().run()
    assert not app.exception
    assert app.dataframe[0].value.Status.tolist()==['Recorded settlement differs']
    assert archive.load(contract,'2026-01-05').price==1020
    assert len(archive.versions(contract,'2026-01-05'))==2
    assert settlement_notices(tmp_path,journal)[1]!=identity
    assert (tmp_path/ledger.FILE).read_bytes()==before


def test_manual_scan_resolves_persisted_gap_without_resetting_automatic_schedule(tmp_path):
    journal,contract=workspace(tmp_path)
    from portfolio_app.holdings import DataError
    def unavailable(*_):raise DataError('Invented unavailable date')
    scan_dates(tmp_path,contract,['2026-01-05'],fetch=unavailable)
    assert 'unavailable' in settlement_notices(tmp_path,journal)[0][0]
    scan_dates(tmp_path,contract,['2026-01-05'],fetch=quote)
    assert settlement_notices(tmp_path,journal)==([],())
    assert read_status(tmp_path)['contracts'][contract.id]['dates']['2026-01-05']['status']=='checked'
