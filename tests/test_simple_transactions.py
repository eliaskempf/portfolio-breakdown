"""Invented transaction parity, lifecycle, estimates and publication recovery."""
from dataclasses import replace
from decimal import Decimal as D
import json

import pytest

from portfolio_app.account_journal import Journal, load_journal, journal_from_dict, journal_to_dict
from portfolio_app.csv_transaction_imports import prepare_import, event_for
from portfolio_app.history_commands import correct_transaction, remove_transactions, save_history
from portfolio_app.history_observations import HistoryScope, Observation, ObservationStore, CoverageReview
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.personal_performance import calculate_history
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import SecurityEvent, SecurityBook, replay_securities, history_fingerprint
from portfolio_app.transaction_csv import read_csv
from portfolio_app.transaction_import_commands import save_transaction_import, positions_content

HEADER = 'reference,date,type,asset,quantity,amount,currency'
MAPPING = {('Portfolio','AUR'):'aur', ('Portfolio','ORB'):'orb'}
ADDITIONS = [dict(position_key=p, id=p, name=p.title(), account='Portfolio', instrument_type='crypto') for p in ('aur','orb')]


def rows(text, extra=''):
    return read_csv(HEADER+extra+'\n'+text)


def holdings():
    return parse_holdings('position_key,id,name,shares,account\naur,aur,Aurora,0,Portfolio\norb,orb,Orbit,0,Portfolio\n')


def applied(items):
    return prepare_import(Journal(), holdings(), items, MAPPING,
        selected_ids=[r.identity for r in items], starts_at_zero=True, allow_incomplete=True).journal


def test_simple_format_dates_order_and_sparse_roundtrip():
    items = rows('b,2024-01-02,buy,AUR,10,100,EUR\ns,2024-01-02,sell,AUR,2,25,EUR\n')
    assert not any(r.issues for r in items)
    assert [r.facts['timestamp_utc'] for r in items] == ['', '']
    assert [event_for(r,MAPPING).sequence for r in items] == [0,1]
    journal = applied(items)
    assert replay_securities(journal.securities).positions['aur'].quantity == 8
    wire = journal_to_dict(journal)
    assert wire['version'] == 5 and 'timestamp_utc' not in wire['transaction_imports'][0]['facts']
    assert journal_from_dict(wire) == journal


@pytest.mark.parametrize('source', ['manual','consors_pdf:synthetic','transaction_csv_v3'])
def test_entry_source_does_not_change_calculations_or_rows(source):
    from portfolio_app.history_ui import _transaction_table
    items = rows('b,2024-01-02,buy,AUR,10,100,EUR\ns,2024-01-03,sell,AUR,2,25,EUR\n')
    original = applied(items)
    journal = replace(original, securities=tuple(replace(b, records=tuple(
        replace(e, source=source) if e.kind != 'opening' else e for e in b.records)) for b in original.securities))
    scope=HistoryScope(('aur',))
    store=ObservationStore((Observation('p1','price','aur','2024-01-02',10,'EUR'),
                            Observation('p2','price','aur','2024-01-03',12,'EUR')),
                           (CoverageReview('r',scope,'2024-01-01','2024-01-03',history_fingerprint(journal)),))
    result=calculate_history(journal,scope,store,'2024-01-01','2024-01-03')
    assert result.total_gain == 21 and result.money_weighted is not None and result.time_weighted is not None
    book=journal.securities[0]
    expected=_transaction_table(list(original.securities[0].records), original.securities[0])
    assert _transaction_table(list(book.records),book).equals(expected)
    edited=correct_transaction(book,items[0].identity,amount=D(110))
    assert edited.records[-1].amount == 110 and edited.records[-1].replaces == items[0].identity
    removed=remove_transactions(book,[items[-1].identity])
    assert items[-1].identity not in removed.active_ids


