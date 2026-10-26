"""Invented inventory and checkpoint contracts, independent of working data."""
from dataclasses import replace
from decimal import Decimal as D
from io import StringIO
import csv
import pytest

from portfolio_app.account_journal import Journal, journal_to_dict, journal_from_dict, replay
from portfolio_app.transaction_csv import normalize, read_csv, FIELDS_V2
from portfolio_app.csv_transaction_imports import prepare_import
from portfolio_app.transaction_import_commands import prepare_positions, save_transaction_import
from portfolio_app.positions import read_snapshot
from portfolio_app.holdings import DataError
from portfolio_app.security_history import replay_securities, selected_events
from portfolio_app.gain_components import security_gain_components
from portfolio_app.gain_accounting import summarize_known_gain
from portfolio_app.history_observations import HistoryScope, ObservationStore
from portfolio_app.personal_performance import external_flows, coverage_reasons
from test_csv_transaction_imports import holdings, MAPPING, row as old_row


def row(identity, kind='checkpoint', **kw):
    fields = dict(schema_version='2',source_id='invented-inventory',event_id=identity,
        event_type=kind,account_id='Desk',asset_id='AUR',quantity='10',fee_amount='0')
    if kind in ('opening','checkpoint'):
        fields['effective_date']='2024-03-31'
    else:
        fields['timestamp_utc']='2024-04-01T09:00:00Z'
    if kind in ('inventory_in','inventory_out'):
        fields['flow_classification']='unresolved'
    fields.update(kw)
    return normalize(fields)


def activate(rows, journal=None, **kw):
    assert not any(r.issues for r in rows),[r.issues for r in rows]
    return prepare_import(journal or Journal(),holdings(),rows,MAPPING,
        selected_ids=[r.identity for r in rows],allow_incomplete=True,**kw)


def estimate(**kw):
    return dict(acquisition_cost='80',acquisition_currency='EUR',cost_quality='estimated',
                cost_source='Invented prior reconstruction',**kw)


def test_checkpoint_unknown_observation_is_not_execution_or_acquisition_date():
    r=row('balance',**estimate())
    p=activate([r]);j=p.journal
    assert j.version==4 and journal_from_dict(journal_to_dict(j))==j
    e=selected_events(j.securities[0])[0]
    assert e.kind=='checkpoint' and not e.timestamp_utc and not e.observed_at
    assert not replay_securities(j.securities,as_of='2024-03-30').positions['desk'].boundary
    state=replay_securities(j.securities).positions['desk']
    assert state.quantity==10 and state.lots[0].date==''
    assert state.costs[0]['amount']=='80' and state.costs[0]['quality']=='estimated'
    assert p.checkpoints[0]['explained_units'] is None
    assert not replay(j).cash
    gain=summarize_known_gain(j,HistoryScope(('desk',)),ObservationStore(),'2024-04-02',current_values={'desk':D(120)})
    assert gain.amount==40 and gain.partial
    assert 'Estimated' in gain.coverage_note


def test_checkpoint_difference_is_visible_without_fake_cash_or_gain():
    first=row('start','opening',quantity='10',acquisition_cost='80',acquisition_currency='EUR',cost_quality='recorded',cost_source='Invented statement')
    checkpoint=row('reconcile',quantity='13',effective_date='2024-04-02',**estimate())
    p=activate([first,checkpoint]);j=p.journal
    assert p.checkpoints[0]['explained_units']=='10'
    assert p.checkpoints[0]['unresolved_difference']=='3'
    assert replay_securities(j.securities,as_of='2024-04-01').positions['desk'].quantity==10
    assert replay_securities(j.securities).positions['desk'].quantity==13
    assert not replay(j).cash
    assert external_flows(j,HistoryScope(('desk',)),ObservationStore(),'2024-03-31','2024-04-03','EUR')[0]==()
    assert any('boundary' in x for x in coverage_reasons(j,HistoryScope(('desk',)),ObservationStore(),'2024-03-31','2024-04-03'))


