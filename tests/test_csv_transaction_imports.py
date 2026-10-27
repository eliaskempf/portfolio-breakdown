"""Independently invented scenarios; never read working data or supplied exports."""
from dataclasses import replace
from decimal import Decimal as D
from io import StringIO
import csv
import pytest

from portfolio_app.account_journal import Journal, journal_from_dict, journal_to_dict, replay
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.transaction_csv import FIELDS, normalize, read_csv
from portfolio_app.csv_transaction_imports import prepare_import
from portfolio_app.security_history import replay_securities
from portfolio_app.gain_components import security_gain_components
from portfolio_app.gain_accounting import summarize_known_gain
from portfolio_app.history_observations import HistoryScope, ObservationStore
from portfolio_app.personal_performance import external_flows


def row(number, kind='buy', **fields):
    return normalize(dict(schema_version='1', source_id='invented-lab', event_id=str(number),
        timestamp_utc=f'2024-04-{number:02d}T09:00:00Z', event_type=kind, account_id='Desk',
        asset_id='AUR', quantity='1', fee_amount='0', **fields))


def scenario():
    def r(n, kind, **kw):
        values = dict(schema_version='1', source_id='invented-lab', event_id=str(n),
            timestamp_utc=f'2024-04-{n:02d}T09:00:00Z', event_type=kind, account_id='Desk',
            asset_id='AUR', quantity='1', fee_amount='0')
        values.update(kw)
        return normalize(values)
    return (
        r(1,'buy',quantity='10',gross_amount='100',currency='EUR',fee_amount='2',fee_currency='EUR'),
        r(2,'buy',quantity='5',gross_amount='100',currency='EUR'),
        r(3,'transfer',quantity='12',counter_account_id='Vault',fee_amount='1',fee_asset_id='AUR'),
        r(4,'swap',account_id='Vault',quantity='4',received_asset_id='ORB',received_quantity='2',
          valuation_amount='80',valuation_currency='EUR',fee_amount='1',fee_asset_id='AUR'),
        r(5,'reward',account_id='Vault',asset_id='ORB',quantity='1',valuation_amount='25',valuation_currency='EUR'),
        r(6,'fee',account_id='Vault',asset_id='ORB',quantity='0.2'),
        r(7,'sell',account_id='Vault',quantity='7',gross_amount='175',currency='EUR',fee_amount='3',fee_currency='EUR'),
    )


def holdings():
    return parse_holdings('id,name,shares,account,position_key\naur,Aurora,0,Desk,desk\naur,Aurora,0,Vault,vault\norb,Orbit,0,Vault,orb\n')


MAPPING = {('Desk','AUR'):'desk', ('Vault','AUR'):'vault', ('Vault','ORB'):'orb'}


def activated(rows=None, **kwargs):
    rows = scenario() if rows is None else rows
    return prepare_import(Journal(), holdings(), rows, MAPPING,
                          selected_ids=[r.identity for r in rows], starts_at_zero=True, **kwargs)


def test_replay_lots_cash_and_profit_identity():
    result = activated()
    j = result.journal
    state = replay_securities(j.securities)
    assert state.positions['desk'].quantity == 2
    assert state.positions['vault'].quantity == 0
    assert state.positions['orb'].quantity == D('2.8')
    assert sum(D(c['amount']) for c in state.positions['orb'].costs) == 97
    assert state.positions['desk'].lots[0].date == '2024-04-02'
    assert not replay(j).cash
    obs = ObservationStore()
    scope = HistoryScope(tuple(MAPPING.values()))
    flows, missing = external_flows(j, scope, obs, '2024-03-31', '2024-04-08', 'EUR')
    assert not missing and sum(f.amount for f in flows) == 30
    components = [security_gain_components(j,p,obs,'2024-04-08',current_value=v) for p,v in [('desk',D(40)),('vault',D(0)),('orb',D(140))]]
    assert sum(c.realized for c in components) == D('110.4')
    assert sum(c.income for c in components) == 25
    assert sum(c.expenses for c in components) == D('28.4')
    assert sum(c.unrealized for c in components) == 43
    gain = summarize_known_gain(j,scope,obs,'2024-04-08',current_values={'desk':D(40),'vault':D(0),'orb':D(140)})
    assert gain.amount == 150 and gain.capital == 30
    assert gain.review_required_only
    assert journal_from_dict(journal_to_dict(j)) == j