def test_pdf_csv_and_manual_normalize_same_purchase():
    from test_consors_pdf import statement
    from portfolio_app.consors_pdf import parse_consors_pdf
    from portfolio_app.transaction_imports import as_event
    pdf=as_event(parse_consors_pdf(statement(fees=False)),charges_known=True)
    csv=event_for(rows('p,2024-02-07,buy,AUR,2,100,EUR\n')[0],MAPPING)
    manual=SecurityEvent('manual','buy','2024-02-07',quantity=2,amount=100,currency='EUR',charges_known=True)
    states=[]
    for e in (pdf,csv,manual):
        opening=SecurityEvent('opening','opening','2024-02-06')
        book=SecurityBook('aur','aur',('0','',''),records=(opening,e),active_ids=('opening',e.id))
        state=replay_securities((book,)).positions['aur']
        states.append((state.quantity, sum(D(p['amount']) for p in state.costs)))
    assert states == [(D(2),D(100))]*3


def test_import_edit_remove_and_reimport_does_not_resurrect(tmp_path):
    items=rows('b,2024-01-02,buy,AUR,10,100,EUR\n')
    saved=save_transaction_import(tmp_path,items,MAPPING,expected_journal_revision=None,expected_holdings_revision=None,
        additions=ADDITIONS,selected_ids=[items[0].identity],starts_at_zero=True,accept_reconciliation=True,comparison_date='2024-01-03')
    journal,revision=load_journal(tmp_path)
    snap=read_snapshot(tmp_path/'holdings.csv')
    candidate=correct_transaction(journal.securities[0],items[0].identity,quantity=D(11))
    candidate=replace(candidate,active_ids=(candidate.active_ids[0],candidate.records[-1].id))
    save_history(tmp_path,candidate,expected_journal_revision=revision,expected_holdings_revision=snap.revision,
                 activate=True,accept_balance_change=True)
    journal,revision=load_journal(tmp_path)
    assert prepare_import(journal,snap.holdings,items,MAPPING,selected_ids=[items[0].identity]).applied == 0
    book=journal.securities[0]
    candidate=remove_transactions(book,[book.records[-1].id])
    save_history(tmp_path,candidate,expected_journal_revision=revision,expected_holdings_revision=snap.revision,
                 activate=True,accept_balance_change=True)
    journal,_=load_journal(tmp_path)
    again=prepare_import(journal,snap.holdings,items,MAPPING,selected_ids=[items[0].identity])
    assert again.applied == 0 and again.journal == journal
    assert replay_securities(journal.securities).positions['aur'].quantity == 0
    assert journal.transaction_imports == saved.journal.transaction_imports


@pytest.mark.parametrize('explicit_order', [False, True])
def test_overlapping_export_preserves_edits_removals_and_stored_identities(tmp_path, explicit_order):
    items = (rows('b,2024-01-02,buy,AUR,10,100,EUR,4\nr,2024-01-05,buy,AUR,5,60,EUR,5\n', ',order')
             if explicit_order else rows('b,2024-01-02,buy,AUR,10,100,EUR\nr,2024-01-05,buy,AUR,5,60,EUR\n'))
    save_transaction_import(tmp_path, items, MAPPING, expected_journal_revision=None, expected_holdings_revision=None,
        additions=ADDITIONS, selected_ids=[r.identity for r in items], starts_at_zero=True,
        accept_reconciliation=True, comparison_date='2024-01-10')
    journal, revision = load_journal(tmp_path)
    snapshot = read_snapshot(tmp_path/'holdings.csv')
    candidate = correct_transaction(journal.securities[0], items[0].identity, quantity=D(11))
    candidate = replace(candidate, active_ids=tuple(i for i in candidate.active_ids if i != items[0].identity)
                        + (candidate.records[-1].id,))
    candidate = remove_transactions(candidate, [items[1].identity])
    save_history(tmp_path, candidate, expected_journal_revision=revision, expected_holdings_revision=snapshot.revision,
                 activate=True, accept_balance_change=True)
    journal, revision = load_journal(tmp_path)
    original_receipts = journal.transaction_imports
    original_records = journal.securities[0].records
    changed = rows('new,2024-02-01,buy,AUR,1,12,EUR\nr,2024-01-05,buy,AUR,5,60,EUR\nb,2024-01-02,buy,AUR,10,100,EUR\n')
    result = save_transaction_import(tmp_path, changed, MAPPING, expected_journal_revision=revision,
        expected_holdings_revision=snapshot.revision, selected_ids=[r.identity for r in changed],
        accept_reconciliation=True, comparison_date='2024-02-02')
    assert result.added == result.applied == 1
    saved, revision = load_journal(tmp_path)
    assert saved.transaction_imports[:2] == original_receipts
    assert saved.securities[0].records[:len(original_records)] == original_records
    assert replay_securities(saved.securities).positions['aur'].quantity == 12
    again = save_transaction_import(tmp_path, changed, MAPPING, expected_journal_revision=revision,
        expected_holdings_revision=snapshot.revision, selected_ids=[r.identity for r in changed],
        accept_reconciliation=True, comparison_date='2024-02-02')
    assert again.journal == saved and load_journal(tmp_path)[1] == revision


