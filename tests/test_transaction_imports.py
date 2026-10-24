from dataclasses import replace
from decimal import Decimal as D

import pytest

from portfolio_app.account_journal import load_journal, save_journal, Journal
from portfolio_app.consors_pdf import parse_consors_pdf, parse_consors_batch
from portfolio_app.history_commands import save_history, reconstruction_candidate
from portfolio_app.holdings import DataError
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import effective_holdings
from portfolio_app.transaction_imports import save_pdf_drafts, draft_problems
from test_consors_pdf import statement


def setup(tmp_path):
    (tmp_path/'holdings.csv').write_text('id,name,isin,shares,acquisition_price,acquisition_currency\ninvented,Invented,XX0000000000,2,53.5,EUR\n')
    snapshot = read_snapshot(tmp_path/'holdings.csv')
    journal, revision = load_journal(tmp_path)
    return snapshot, journal, revision


def test_import_retains_evidence_without_changing_holding_then_reconciles_once(tmp_path):
    snapshot,journal,revision = setup(tmp_path)
    draft = parse_consors_pdf(statement())
    save_pdf_drafts(tmp_path,snapshot.holdings.iloc[0],journal,revision,snapshot,[draft],charges_known=True)
    current,revision = load_journal(tmp_path)
    snapshot = read_snapshot(tmp_path/'holdings.csv')
    assert effective_holdings(snapshot.holdings,current.securities).iloc[0].shares == 2
    assert not current.securities[0].active
    candidate = reconstruction_candidate(current.securities[0], [current.securities[0].records[0].id], starts_at_zero=True)
    save_history(tmp_path,candidate,expected_journal_revision=revision,expected_holdings_revision=snapshot.revision,activate=True)
    after,_ = load_journal(tmp_path)
    assert effective_holdings(snapshot.holdings,after.securities).iloc[0].shares == 2
    assert effective_holdings(snapshot.holdings,after.securities).iloc[0].acquisition_price == 53.5
    assert current.securities[0].records[0].amount == D(100)
    assert current.securities[0].records[0].value_date == '2024-02-09'
    assert sum(c.amount for c in current.securities[0].records[0].charges) == D(7)


def test_repeated_file_and_duplicate_within_batch_cannot_be_saved_twice(tmp_path):
    snapshot,journal,revision = setup(tmp_path)
    data=statement(); draft=parse_consors_pdf(data)
    with pytest.raises(DataError,match='Duplicate'):
        save_pdf_drafts(tmp_path,snapshot.holdings.iloc[0],journal,revision,snapshot,[draft,draft])
    assert not load_journal(tmp_path)[0].securities
    save_pdf_drafts(tmp_path,snapshot.holdings.iloc[0],journal,revision,snapshot,[draft])
    current,revision=load_journal(tmp_path); snapshot=read_snapshot(tmp_path/'holdings.csv')
    with pytest.raises(DataError,match='Already imported'):
        save_pdf_drafts(tmp_path,snapshot.holdings.iloc[0],current,revision,snapshot,[draft])
    assert len(load_journal(tmp_path)[0].securities[0].records)==1


def test_similar_files_require_explicit_resolution(tmp_path):
    snapshot,journal,revision = setup(tmp_path)
    drafts=parse_consors_batch([statement(),statement()])
    with pytest.raises(DataError,match='Similar'):
        save_pdf_drafts(tmp_path,snapshot.holdings.iloc[0],journal,revision,snapshot,drafts)
    assert not load_journal(tmp_path)[0].securities
    save_pdf_drafts(tmp_path,snapshot.holdings.iloc[0],journal,revision,snapshot,drafts,allow_similar=True)
    events=load_journal(tmp_path)[0].securities[0].records
    assert len(events)==2 and all(e.charges_known for e in events)


def test_wrong_instrument_and_inconsistent_statement_rejected(tmp_path):
    snapshot,journal,revision=setup(tmp_path)
    draft=parse_consors_pdf(statement())
    wrong=replace(draft,isin='XX1111111111')
    assert 'Instrument does not match this position.' in draft_problems(wrong,snapshot.holdings.iloc[0],journal)
    for invalid in [wrong,parse_consors_pdf(statement(net='900,00'))]:
        with pytest.raises(DataError):
            save_pdf_drafts(tmp_path,snapshot.holdings.iloc[0],journal,revision,snapshot,[invalid])
    assert not load_journal(tmp_path)[0].securities


def test_stale_review_does_not_overwrite_other_work(tmp_path):
    snapshot,journal,revision=setup(tmp_path)
    save_journal(tmp_path,Journal(version=2),revision)
    with pytest.raises(DataError,match='changed'):
        save_pdf_drafts(tmp_path,snapshot.holdings.iloc[0],journal,revision,snapshot,[parse_consors_pdf(statement())])
    assert not load_journal(tmp_path)[0].securities


def test_statement_times_order_same_day_trades_independently_of_upload_order(tmp_path):
    from portfolio_app.security_history import selected_events
    snapshot,journal,revision=setup(tmp_path)
    buy=replace(parse_consors_pdf(statement()),trade_time='10:00:00')
    sale=replace(parse_consors_pdf(statement(sale=True,quantity='1',gross='50,00',net='43,00')),trade_time='11:00:00')
    book=save_pdf_drafts(tmp_path,snapshot.holdings.iloc[0],journal,revision,snapshot,[sale,buy],charges_known=True)
    candidate=reconstruction_candidate(book,[e.id for e in book.records],starts_at_zero=True)
    assert [e.kind for e in selected_events(candidate) if e.kind!='opening']==['buy','sell']


@pytest.mark.parametrize('confirmed', [False, True])
def test_fee_free_purchase_remains_unknown_until_confirmed(tmp_path, confirmed):
    from portfolio_app.transaction_import_ui import _charges
    from portfolio_app.money import format_money
    snapshot, journal, revision = setup(tmp_path)
    # Within the existing cent tolerance, but not exact enough for autozero.
    draft = parse_consors_pdf(statement(fees=False, net='100,01'))
    display = _charges(draft.fees, draft.gross, confirmed)
    assert display == (format_money(0, 'EUR') if confirmed else 'Not listed')
    book = save_pdf_drafts(tmp_path, snapshot.holdings.iloc[0], journal, revision,
                          snapshot, [draft], charges_known=confirmed)
    assert book.records[-1].amount == D(100)
    assert book.records[-1].charges == ()
    assert book.records[-1].charges_known is confirmed


def test_zero_statement_charges_are_saved_automatically_in_a_mixed_batch(tmp_path):
    snapshot, journal, revision = setup(tmp_path)
    free = parse_consors_pdf(statement(fees=False))
    charged = parse_consors_pdf(statement(quantity='3', gross='150,00', net='157,00'))
    book = save_pdf_drafts(tmp_path, snapshot.holdings.iloc[0], journal, revision,
                          snapshot, [free, charged])
    assert book.records[0].charges_known and not book.records[0].charges
    assert 'Zero statement charges' in book.records[0].note
    assert book.records[1].charges_known
    assert sum(c.amount for c in book.records[1].charges) == D(7)