def test_evidence_only_and_idempotence_and_correction():
    rows = scenario()
    evidence = prepare_import(Journal(),holdings(),rows,MAPPING)
    assert not replay_securities(evidence.journal.securities).positions
    first = activated()
    again = prepare_import(first.journal,holdings(),rows,MAPPING,selected_ids=[r.identity for r in rows])
    assert again.journal == first.journal and again.added == again.applied == 0
    changed = normalize({**rows[0].raw,'gross_amount':'110'})
    with pytest.raises(DataError,match='different facts'):
        prepare_import(first.journal,holdings(),[changed],MAPPING)
    corrected = prepare_import(first.journal,holdings(),[changed],MAPPING,selected_ids=[changed.identity],correction_ids=[changed.identity])
    assert len(corrected.journal.transaction_imports)==8
    assert replay_securities(corrected.journal.securities).positions['desk'].quantity==2


def test_incomplete_reward_applies_units_without_cost_or_income():
    rows = list(scenario())
    rows[4] = normalize({**rows[4].raw,'valuation_amount':'','valuation_currency':''})
    with pytest.raises(DataError,match='missing monetary'):
        activated(rows)
    j=activated(rows,allow_incomplete=True).journal
    assert replay_securities(j.securities).positions['orb'].quantity==D('2.8')
    c=security_gain_components(j,'orb',ObservationStore(),'2024-04-08',current_value=D(140))
    assert c.remaining_cost is None and c.income is None
    gain=summarize_known_gain(j,HistoryScope(tuple(MAPPING.values())),ObservationStore(),'2024-04-08',current_values={'desk':D(40),'vault':D(0),'orb':D(140)})
    assert gain.amount==150


def test_unknown_asset_fee_blocks_and_unknown_fiat_fee_does_not_invent_zero():
    rows=list(scenario())
    rows[2]=normalize({**rows[2].raw,'fee_amount':''})
    with pytest.raises(DataError,match='Unknown asset fee'):
        activated(rows,allow_incomplete=True)
    rows=list(scenario()[:2])
    rows[0]=normalize({**rows[0].raw,'fee_amount':''})
    j=activated(rows,allow_incomplete=True).journal
    assert replay_securities(j.securities).positions['desk'].quantity==15
    flows,missing=external_flows(j,HistoryScope(('desk',)),ObservationStore(),'2024-03-31','2024-04-08','EUR')
    assert missing and any(f.amount is None for f in flows)


def test_sequence_and_boundary_and_precision():
    rows=list(scenario()[:2])
    rows[1]=normalize({**rows[1].raw,'timestamp_utc':rows[0].facts['timestamp_utc']})
    with pytest.raises(DataError,match='sequence'):
        activated(rows)
    rows[1]=normalize({**rows[1].raw,'sequence':'2'})
    assert activated(rows).applied==2
    with pytest.raises(DataError,match='Confirm zero'):
        prepare_import(Journal(),holdings(),scenario(),MAPPING,selected_ids=[r.identity for r in scenario()])
    precise=normalize({**rows[0].raw,'quantity':'0.123456789012345678901234567890123456789'})
    assert precise.facts['quantity'].endswith('456789') and len(precise.facts['quantity'])==41


def test_csv_contract_and_crypto_adapter():
    stream=StringIO(); writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader()
    writer.writerow(scenario()[0].facts)
    assert read_csv(stream.getvalue())[0].identity==scenario()[0].identity
    raw=dict(event_id='toy',timestamp_utc='2024-01-01T00:00:00Z',event_type='swap',account_id='Desk',asset_id='AUR',quantity='2',gross_amount='3',currency='ORB',fees='0.1',fee_currency='AUR',valuation_eur='7')
    swap=normalize(raw,layout='crypto',source_id='toy',asset_fees=('AUR',))
    assert not swap.issues and swap.facts['received_quantity']=='3' and not swap.facts['gross_amount']
    assert swap.facts['fee_asset_id']=='AUR' and swap.facts['valuation_currency']=='EUR'
    with pytest.raises(DataError,match='headers'):
        read_csv('event_id,event_id\na,b\n')


