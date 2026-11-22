"""Independently invented scenarios; never read working data or supplied exports."""
from dataclasses import replace
from decimal import Decimal as D
from io import StringIO
import csv
import pytest

from portfolio_app.ledger.account_journal import Journal, journal_from_dict, journal_to_dict, replay
from portfolio_app.portfolio.holdings import DataError, parse_holdings
from portfolio_app.ingestion.transaction_csv import FIELDS, normalize, read_csv
from portfolio_app.ingestion.csv_transaction_imports import prepare_import
from portfolio_app.ledger.security_history import replay_securities
from portfolio_app.performance.gain_components import security_gain_components
from portfolio_app.performance.gain_accounting import summarize_known_gain
from portfolio_app.performance.history_observations import HistoryScope, ObservationStore
from portfolio_app.performance.personal_performance import external_flows


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
    assert not gain.partial
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


@pytest.mark.parametrize('selected', [False, True])
def test_batch_assembly_hashes_linearly_and_validates_books_once(monkeypatch, selected):
    from datetime import date, timedelta
    from portfolio_app.ledger.security_history import SecurityBook
    from portfolio_app.ingestion.transaction_csv import TransactionRow
    count = 180
    rows = tuple(normalize(dict(schema_version='1',source_id='invented',event_id=str(i),
        timestamp_utc=(date(2020,1,1)+timedelta(days=i)).isoformat()+'T09:00:00Z',
        event_type='buy',account_id='Desk',asset_id='AUR',quantity='1',
        gross_amount='10',currency='EUR',fee_amount='0')) for i in range(count))
    selected_ids = [r.identity for r in rows] if selected else ()
    original_hash, original_book = TransactionRow.identity.fget, SecurityBook.__post_init__
    hashes, validations = [], []
    def hashed(self):
        hashes.append(True)
        return original_hash(self)
    def validated(self):
        validations.append(True)
        return original_book(self)
    monkeypatch.setattr(TransactionRow,'identity',property(hashed))
    monkeypatch.setattr(SecurityBook,'__post_init__',validated)
    result = prepare_import(Journal(),holdings(),rows,MAPPING,selected_ids=selected_ids,starts_at_zero=True)
    assert result.added == count and result.applied == (count if selected else 0)
    assert len(hashes) <= 6*count and len(validations) < 12
    restored = journal_from_dict(journal_to_dict(result.journal))
    assert restored == result.journal
    if selected:
        assert replay_securities(restored.securities).positions['desk'].quantity == count


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
    from portfolio_app.ledger.security_history import SecurityBook,SecurityEvent,SecurityLot
    from portfolio_app.performance.cost_basis import component
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
    from portfolio_app.ledger.security_history import SecurityBook,SecurityEvent
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
    from portfolio_app.performance.history_scope import can_start_history_chart
    j=activated().journal
    assert can_start_history_chart(j,HistoryScope(('orb',)))
    bad=normalize({**scenario()[0].raw,'timestamp_utc':'2024-04-01T09:00:00.123456789Z'})
    assert bad.issues


def test_linked_cash_is_explicit_and_internal_payments_cancel():
    from portfolio_app.ledger.account_journal import Account,Event
    j=Journal(accounts=(Account('funding','Invented cash'),),events=(Event('cash-open','2024-03-31','cash_open','funding','EUR',D(300)),))
    rows=scenario()
    result=prepare_import(j,holdings(),rows,MAPPING,cash_accounts={'Desk':'funding'},selected_ids=[r.identity for r in rows],starts_at_zero=True)
    assert replay(result.journal).cash['funding','EUR']==98
    flows,missing=external_flows(result.journal,HistoryScope(tuple(MAPPING.values()),('funding',)),ObservationStore(),'2024-03-31','2024-04-08','EUR')
    assert not missing and sum(f.amount for f in flows)==-172
    assert len(result.journal.events)==1  # security cash remains derived


def test_sub_cent_linked_cash_is_not_silently_rounded():
    from portfolio_app.ledger.account_journal import Account,Event
    j=Journal(accounts=(Account('funding','Invented cash'),),events=(Event('cash-open','2024-03-31','cash_open','funding','EUR',D(300)),))
    row=normalize({**scenario()[0].raw,'gross_amount':'100.004'})
    with pytest.raises(DataError,match='sub-cent'):
        prepare_import(j,holdings(),[row],MAPPING,cash_accounts={'Desk':'funding'},selected_ids=[row.identity],starts_at_zero=True)
    retained=prepare_import(j,holdings(),[row],MAPPING)
    assert retained.journal.transaction_imports[0].facts['gross_amount']=='100.004'
    assert replay(retained.journal).cash['funding','EUR']==300