def test_reimport_matches_legacy_zero_fee_without_changing_receipt_identity():
    from portfolio_app.transaction_csv import TransactionRow, retain_import_interpretations
    original = rows('b,2024-01-02,buy,AUR,10,100,EUR,0.00\n', ',fee')[0]
    legacy = TransactionRow(original.raw, {**original.facts, 'fee_currency': 'EUR'})
    journal = applied([legacy])
    incoming = rows('b,2024-01-02,buy,AUR,10,100,EUR,0\n', ',fee')
    assert incoming[0].identity != legacy.identity
    assert retain_import_interpretations(incoming, journal.transaction_imports)[0].identity == legacy.identity
    result = prepare_import(journal, holdings(), incoming, MAPPING, selected_ids=[r.identity for r in incoming])
    assert result.journal == journal and result.added == result.applied == 0
    assert journal_from_dict(journal_to_dict(journal)) == journal


def test_explicit_order_change_requires_correction_and_preserves_original_evidence():
    original = rows('b,2024-01-02,buy,AUR,10,100,EUR,4\n', ',order')
    journal = applied(original)
    changed = rows('b,2024-01-02,buy,AUR,10,100,EUR,5\n', ',order')
    with pytest.raises(DataError, match='different facts'):
        prepare_import(journal, holdings(), changed, MAPPING, selected_ids=[changed[0].identity])
    result = prepare_import(journal, holdings(), changed, MAPPING, selected_ids=[changed[0].identity],
                            correction_ids=[changed[0].identity])
    assert result.applied == 1 and result.journal.transaction_imports[0] == journal.transaction_imports[0]
    assert result.journal.securities[0].records[-1].sequence == 5


def test_changed_amount_keeps_accepted_implicit_order_and_requires_correction():
    from portfolio_app.transaction_csv import retain_import_interpretations
    original = rows('a,2024-01-02,buy,AUR,1,10,EUR\nb,2024-01-02,buy,AUR,2,20,EUR\n')
    journal = applied(original)
    changed = rows('b,2024-01-02,buy,AUR,2,21,EUR\n')
    aligned = retain_import_interpretations(changed, journal.transaction_imports)
    assert aligned[0].facts['sequence'] == '1'
    with pytest.raises(DataError, match='different facts'):
        prepare_import(journal, holdings(), changed, MAPPING, selected_ids=[changed[0].identity])
    result = prepare_import(journal, holdings(), changed, MAPPING, selected_ids=[changed[0].identity],
                            correction_ids=[changed[0].identity])
    assert result.journal.securities[0].records[-1].sequence == 1
    assert result.journal.securities[0].records[-1].amount == 21