def test_subset_swap_needs_valuation_but_whole_scope_does_not():
    rows=list(scenario())
    rows[3]=normalize({**rows[3].raw,'valuation_amount':'','valuation_currency':''})
    j=activated(rows,allow_incomplete=True).journal
    _,missing=external_flows(j,HistoryScope(('orb',)),ObservationStore(),'2024-03-31','2024-04-08','EUR')
    assert missing
    flows,missing=external_flows(j,HistoryScope(tuple(MAPPING.values())),ObservationStore(),'2024-03-31','2024-04-08','EUR')
    assert not missing and sum(f.amount for f in flows)==30


def test_third_asset_fee_and_received_lot_order():
    from portfolio_app.security_history import SecurityBook,SecurityEvent,SecurityLot
    from portfolio_app.cost_basis import component
    # A separate inventory pays the fee; the swap must not debit its sent asset twice.
    rows=list(scenario()[:4])
    rows[3]=normalize({**rows[3].raw,'fee_asset_id':'ORB','fee_amount':'0.25'})
    opening=SecurityEvent('inventory','opening','2024-03-30',quantity=D(1),
        lots=(SecurityLot('inventory-lot',D(1),(component(1,12,'EUR','2023-01-01'),),True,'2023-01-01'),))
    b=SecurityBook('orb','orb',('0','',''),records=(opening,),active_ids=('inventory',))
    j=Journal(version=2,securities=(b,))
    p=prepare_import(j,holdings(),rows,MAPPING,selected_ids=[r.identity for r in rows],starts_at_zero=True)
    state=replay_securities(p.journal.securities)
    assert state.positions['orb'].quantity==D('2.75')
    assert state.positions['vault'].quantity==8
    assert state.positions['orb'].lots[0].date=='2023-01-01'
    c=security_gain_components(p.journal,'orb',ObservationStore(),'2024-04-08')
    assert c.expenses==3


def test_incremental_mapping_cannot_change_and_conflicting_upload_rejected():
    first=activated()
    with pytest.raises(DataError,match='mapping'):
        prepare_import(first.journal,holdings(),scenario(),{**MAPPING,('Desk','AUR'):'vault'})
    changed=normalize({**scenario()[0].raw,'quantity':'11'})
    with pytest.raises(DataError,match='within this upload'):
        prepare_import(Journal(),holdings(),[scenario()[0],changed],MAPPING,correction_ids=[changed.identity])


def test_correcting_retained_only_revision_replaces_active_ancestor():
    rows=scenario();first=activated()
    evidence=normalize({**rows[0].raw,'gross_amount':'110'})
    held=prepare_import(first.journal,holdings(),[evidence],{},correction_ids=[evidence.identity])
    corrected=normalize({**rows[0].raw,'gross_amount':'120'})
    applied=prepare_import(held.journal,holdings(),[corrected],MAPPING,selected_ids=[corrected.identity],correction_ids=[corrected.identity])
    state=replay_securities(applied.journal.securities)
    assert state.positions['desk'].quantity==2
    b=next(b for b in applied.journal.securities if b.position_id=='desk')
    assert rows[0].identity not in b.active_ids and corrected.identity in b.active_ids


def test_v2_wire_shape_stays_compatible():
    from portfolio_app.security_history import SecurityBook,SecurityEvent
    b=SecurityBook('p','a',('0','',''),records=(SecurityEvent('open','opening','2024-01-01'),),active_ids=('open',))
    raw=journal_to_dict(Journal(version=2,securities=(b,)))
    assert 'transaction_imports' not in raw
    assert 'custody_id' not in raw['securities'][0]
    assert 'asset_charges' not in raw['securities'][0]['records'][0]
    assert journal_to_dict(journal_from_dict(raw))==raw