def test_known_units_unknown_cost_and_unclassified_receipts_are_not_income():
    opening=row('start','opening',quantity='0')
    receipt=row('receipt','inventory_in',quantity='4')
    removal=row('removal','inventory_out',quantity='1',timestamp_utc='2024-04-02T09:00:00Z')
    j=activate([opening,receipt,removal]).journal
    s=replay_securities(j.securities).positions['desk']
    assert s.quantity==3 and s.costs[0]['amount']==''
    parts=security_gain_components(j,'desk',ObservationStore(),'2024-04-03',current_value=D(60))
    assert parts.income==0 and parts.remaining_cost is None and parts.unrealized is None
    flows,reasons=external_flows(j,HistoryScope(('desk',)),ObservationStore(),'2024-03-31','2024-04-03','EUR')
    assert flows==() and any('unresolved' in r for r in reasons)
    assert not replay(j).cash


def test_estimated_cost_survives_sale_and_transfer_without_becoming_recorded():
    opening=row('start','opening',**estimate())
    destination=row('destination','opening',account_id='Vault',quantity='0')
    move=row('move','transfer',quantity='4',counter_account_id='Vault')
    sale=row('sale','sell',quantity='2',gross_amount='30',currency='EUR',timestamp_utc='2024-04-02T09:00:00Z')
    j=activate([opening,destination,move,sale]).journal
    states=replay_securities(j.securities).positions
    assert states['vault'].costs[0]['amount']=='32'
    assert states['desk'].costs[0]['amount']=='32'
    assert states['vault'].costs[0]['quality']=='estimated'
    result=security_gain_components(j,'desk',ObservationStore(),'2024-04-03',current_value=D(50))
    assert result.realized==14 and result.unrealized==18
    assert any('Estimated acquisition cost' in r for r in result.reasons)


def test_partial_cost_does_not_turn_unknown_remainder_into_zero():
    j=activate([row('balance',**estimate(cost_quantity='8'))]).journal
    parts=replay_securities(j.securities).positions['desk'].costs
    assert [(p['shares'],p['amount']) for p in parts]==[('8','80'),('2','')]


def test_external_inventory_uses_valuation_not_acquisition_cost_for_flows():
    opening=row('start','opening',quantity='0')
    incoming=row('in','inventory_in',quantity='5',flow_classification='external',
        valuation_amount='100',valuation_currency='EUR',valuation_quality='estimated',valuation_source='Invented daily close',**estimate())
    j=activate([opening,incoming]).journal
    flows,reasons=external_flows(j,HistoryScope(('desk',)),ObservationStore(),'2024-03-31','2024-04-02','EUR')
    assert not reasons and flows[0].amount==100
    assert replay_securities(j.securities).positions['desk'].costs[0]['amount']=='80'


@pytest.mark.parametrize('changes',[
    {'cost_quality':'estimated','acquisition_cost':'80','acquisition_currency':'EUR'},
    {'quantity':'0','acquisition_cost':'1','acquisition_currency':'EUR','cost_quality':'recorded','cost_source':'invented'},
    {'effective_date':''}, {'effective_date':'2999-01-01'},
    {'timestamp_utc':'2024-04-02T00:00:00Z'},
    {'fee_amount':'1','fee_currency':'EUR'},
    {'cost_quantity':'11',**estimate()},
])
def test_invalid_checkpoint_evidence_stays_unapplied(changes):
    r=row('bad',**changes)
    assert r.issues
    retained=prepare_import(Journal(),holdings(),[r],{})
    assert retained.added==1 and not retained.journal.securities
    with pytest.raises(DataError):prepare_import(Journal(),holdings(),[r],MAPPING,selected_ids=[r.identity],allow_incomplete=True)


def test_v1_receipt_identity_and_wire_shape_unchanged():
    r=old_row(1,gross_amount='10',currency='EUR')
    j=prepare_import(Journal(),holdings(),[r],{},selected_ids=[]).journal
    assert j.version==3
    restored=journal_from_dict(journal_to_dict(j))
    assert restored==j and restored.transaction_imports[0].id==r.identity
    with pytest.raises(DataError,match='version 4'):
        replace(activate([row('balance',**estimate())]).journal,version=3)