def test_mapped_evidence_is_an_ordinary_pending_transaction(tmp_path):
    items=rows('b,2024-01-02,buy,AUR,10,100,EUR\n')
    content=positions_content('',read_snapshot(tmp_path/'holdings.csv'),ADDITIONS)
    (tmp_path/'holdings.csv').write_text(content)
    snap=read_snapshot(tmp_path/'holdings.csv')
    result=save_transaction_import(tmp_path,items,MAPPING,expected_journal_revision=None,expected_holdings_revision=snap.revision)
    assert len(result.journal.securities[0].records) == 1
    assert not result.journal.securities[0].active
    assert (tmp_path/'holdings.csv').read_text() == content


def test_estimates_enable_normal_returns_but_missing_prices_still_block():
    items=rows('t,2024-01-02,transfer_in,AUR,10,100,EUR,yes\n',',estimated')
    journal=applied(items)
    scope=HistoryScope(('aur',))
    review=CoverageReview('r',scope,'2024-01-01','2024-01-03',history_fingerprint(journal))
    store=ObservationStore((Observation('a','price','aur','2024-01-02',10,'EUR'),Observation('b','price','aur','2024-01-03',12,'EUR')),(review,))
    result=calculate_history(journal,scope,store,'2024-01-01','2024-01-03')
    assert result.estimated and result.money_weighted is not None
    assert result.time_weighted == pytest.approx(.2)
    missing=calculate_history(journal,scope,replace(store,observations=()),'2024-01-01','2024-01-03')
    assert missing.money_weighted is None and missing.time_weighted is None
    without_review=calculate_history(journal,scope,replace(store,reviews=()),'2024-01-01','2024-01-03')
    assert without_review.money_weighted == result.money_weighted
    assert without_review.time_weighted == result.time_weighted


def test_atomic_new_positions_roll_back_on_journal_failure(tmp_path, monkeypatch):
    import portfolio_app.account_journal as journals
    def fail(*args,**kwargs):raise OSError('Invented disk failure')
    monkeypatch.setattr(journals,'save_journal',fail)
    items=rows('b,2024-01-02,buy,AUR,10,100,EUR\n')
    with pytest.raises(OSError,match='Invented'):
        save_transaction_import(tmp_path,items,MAPPING,expected_journal_revision=None,expected_holdings_revision=None,
            additions=ADDITIONS,selected_ids=[items[0].identity],starts_at_zero=True,accept_reconciliation=True,comparison_date='2024-01-03')
    assert not (tmp_path/'holdings.csv').exists() and not (tmp_path/'account-journal.json').exists()
    assert not (tmp_path/'.history-publication.json').exists()


def test_interrupted_publication_recovers_before_reads(tmp_path):
    after='id,name,shares\na,Aurora,5\n'
    (tmp_path/'holdings.csv').write_text(after)
    (tmp_path/'.history-publication.json').write_text(json.dumps({
        'holdings.csv':dict(before=None,after=after), 'account-journal.json':dict(before=None,after='{}')}))
    assert read_snapshot(tmp_path/'holdings.csv').holdings.empty
    assert not (tmp_path/'.history-publication.json').exists()


def test_unrelated_evidence_cannot_change_existing_capital():
    from test_gain_accounting import book, E, START, END, observations, summarize_known_gain
    from portfolio_app.transaction_csv import ImportReceipt
    journal=Journal(version=2,securities=(book(E('o','opening',START),
        E('b','buy','2024-02-01',quantity=10,amount=100,currency='EUR',charges_known=True),
        E('move','transfer','2024-07-01',quantity=4,other_position_id='other')),
        book(E('other-o','opening',START),identity='other')))
    scope=HistoryScope(('stock','other'))
    store=observations(journal,scope,[('stock',END,15),('other',END,15)],review=False)
    r=rows('unrelated,2024-01-02,buy,OTHER,1,10,EUR\n')[0]
    changed=replace(journal,version=5,transaction_imports=(ImportReceipt(r.identity,r.raw,r.facts),))
    assert summarize_known_gain(journal,scope,store,END) == summarize_known_gain(changed,scope,store,END)