def test_keep_source_variant_preserves_authority_pending_coverage_and_legacy_shape():
    from portfolio_app.performance.personal_performance import unresolved_receipts
    original = scenario()[0]
    active = activated([original]).journal
    assert 'kept_imports' not in journal_to_dict(active)
    changed = normalize({**original.raw, 'gross_amount': '110'})
    further = normalize({**original.raw, 'gross_amount': '120'})
    pending = prepare_import(active, holdings(), [further], MAPPING, correction_ids=[further.identity]).journal
    result = prepare_import(pending, holdings(), [changed], {}, keep_ids=[changed.identity])
    kept = result.journal
    assert kept.version == 6 and result.applied == 0
    assert kept.securities == pending.securities and kept.transaction_imports == pending.transaction_imports
    assert unresolved_receipts(kept) == unresolved_receipts(pending)
    assert len(kept.kept_imports) == 1 and kept.kept_imports[0].replaces == further.identity
    assert journal_from_dict(journal_to_dict(kept)) == kept
    # A repeat carrying the “select all” default cannot apply a remembered variant.
    again = prepare_import(kept, holdings(), [changed], {}, selected_ids=[changed.identity])
    assert again.journal == kept and again.applied == again.added == 0
    with pytest.raises(DataError, match='different facts'):
        prepare_import(kept, holdings(), [normalize({**original.raw, 'gross_amount': '111'})], {})
    with pytest.raises(DataError, match='conflicting import decisions'):
        prepare_import(kept, holdings(), [changed], {}, correction_ids=[changed.identity])
    with pytest.raises(DataError, match='changed facts'):
        prepare_import(active, holdings(), [original], MAPPING, keep_ids=[original.identity])


def test_remembered_decisions_ignore_incidental_layout_but_respect_explicit_changes():
    from portfolio_app.ingestion.transaction_csv import read_csv
    header = 'reference,date,type,asset,quantity,amount,currency,order'
    a = 'a,2024-02-02,buy,AUR,1,10,EUR,'
    b = 'b,2024-02-02,buy,AUR,1,20,EUR,'
    def upload(*lines):
        return read_csv('\n'.join((header, *lines)))
    base = prepare_import(Journal(), holdings(), upload(a, b), {}).journal
    changed = a.replace(',10,', ',11,')
    row = upload(changed)[0]
    kept = prepare_import(base, holdings(), [row], {}, keep_ids=[row.identity]).journal
    rearranged = upload(b, changed.replace(',11,', ',11.00,'))
    repeat = prepare_import(kept, holdings(), rearranged, {})
    assert repeat.journal == kept
    with pytest.raises(DataError, match='different facts'):
        prepare_import(kept, holdings(), upload(changed + '9'), {})


def test_reconsider_keeps_comparison_and_authority_lineages_after_later_edit_and_removal():
    from portfolio_app.ledger.history_commands import correct_transaction, remove_transactions, replace_book
    original = scenario()[0]
    active = activated([original]).journal
    declined = normalize({**original.raw, 'gross_amount': '110'})
    latest = normalize({**original.raw, 'gross_amount': '120'})
    kept = prepare_import(active, holdings(), [declined], {}, keep_ids=[declined.identity]).journal
    changed = prepare_import(kept, holdings(), [latest], MAPPING, selected_ids=[latest.identity],
                             correction_ids=[latest.identity]).journal
    book = changed.securities[0]
    edited = correct_transaction(book, latest.identity, quantity=D('9'))
    edited = replace(edited, active_ids=tuple(i for i in edited.active_ids if i != latest.identity) + (edited.records[-1].id,))
    removed = remove_transactions(edited, [edited.records[-1].id])
    changed = replace_book(changed, removed)
    assert changed.version == 6 and changed.kept_imports == kept.kept_imports
    assert prepare_import(changed, holdings(), [declined], MAPPING, selected_ids=[declined.identity]).journal == changed
    choices = dict(correction_ids=[declined.identity], reconsider_ids=[declined.identity])
    with pytest.raises(DataError, match='Review the saved keep decision'):
        prepare_import(changed, holdings(), [declined], MAPPING, **choices)
    restored = prepare_import(changed, holdings(), [declined], MAPPING, selected_ids=[declined.identity], **choices).journal
    assert restored.kept_imports[0].replaces == original.identity
    assert restored.transaction_imports[-1].replaces == latest.identity
    assert restored.securities[0].records[-1].replaces == removed.records[-1].id
    assert restored.securities[0].records[-1].id == declined.identity
    assert journal_from_dict(journal_to_dict(restored)) == restored
    removed_again = replace_book(restored, remove_transactions(restored.securities[0], [declined.identity]))
    assert removed_again.kept_imports == kept.kept_imports
    again = prepare_import(removed_again, holdings(), [declined], MAPPING, selected_ids=[declined.identity])
    assert again.journal == removed_again and again.applied == 0


def test_explicit_reconsideration_uses_the_saved_version_after_a_later_order_change():
    header = 'reference,date,type,asset,quantity,amount,currency,order\n'
    def source(amount, order=''):
        return read_csv(header + f'a,2024-01-02,buy,AUR,1,{amount},EUR,{order}\n')[0]
    first, declined, reordered = source(10), source(11), source(11, 7)
    mapping = {('Portfolio', 'AUR'): 'desk'}
    applied = prepare_import(Journal(), holdings(), [first], mapping, selected_ids=[first.identity], starts_at_zero=True).journal
    kept = prepare_import(applied, holdings(), [declined], {}, keep_ids=[declined.identity]).journal
    later = prepare_import(kept, holdings(), [reordered], mapping, correction_ids=[reordered.identity],
                           selected_ids=[reordered.identity]).journal
    recovered = prepare_import(later, holdings(), [declined], mapping, reconsider_ids=[declined.identity],
                               correction_ids=[declined.identity], selected_ids=[declined.identity]).journal
    assert recovered.transaction_imports[-1].facts['sequence'] == declined.facts['sequence']
    assert recovered.transaction_imports[-1].replaces == reordered.identity
    assert recovered.kept_imports == kept.kept_imports