def test_v2_csv_roundtrip_and_checkpoint_idempotency_and_correction():
    r=row('balance',**estimate())
    output=StringIO();writer=csv.DictWriter(output,fieldnames=FIELDS_V2);writer.writeheader();writer.writerow(r.facts)
    assert read_csv(output.getvalue())[0].identity==r.identity
    j=activate([r]).journal
    assert activate([r],j).journal==j
    changed=row('balance',quantity='11',**estimate())
    with pytest.raises(DataError,match='correction'):activate([changed],j)
    corrected=activate([changed],j,correction_ids=[changed.identity]).journal
    assert len(corrected.transaction_imports)==2
    assert replay_securities(corrected.securities).positions['desk'].quantity==11


def test_checkpoint_command_requires_separate_acceptance(tmp_path):
    snapshot=prepare_positions(tmp_path,[dict(position_key='desk',id='aur',name='Aurora',account='Desk')],expected_holdings_revision=None)
    r=row('checkpoint',**estimate())
    options=dict(expected_holdings_revision=snapshot.revision,expected_journal_revision=None,
        selected_ids=[r.identity],accept_reconciliation=True,comparison_date='2024-04-01')
    with pytest.raises(DataError,match='checkpoints'):
        save_transaction_import(tmp_path,[r],MAPPING,**options)
    result=save_transaction_import(tmp_path,[r],MAPPING,accept_checkpoints=True,**options)
    assert result.applied==1
    assert read_snapshot(tmp_path/'holdings.csv').holdings.shares.sum()==0


def test_future_trade_uses_checkpoint_inventory_and_does_not_double_it():
    j=activate([row('balance',**estimate())]).journal
    sale=row('sale','sell',quantity='3',gross_amount='45',currency='EUR')
    j=activate([sale],j).journal
    assert replay_securities(j.securities).positions['desk'].quantity==7


def test_unknown_origin_cannot_be_applied_from_an_unconfirmed_zero():
    with pytest.raises(DataError,match='zero holdings'):
        activate([row('receipt','inventory_in')])


def test_partial_gain_covers_costed_units_only():
    j=activate([row('balance',**estimate(cost_quantity='8'))]).journal
    result=security_gain_components(j,'desk',ObservationStore(),'2024-04-01',current_value=D(150))
    assert result.remaining_cost is None
    assert result.unrealized==40  # Eight valued units at 15, less their supplied cost 80.
    assert any('only units' in reason for reason in result.reasons)
    gain=summarize_known_gain(j,HistoryScope(('desk',)),ObservationStore(),'2024-04-01',current_values={'desk':D(150)})
    assert gain.amount==40 and gain.partial


def test_estimated_valuation_quality_propagates_into_reward_cost():
    opening=row('start','opening',quantity='0')
    reward=row('reward','reward',quantity='2',valuation_amount='12',valuation_currency='EUR',
        valuation_quality='estimated',valuation_source='Invented reconstructed valuation')
    j=activate([opening,reward]).journal
    assert replay_securities(j.securities).positions['desk'].costs[0]['quality']=='estimated'
    gain=summarize_known_gain(j,HistoryScope(('desk',)),ObservationStore(),'2024-04-02',current_values={'desk':D(20)})
    assert gain.amount==20 and 'Estimated' in gain.coverage_note


def test_imported_checkpoint_correction_preserves_receipt():
    from portfolio_app.history_commands import correct_transaction
    j=activate([row('balance',**estimate())]).journal
    book=j.securities[0]
    corrected=correct_transaction(book,book.active_ids[0],quantity=D(4))
    assert corrected.records[-1].replaces == book.active_ids[0]
    from dataclasses import replace
    j=replace(j,version=5,securities=(corrected,))
    assert journal_from_dict(journal_to_dict(j)) == j