def test_cost_estimate_qualifies_gain_without_qualifying_returns():
    from portfolio_app.gain_accounting import summarize_known_gain
    items=rows('o,2024-01-01,opening,AUR,10,80,EUR,yes\n',',estimated')
    journal=applied(items)
    scope=HistoryScope(('aur',))
    store=ObservationStore((Observation('a','price','aur','2024-01-01',10,'EUR'),
                            Observation('b','price','aur','2024-01-02',12,'EUR')),
        (CoverageReview('r',scope,'2024-01-01','2024-01-02',history_fingerprint(journal)),))
    result=calculate_history(journal,scope,store,'2024-01-01','2024-01-02')
    assert result.time_weighted == pytest.approx(.2) and result.money_weighted is not None
    assert not result.estimated
    gain=summarize_known_gain(journal,scope,store,'2024-01-02')
    assert gain.amount == 40 and gain.coverage_note == 'Estimated gain'


def test_corrected_import_backup_roundtrip_preserves_inert_legacy_settings(tmp_path):
    from portfolio_app.backup import create_backup, inspect_backup, commit_restore
    source=tmp_path/'source';source.mkdir()
    items=rows('b,2024-01-02,buy,AUR,10,100,EUR\n')
    save_transaction_import(source,items,MAPPING,expected_journal_revision=None,expected_holdings_revision=None,
        additions=ADDITIONS,selected_ids=[items[0].identity],starts_at_zero=True,
        accept_reconciliation=True,comparison_date='2024-01-03')
    journal,revision=load_journal(source)
    candidate=correct_transaction(journal.securities[0],items[0].identity,note='Invented correction')
    save_history(source,candidate,expected_journal_revision=revision,
        expected_holdings_revision=read_snapshot(source/'holdings.csv').revision)
    # Retired settings are opaque private files, preserved without driving the UI.
    legacy=source/'asset-performance.json';legacy.write_text('{"legacy": "invented"}')
    target=tmp_path/'restored'
    commit_restore(inspect_backup(create_backup(source)),target,source)
    assert load_journal(target)==load_journal(source)
    assert (target/'asset-performance.json').read_bytes()==legacy.read_bytes()


def test_later_review_keeps_removed_import_resolved(tmp_path):
    from portfolio_app.history_commands import reconstruction_candidate
    items=rows('b,2024-01-02,buy,AUR,10,100,EUR\ns,2024-01-03,sell,AUR,2,25,EUR\n')
    save_transaction_import(tmp_path,items,MAPPING,expected_journal_revision=None,expected_holdings_revision=None,
        additions=ADDITIONS,selected_ids=[r.identity for r in items],starts_at_zero=True,
        accept_reconciliation=True,comparison_date='2024-01-03')
    journal,_=load_journal(tmp_path)
    candidate=remove_transactions(journal.securities[0],[items[-1].identity])
    candidate=reconstruction_candidate(candidate,[e.id for e in candidate.records if e.kind in ('opening','buy')])
    assert candidate.records[-1].kind == 'void' and candidate.records[-1].id in candidate.active_ids
    reviewed=replace(journal,securities=(candidate,))
    assert replay_securities(reviewed.securities).positions['aur'].quantity == 10
    from portfolio_app.personal_performance import coverage_reasons
    assert 'Unapplied transaction evidence requires review' not in coverage_reasons(
        reviewed,HistoryScope(('aur',)),ObservationStore(),'2024-01-01','2024-01-04')


def test_failed_publication_preserves_existing_documents(tmp_path, monkeypatch):
    from portfolio_app.account_journal import save_journal
    import portfolio_app.account_journal as journals
    original='position_key,id,name,shares,account\nold,old,Invented existing,2,Portfolio\n'
    (tmp_path/'holdings.csv').write_text(original)
    save_journal(tmp_path,Journal(),None)
    before=(tmp_path/'account-journal.json').read_bytes()
    revision=load_journal(tmp_path)[1]
    def fail(*args,**kwargs):raise OSError('Invented interruption')
    monkeypatch.setattr(journals,'save_journal',fail)
    items=rows('b,2024-01-02,buy,AUR,10,100,EUR\n')
    with pytest.raises(OSError,match='Invented'):
        save_transaction_import(tmp_path,items,MAPPING,expected_journal_revision=revision,
            expected_holdings_revision=read_snapshot(tmp_path/'holdings.csv').revision,
            additions=ADDITIONS,selected_ids=[items[0].identity],starts_at_zero=True,
            accept_reconciliation=True,comparison_date='2024-01-03')
    assert (tmp_path/'holdings.csv').read_text()==original
    assert (tmp_path/'account-journal.json').read_bytes()==before