def test_missing_fx_limits_components_without_erasing_units_or_portfolio_flows():
    rows=list(scenario())
    rows[3]=normalize({**rows[3].raw,'valuation_currency':'USD'})
    j=activated(rows).journal
    state=replay_securities(j.securities)
    assert state.positions['orb'].quantity==D('2.8')
    outgoing=security_gain_components(j,'vault',ObservationStore(),'2024-04-08')
    incoming=security_gain_components(j,'orb',ObservationStore(),'2024-04-08')
    assert outgoing.realized is None and incoming.remaining_cost is None
    gain=summarize_known_gain(j,HistoryScope(tuple(MAPPING.values())),ObservationStore(),'2024-04-08',current_values={'desk':D(40),'vault':D(0),'orb':D(140)})
    assert gain.amount==150


def test_oversale_and_partial_dependency_do_not_activate():
    rows=scenario()
    with pytest.raises(DataError,match='exceeds'):
        prepare_import(Journal(),holdings(),rows,MAPPING,selected_ids=[rows[-1].identity],starts_at_zero=True)
    missing=normalize({**rows[4].raw,'timestamp_utc':''})
    held=prepare_import(Journal(),holdings(),[missing],{})
    assert held.journal.transaction_imports[0].issues
    assert not held.journal.securities


def test_group_ids_do_not_deduplicate_distinct_executions():
    a=scenario()[0]
    b=normalize({**a.raw,'event_id':'another-execution','sequence':'1','group_id':'partial-fills'})
    with pytest.raises(DataError,match='Matching economic'):
        activated([a,b])
    j=activated([a,b],accept_duplicates=True).journal
    assert replay_securities(j.securities).positions['desk'].quantity==20
    again=prepare_import(j,holdings(),[a,b],MAPPING,selected_ids=[a.identity,b.identity])
    assert again.applied==0


def test_duplicate_review_identifies_pairs_and_does_not_repeat_for_unrelated_new_trade():
    a = scenario()[0]
    b = normalize({**a.raw, 'event_id':'another-execution', 'sequence':'1'})
    reviewed = activated([a, b], accept_duplicates=True)
    assert reviewed.matches == (('desk', a.identity, b.identity),)
    c = scenario()[1]
    preview = prepare_import(reviewed.journal, holdings(), [c], MAPPING,
        selected_ids=[c.identity], starts_at_zero=True)
    assert not preview.matches and preview.applied == 1


def test_received_swap_and_income_are_useful_history_without_a_cash_buy():
    from portfolio_app.history_scope import can_start_history_chart
    j=activated().journal
    assert can_start_history_chart(j,HistoryScope(('orb',)))
    bad=normalize({**scenario()[0].raw,'timestamp_utc':'2024-04-01T09:00:00.123456789Z'})
    assert bad.issues


def test_linked_cash_is_explicit_and_internal_payments_cancel():
    from portfolio_app.account_journal import Account,Event
    j=Journal(accounts=(Account('funding','Invented cash'),),events=(Event('cash-open','2024-03-31','cash_open','funding','EUR',D(300)),))
    rows=scenario()
    result=prepare_import(j,holdings(),rows,MAPPING,cash_accounts={'Desk':'funding'},selected_ids=[r.identity for r in rows],starts_at_zero=True)
    assert replay(result.journal).cash['funding','EUR']==98
    flows,missing=external_flows(result.journal,HistoryScope(tuple(MAPPING.values()),('funding',)),ObservationStore(),'2024-03-31','2024-04-08','EUR')
    assert not missing and sum(f.amount for f in flows)==-172
    assert len(result.journal.events)==1  # security cash remains derived


def test_sub_cent_linked_cash_is_not_silently_rounded():
    from portfolio_app.account_journal import Account,Event
    j=Journal(accounts=(Account('funding','Invented cash'),),events=(Event('cash-open','2024-03-31','cash_open','funding','EUR',D(300)),))
    row=normalize({**scenario()[0].raw,'gross_amount':'100.004'})
    with pytest.raises(DataError,match='sub-cent'):
        prepare_import(j,holdings(),[row],MAPPING,cash_accounts={'Desk':'funding'},selected_ids=[row.identity],starts_at_zero=True)
    retained=prepare_import(j,holdings(),[row],MAPPING)
    assert retained.journal.transaction_imports[0].facts['gross_amount']=='100.004'
    assert replay(retained.journal).cash['funding','EUR']==300