def test_quantity_diagnostics_do_not_need_cost_or_invent_starting_inventory():
    from portfolio_app.transaction_evidence import movement_totals
    records=[row('receipt','inventory_in',quantity='5'),
        row('move','transfer',quantity='2',counter_account_id='Vault',fee_amount='',fee_asset_id='AUR'),
        row('undated','reward',quantity='1',timestamp_utc=''),row('balance',quantity='99')]
    result={(r['account'],r['asset']):r for r in movement_totals([*records,records[0]])}
    assert result['Desk','AUR']['known_unit_change']=='4'
    assert result['Desk','AUR']['undated_movements']==1
    assert result['Desk','AUR']['unknown_asset_charges']==1
    assert result['Vault','AUR']['known_unit_change']=='2'


def test_same_day_balance_order_requires_evidence():
    a=row('first',**estimate())
    b=row('second',quantity='12',**estimate())
    with pytest.raises(DataError,match='distinct explicit sequence'):
        activate([a,b])
    ordered=row('second',quantity='12',sequence='1',**estimate())
    p=activate([a,ordered])
    assert p.checkpoints[1]['explained_units']=='10'
    assert replay_securities(p.journal.securities).positions['desk'].quantity==12


def test_estimate_sources_do_not_hide_duplicate_economic_movements():
    opening=row('start','opening',quantity='0')
    a=row('a','inventory_in',**estimate())
    b=row('b','inventory_in',**{**estimate(),'cost_source':'Another invented reconstruction'})
    with pytest.raises(DataError,match='distinct explicit sequence'):
        activate([opening,a,b])
    b=row('b','inventory_in',sequence='1',**{**estimate(),'cost_source':'Another invented reconstruction'})
    with pytest.raises(DataError,match='Matching economic'):
        activate([opening,a,b])


def test_checkpoint_backup_roundtrip_preserves_unknown_cost_and_estimate_source(tmp_path):
    from portfolio_app.account_journal import load_journal
    from portfolio_app.backup import create_backup,inspect_backup,commit_restore
    source=tmp_path/'source';source.mkdir()
    snapshot=prepare_positions(source,[dict(position_key='desk',id='aur',name='Aurora',account='Desk')],expected_holdings_revision=None)
    r=row('checkpoint',**estimate(cost_quantity='8'))
    save_transaction_import(source,[r],{('Desk','AUR'):'desk'},expected_journal_revision=None,
        expected_holdings_revision=snapshot.revision,selected_ids=[r.identity],accept_reconciliation=True,
        accept_checkpoints=True,allow_incomplete=True,comparison_date='2024-04-01')
    restored=tmp_path/'restored'
    commit_restore(inspect_backup(create_backup(source)),restored,source)
    assert load_journal(source)==load_journal(restored)


def test_estimated_external_values_are_qualified_instead_of_blocked():
    opening=row('start','opening',quantity='0')
    incoming=row('receipt','inventory_in',flow_classification='external',valuation_amount='100',
        valuation_currency='EUR',valuation_quality='estimated',valuation_source='Invented prior valuation',**estimate())
    j=activate([opening,incoming]).journal
    assert not any('exact percentage returns' in r for r in coverage_reasons(j,HistoryScope(('desk',)),ObservationStore(),'2024-03-31','2024-04-02'))


def test_checkpoint_model_rejects_cash_and_cost_on_zero_units():
    from portfolio_app.security_history import SecurityEvent, SecurityBook
    from portfolio_app.cost_basis import component
    with pytest.raises(DataError,match='not a cash transaction'):
        SecurityEvent('bad','checkpoint','2024-03-31',quantity=D(1),amount=D(12),currency='EUR')
    event=SecurityEvent('bad','checkpoint','2024-03-31',costs=(component(0,12,'EUR'),))
    with pytest.raises(DataError,match='positive opening quantity'):
        replay_securities((SecurityBook('p','a',('0','',''),records=(event,),active_ids=(event.id,)),))


def test_malformed_cost_provenance_is_rejected_as_data_error():
    from portfolio_app.cost_basis import validate_components
    for parts in ([None],[dict(shares='1',amount='2',currency='EUR',quality='estimated',source=7)]):
        with pytest.raises(DataError):validate_components(parts)