def test_estimated_internal_exchange_does_not_qualify_whole_portfolio_return():
    from portfolio_app.personal_performance import estimated_return_inputs
    items=rows('b,2024-01-02,buy,AUR,10,100,EUR,,,no\n'
               'x,2024-01-03,exchange,AUR,2,25,EUR,ORB,4,yes\n',
               ',received_asset,received_quantity,estimated')
    journal=applied(items)
    assert estimated_return_inputs(journal,HistoryScope(('aur',)),'2024-01-01','2024-01-03')
    assert estimated_return_inputs(journal,HistoryScope(('orb',)),'2024-01-01','2024-01-03')
    assert not estimated_return_inputs(journal,HistoryScope(('aur','orb')),'2024-01-01','2024-01-03')


def test_bulk_removal_validates_final_state_of_interdependent_books(tmp_path):
    from portfolio_app.account_journal import save_journal
    from portfolio_app.history_commands import preview_activation
    (tmp_path/'holdings.csv').write_text('position_key,id,name,shares,account\na,asset,Invented,0,A\nb,asset,Invented,0,B\n')
    a_events=(SecurityEvent('a-open','opening','2024-01-01'),
        SecurityEvent('buy','buy','2024-01-02',quantity=10,amount=100,currency='EUR',charges_known=True),
        SecurityEvent('out','transfer','2024-01-03',quantity=10,other_position_id='b'))
    b_events=(SecurityEvent('b-open','opening','2024-01-01'),
        SecurityEvent('back','transfer','2024-01-04',quantity=5,other_position_id='a'))
    a=SecurityBook('a','asset',('0','',''),records=a_events,active_ids=tuple(e.id for e in a_events))
    b=SecurityBook('b','asset',('0','',''),records=b_events,active_ids=tuple(e.id for e in b_events))
    journal=Journal(version=2,securities=(a,b));save_journal(tmp_path,journal,None)
    revision=load_journal(tmp_path)[1];snapshot=read_snapshot(tmp_path/'holdings.csv')
    a=remove_transactions(a,['buy','out']);b=remove_transactions(b,['back'])
    _,comparison,_=preview_activation(journal,a,a.active_ids,snapshot.holdings.iloc[0],other_changes=(b,))
    assert comparison.reconstructed_quantity == 0
    saved=save_history(tmp_path,a,other_changes=(b,),activate=True,accept_balance_change=True,
        expected_journal_revision=revision,expected_holdings_revision=snapshot.revision)
    assert all(s.quantity==0 for s in replay_securities(saved.securities).positions.values())


def test_starting_balance_looks_the_same_with_csv_or_manual_costs():
    from portfolio_app.history_ui import _transaction_table
    csv=event_for(rows('o,2024-01-01,opening,AUR,10,100,EUR\n')[0],MAPPING)
    manual=SecurityEvent('manual','opening','2024-01-01',quantity=10,amount=100,currency='EUR',charges_known=True)
    book=SecurityBook('aur','aur',('0','',''))
    left=_transaction_table([csv],book).iloc[0]
    right=_transaction_table([manual],book).iloc[0]
    assert left.equals(right) and left['Trade value']=='100.00 EUR'


def test_normal_review_preserves_checkpoint_without_inventing_zero_start():
    from portfolio_app.history_commands import reconstruction_candidate
    from test_inventory_imports import activate,row,estimate
    journal=activate([row('balance',**estimate())]).journal
    book=journal.securities[0]
    assert reconstruction_candidate(book,book.active_ids)==book
