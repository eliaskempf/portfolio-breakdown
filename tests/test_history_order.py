"""Invented mixed-source chronology, retaining accepted FIFO and source clocks."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from portfolio_app.account_journal import Journal, journal_from_dict, journal_to_dict
from portfolio_app.csv_transaction_imports import prepare_import
from portfolio_app.history_commands import preview_activation, reconstruction_candidate
from portfolio_app.history_order import OrderReviewRequired, apply_order, validate_order
from portfolio_app.holdings import parse_holdings
from portfolio_app.security_history import SecurityBook, SecurityEvent, selected_events, replay_securities
from portfolio_app.transaction_csv import read_csv, normalize, ImportReceipt


def source_row():
    return normalize(dict(schema_version='1', source_id='invented', event_id='buy',
        timestamp_utc='2024-01-02T09:00:00Z', event_type='buy', account_id='Desk',
        asset_id='INV', quantity='2', gross_amount='20', currency='EUR', fee_amount='0'))


def baseline_for(book):
    row = source_row()
    return Journal(version=5, securities=(book,), transaction_imports=(
        ImportReceipt(row.identity,row.raw,row.facts,positions=(('Desk','INV','p'),)),))


def mixed():
    from portfolio_app.csv_transaction_imports import event_for
    buy = event_for(source_row(), {('Desk','INV'):'p'})
    sale = SecurityEvent('pdf-sale', 'sell', buy.date, 5400000000,
        quantity=1, amount=15, currency='EUR', charges_known=True,
        source='consors_pdf:invented', order_domain='statement-local', order_time='15:00:00')
    book = SecurityBook('p', 'invented', ('0','',''), records=(buy,sale))
    return book, buy, sale


@pytest.mark.parametrize('reverse', [False, True])
def test_mixed_clocks_require_reachable_review_before_oversell(reverse):
    book, buy, sale = mixed()
    if reverse:
        book = replace(book, records=tuple(reversed(book.records)))
    baseline = baseline_for(book)
    candidate = reconstruction_candidate(book, (buy.id,sale.id), starts_at_zero=True)
    row = parse_holdings('position_key,id,name,shares\np,invented,Invented,0\n').iloc[0]
    with pytest.raises(OrderReviewRequired) as error:
        preview_activation(baseline, candidate, candidate.active_ids, row)
    assert {e.id for group in error.value.groups for _,e in group} == {buy.id,sale.id}
    ordered = apply_order(baseline, (candidate,), {buy.id:1,sale.id:2})
    validate_order(baseline, ordered)
    assert ordered[0].records[:2] == book.records  # Saved evidence is append-only.
    assert [(e.kind,e.sequence) for e in selected_events(ordered[0]) if e.kind!='opening'] == [('buy',1),('sell',2)]
    state = replay_securities(ordered).positions['p']
    assert state.quantity == 1 and sum(D(p['amount']) for p in state.costs) == 10
    reviewed = [e for e in ordered[0].records if e.order_domain=='reviewed']
    assert reviewed[0].timestamp_utc == buy.timestamp_utc if not reverse else reviewed[1].timestamp_utc == buy.timestamp_utc
    result = replace(baseline, securities=ordered)
    assert journal_from_dict(journal_to_dict(result)) == result


def test_accepted_mixed_source_order_is_grandfathered_on_unrelated_days():
    book, buy, sale = mixed()
    # Deliberately accepted legacy ordering: no read-time migration or rerank.
    buy, sale = replace(buy, sequence=1, order_domain=''), replace(sale, sequence=2, order_domain='', order_time='')
    opening = SecurityEvent('zero','opening','2024-01-01',source='confirmed_zero_start')
    book = replace(book, records=(opening,buy,sale), active_ids=('zero',buy.id,sale.id))
    baseline = baseline_for(book)
    later = SecurityEvent('later','buy','2024-01-03',quantity=1,amount=20,currency='EUR',charges_known=True)
    candidate = replace(book, records=(*book.records,later),active_ids=(*book.active_ids,later.id))
    validate_order(baseline,(candidate,))
    assert apply_order(baseline,(candidate,),{}) == (candidate,)
    assert all('order_domain' not in e and 'order_time' not in e for e in journal_to_dict(baseline)['securities'][0]['records'])


def test_separate_date_only_files_require_order_and_preserve_review_on_reimport():
    holdings = parse_holdings('position_key,id,name,shares\np,invented,Invented,0\n')
    mapping = {('Portfolio','AUR'):'p'}
    def rows(text):
        return read_csv('reference,date,type,asset,quantity,amount,currency\n'+text)
    first = rows('first,2024-01-02,buy,AUR,2,20,EUR\n')
    original = prepare_import(Journal(),holdings,first,mapping,selected_ids=[first[0].identity],starts_at_zero=True).journal
    second = rows('second,2024-01-02,buy,AUR,2,40,EUR\nsale,2024-01-02,sell,AUR,1,30,EUR\n')
    options = dict(selected_ids=[r.identity for r in second])
    with pytest.raises(OrderReviewRequired):
        prepare_import(original,holdings,second,mapping,**options)
    ranks = {first[0].identity:1,second[0].identity:2,second[1].identity:3}
    reviewed = prepare_import(original,holdings,second,mapping,order_overrides=ranks,**options).journal
    assert sum(D(p['amount']) for p in replay_securities(reviewed.securities).positions['p'].costs) == 50
    assert prepare_import(reviewed,holdings,second,mapping,**options).journal == reviewed


def test_dependent_legs_require_review_but_unrelated_positions_do_not():
    book,buy,sale = mixed()
    transfer = replace(sale, kind='transfer', other_position_id='dest')
    owner = replace(book, records=(transfer,), active_ids=(transfer.id,))
    other = replace(book, position_id='dest', records=(buy,), active_ids=(buy.id,))
    with pytest.raises(OrderReviewRequired):
        validate_order(Journal(),(owner,other))
    independent = replace(owner,records=(sale,))
    validate_order(Journal(),(independent,other))


def test_order_review_controls_recover_from_duplicate_ranks(tmp_path):
    from test_transaction_review_ui import launch, button
    first = b'reference,date,type,asset,quantity,amount,currency\na,2024-01-02,buy,BTC,2,20,EUR\n'
    app = launch(tmp_path,first)
    button(app,'Review against portfolio').click().run()
    button(app,'Apply history').click().run()
    second = b'reference,date,type,asset,quantity,amount,currency\nb,2024-01-02,sell,BTC,1,30,EUR\n'
    app = launch(tmp_path,second)
    button(app,'Review against portfolio').click().run()
    assert not app.exception and len(app.number_input)==2
    app.number_input[1].set_value(1).run()
    button(app,'Use reviewed order').click().run()
    assert not app.exception and len(app.number_input)==2
    app.number_input[1].set_value(2).run()
    button(app,'Use reviewed order').click().run()
    assert not app.exception
    button(app,'Apply history').click().run()
    from portfolio_app.account_journal import load_journal
    journal = load_journal(tmp_path)[0]
    assert next(iter(replay_securities(journal.securities).positions.values())).quantity == 1


def test_legacy_import_order_correction_upgrades_without_rewriting_evidence():
    holdings = parse_holdings('position_key,id,name,shares\np,invented,Invented,0\n')
    first = source_row()
    mapping = {('Desk','INV'):'p'}
    original = prepare_import(Journal(),holdings,(first,),mapping,selected_ids=[first.identity],starts_at_zero=True).journal
    assert original.version == 3
    second = normalize({**first.raw,'event_id':'second','quantity':'1','gross_amount':'15'})
    with pytest.raises(OrderReviewRequired):
        prepare_import(original,holdings,(second,),mapping,selected_ids=[second.identity])
    result = prepare_import(original,holdings,(second,),mapping,selected_ids=[second.identity],
        order_overrides={first.identity:1,second.identity:2}).journal
    assert result.version == 5 and result.securities[0].records[:len(original.securities[0].records)] == original.securities[0].records
    assert journal_from_dict(journal_to_dict(result)) == result


def test_colliding_balance_boundaries_require_distinct_end_of_day_order():
    first = SecurityEvent('one','checkpoint','2024-01-02',quantity=2)
    second = replace(first,id='two',quantity=3)
    book = SecurityBook('p','invented',('0','',''),records=(first,second),active_ids=('one','two'))
    with pytest.raises(OrderReviewRequired):
        validate_order(Journal(),(book,))
    ordered = apply_order(Journal(),(book,),{'one':1,'two':2})
    validate_order(Journal(),ordered)
    assert replay_securities(ordered).positions['p'].quantity == 3
    assert all(e.sequence > 86400000000000000 for e in ordered[0].records)


def test_date_edit_drops_clock_domain_without_inventing_a_timestamp():
    from portfolio_app.history_commands import correct_transaction
    book,buy,_ = mixed()
    edited = correct_transaction(book,buy.id,date='2024-01-03').records[-1]
    assert edited.timestamp_utc == edited.order_domain == edited.order_time == ''
    reviewed = correct_transaction(book,buy.id,date='2024-01-03',sequence=2).records[-1]
    assert reviewed.order_domain == 'reviewed' and not reviewed.timestamp_utc


def test_ordered_saved_proposals_remain_excludable():
    from portfolio_app.history_order import editable_order_ids, original_order_ids
    book,buy,sale = mixed()
    baseline = baseline_for(book)
    candidate = reconstruction_candidate(book,(buy.id,sale.id),starts_at_zero=True)
    ranks = {buy.id:1,sale.id:2}
    ordered = apply_order(baseline,(candidate,),ranks)
    editable = editable_order_ids(ordered,{sale.id},ranks)
    selected = {e.id for e in selected_events(ordered[0]) if e.kind=='sell'}
    assert editable.intersection(ordered[0].active_ids) == selected
    assert original_order_ids(selected,ranks) == {sale.id}


def test_reordering_accepted_transfer_reviews_destination_cost_too():
    holdings = parse_holdings('position_key,id,name,shares,account\na,inv,Invented,0,Desk\nb,inv,Invented,0,Vault\n')
    mapping = {('Desk','INV'):'a',('Vault','INV'):'b'}
    first = normalize({**source_row().raw,'quantity':'1','gross_amount':'10'})
    second = normalize({**first.raw,'event_id':'second','sequence':'1','gross_amount':'20'})
    transfer = normalize({**first.raw,'event_id':'transfer','sequence':'2','event_type':'transfer',
                          'counter_account_id':'Vault','gross_amount':'','currency':''})
    initial = (first,second,transfer)
    journal = prepare_import(Journal(),holdings,initial,mapping,
        selected_ids=[r.identity for r in initial],starts_at_zero=True).journal
    incoming = normalize({**first.raw,'event_id':'new','gross_amount':'30'})
    result = prepare_import(journal,holdings,(incoming,),mapping,selected_ids=[incoming.identity],
        order_overrides={incoming.identity:1,transfer.identity:2,first.identity:3,second.identity:4})
    comparisons = {c['position_id']:c for c in result.comparisons}
    assert comparisons.keys() == {'a','b'}
    assert D(comparisons['b']['before_cost']) == 10 and D(comparisons['b']['after_cost']) == 30


def test_ordered_retained_checkpoints_keep_replacement_balance_confirmation():
    from test_inventory_imports import row, estimate, holdings, MAPPING
    rows = (row('first',**estimate()),row('second',quantity='12',**estimate()))
    pending = prepare_import(Journal(),holdings(),rows,MAPPING).journal
    preview = prepare_import(pending,holdings(),rows,MAPPING,selected_ids=[r.identity for r in rows],
        allow_incomplete=True,order_overrides={r.identity:i for i,r in enumerate(rows,1)})
    assert len(preview.checkpoints) == 2
    assert preview.checkpoints[1]['explained_units'] == '10'
    assert preview.checkpoints[1]['checkpoint_units'] == '12'
