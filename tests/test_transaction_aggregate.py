"""Invented portfolios only: independent balances and aggregate economic events."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from portfolio_app.account_journal import Journal, journal_from_dict, journal_to_dict
from portfolio_app.asset_history import journal_asset_history
from portfolio_app.history_observations import ObservationStore
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.security_history import replay_securities, selected_events
from portfolio_app.transaction_evidence import asset_movement_totals, compare_asset_changes, link_transfer_deductions, recorded_asset_changes
from portfolio_app.transaction_imports import prepare_import
from test_asset_history import row
from test_transaction_imports import scenario


ACCOUNTS = ('Desk', 'Vault')
MAPPING = {('Desk','AUR'):'aur', ('Vault','AUR'):'aur', ('Vault','ORB'):'orb'}


def holdings(quantity='999'):
    return parse_holdings('id,name,shares,account,position_key\naur,Aurora,'+quantity+',Combined,aur\norb,Orbit,888,Combined,orb\n')


def activate(rows=None, **kwargs):
    rows = scenario() if rows is None else rows
    return prepare_import(Journal(), holdings(), rows, MAPPING, selected_ids=[r.identity for r in rows],
                          starts_at_zero=True, aggregate_accounts=ACCOUNTS, **kwargs)


def test_aggregate_replays_forward_and_compares_independent_holdings():
    preview = activate()
    states = replay_securities(preview.journal.securities).positions
    assert states['aur'].quantity == 2 and states['orb'].quantity == D('2.8')
    comparison = next(c for c in preview.comparisons if c['position_id']=='aur')
    assert comparison['before_quantity']=='999.0' and comparison['after_quantity']=='2'
    book = next(b for b in preview.journal.securities if b.position_id=='aur')
    assert any(e.kind=='transfer' and e.other_position_id==book.position_id for e in selected_events(book))
    assert sum(D(p['amount']) for p in states['orb'].costs) == 97
    assert journal_from_dict(journal_to_dict(preview.journal)) == preview.journal
    again = prepare_import(preview.journal,holdings(),scenario(),MAPPING,
        selected_ids=[r.identity for r in scenario()],aggregate_accounts=ACCOUNTS)
    assert again.applied==0 and again.journal==preview.journal


def test_net_changes_include_earlier_exchange_activity_and_cancel_internal_principal():
    rows = scenario()
    totals = {t['asset']:D(t['known_unit_change']) for t in asset_movement_totals(rows)}
    assert totals == {'AUR':D(2),'ORB':D('2.8')}
    assert asset_movement_totals(rows)==asset_movement_totals((*rows, *rows))
    assert recorded_asset_changes(rows,'AUR')[-1]['net_change']==2
    # Internal principal can move many times without changing aggregate units.
    internal = row('internal','stake',account_id='Desk',counter_account_id='Vault',asset_id='AUR',quantity='500')
    assert {t['asset']:t['known_unit_change'] for t in asset_movement_totals((*rows,internal))} == {t['asset']:t['known_unit_change'] for t in asset_movement_totals(rows)}


def test_reference_changes_do_not_supply_opening_or_change_net_series():
    rows=scenario()
    first=compare_asset_changes(rows,{'AUR':dict(quantity='2',date='',scope='Invented wallet only')})
    second=compare_asset_changes(rows,{'AUR':dict(quantity='90',date='',scope='Invented wallet only')})
    assert first[0]['known_unit_change']==second[0]['known_unit_change']=='2'
    assert first[0]['reference_minus_net']=='0' and second[0]['reference_minus_net']=='88'
    assert first[0]['reference_date']=='Unknown'
    evidence=prepare_import(Journal(),holdings(),rows,{})
    assert not evidence.journal.securities
    with pytest.raises(DataError,match='Confirm zero'):
        prepare_import(evidence.journal,holdings(),rows,MAPPING,selected_ids=[r.identity for r in rows],aggregate_accounts=ACCOUNTS)


def test_forward_history_uses_active_books_and_not_evidence_only_corrections():
    preview=activate()
    prices={f'2024-04-{i:02d}':D(20) for i in range(1,9)} | {'2024-03-31':D(20)}
    result=journal_asset_history(preview.journal,'aur',prices,ObservationStore(),end='2024-04-08')
    assert result.points[0]['quantity']==0 and result.points[-1]['quantity']==2
    changed=replace(scenario()[0], raw={**scenario()[0].raw,'note':'evidence only'})
    # Change economic facts through the supported correction normalizer.
    from portfolio_app.transaction_csv import normalize
    changed=normalize({**changed.facts,'quantity':'11'})
    retained=prepare_import(preview.journal,holdings(),[changed],{},correction_ids=[changed.identity])
    after=journal_asset_history(retained.journal,'aur',prices,ObservationStore(),end='2024-04-08')
    assert [p['quantity'] for p in result.points]==[p['quantity'] for p in after.points]


def test_aggregate_does_not_merge_active_constituents_or_assume_custody_openings():
    existing=holdings()
    from pandas import concat
    existing=concat([existing,parse_holdings('id,name,shares,account,position_key\naur,Aurora,3,Vault,vault\n')])
    with pytest.raises(DataError,match='constituent'):
        prepare_import(Journal(),existing,scenario(),MAPPING,selected_ids=[r.identity for r in scenario()],aggregate_accounts=ACCOUNTS,starts_at_zero=True)
    opening=row('open','opening',account_id='Desk',timestamp_utc='2024-03-31T00:00:00Z',effective_date='2024-03-31')
    with pytest.raises(DataError,match='custody balance'):
        activate([opening],allow_incomplete=True)
    with pytest.raises(DataError,match='every source location'):
        prepare_import(Journal(),holdings(),scenario(),MAPPING,aggregate_accounts=('Vault',))


def test_linked_deduction_counts_known_units_once_without_classifying_fee():
    move=row('transfer','transfer',account_id='Desk',counter_account_id='Vault',quantity='2',fee_asset_id='AUR',fee_amount='')
    deduction=row('deduction','inventory_out',account_id='Desk',quantity='.25',flow_classification='unresolved')
    rows=link_transfer_deductions([move,deduction],{move.key:deduction.key})
    totals=asset_movement_totals(rows)
    assert totals[0]['known_unit_change']=='-0.25' and totals[0]['unknown_asset_charges']==0
    assert rows[0].raw==move.raw and rows[0].identity!=move.identity
    assert rows[0].facts['fee_amount']=='0' and rows[0].raw['fee_amount']=='' and rows[1].facts['flow_classification']=='unresolved'
    assert rows[1].identity==deduction.identity
    wrong=row('wrong','inventory_out',account_id='Desk',asset_id='ORB',quantity='.25',flow_classification='unresolved')
    with pytest.raises(DataError,match='same dated'):
        link_transfer_deductions([move,wrong],{move.key:wrong.key})


def test_unknown_asset_fee_still_blocks_without_proven_deduction():
    move=row('transfer','transfer',account_id='Desk',counter_account_id='Vault',fee_asset_id='AUR',fee_amount='')
    assert asset_movement_totals([move])[0]['unknown_asset_charges']==1
    with pytest.raises(DataError,match='Unknown asset fee'):
        activate([move],allow_incomplete=True)


def test_undated_units_and_conflicting_rows_do_not_get_invented_dates():
    from portfolio_app.transaction_csv import normalize
    base=row('undated','inventory_in',flow_classification='unresolved')
    undated=normalize({**base.facts,'timestamp_utc':''})
    assert asset_movement_totals([undated])[0]['known_unit_change']=='2'
    assert recorded_asset_changes([undated],'AUR')==()
    other=normalize({**undated.facts,'quantity':'3'})
    assert asset_movement_totals([undated,other])==()


def test_same_time_aggregate_requires_order_only_for_activation():
    first=row('first','buy',account_id='Desk',quantity='3',gross_amount='30',currency='EUR')
    second=row('second','sell',account_id='Vault',quantity='1',gross_amount='10',currency='EUR')
    assert asset_movement_totals([first,second])[0]['known_unit_change']=='2'
    with pytest.raises(DataError,match='sequence'):
        activate([first,second])


def test_internal_principal_needs_no_custody_order_but_asset_charge_still_does():
    buy=row('buy','buy',account_id='Desk',quantity='3',gross_amount='30',currency='EUR')
    move=row('internal','transfer',account_id='Desk',counter_account_id='Vault',quantity='2')
    repeated=row('internal-again','transfer',account_id='Desk',counter_account_id='Vault',quantity='2')
    state=replay_securities(activate([buy,move,repeated]).journal.securities)
    assert state.positions['aur'].quantity==3
    charged=row('charged','transfer',account_id='Desk',counter_account_id='Vault',quantity='2',fee_asset_id='AUR',fee_amount='.1')
    with pytest.raises(DataError,match='sequence'):
        activate([buy,charged])
