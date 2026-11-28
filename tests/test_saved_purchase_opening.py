"""Previously shipped purchase and currency edits: original documents and exact openings."""
import csv
from decimal import Decimal as D, localcontext
from io import StringIO
import json

import pytest

from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.ledger.security_history import SecurityEvent
from portfolio_app.ledger.trade_commands import save_trade
from portfolio_app.performance.cost_basis import FIELD, active_components, resolve_cost
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.portfolio.purchases import save_purchase_batch
from test_legacy_purchase_recovery import ADD_DATE, BUY_DATE, content, opening, purchase, v1_batch


def prior_component(units='2', amount='20', day=BUY_DATE, target_cost='16'):
    return dict(shares=units, amount=amount, currency='USD', date=day, conversions={
        'EUR': dict(method='supplied_rate', rate='0.8', amount=target_cost, saved_at='2024-01-03')})


def csv_with_document(csv_text, summary, parts, **edits):
    row, = csv.DictReader(StringIO(csv_text))
    row.update(edits)
    row[FIELD] = json.dumps(dict(version=1, summary=summary, components=parts))
    stream = StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    return stream.getvalue()


def first_sale(path):
    before = path.read_bytes()
    snap = read_snapshot(path)
    event = SecurityEvent('sale', 'sell', '2024-07-02', quantity=D('1'), price=D('18'), currency='USD', charges_known=True)
    save_trade(path.parent, event, position_id='test', opening_date='2024-07-01',
               expected_holdings_revision=snap.revision, expected_journal_revision=None)
    assert path.read_bytes() == before
    return load_journal(path.parent)[0].securities[0].records[0]


def legacy_reconciliation(*, multiple=False):
    q = '2.000000000001'
    parts = [prior_component()]
    with localcontext() as context:
        context.prec = 50
        price = str(D('20') / D(q))
        rate_purchase = dict(purchase(), fx_rate='0.8')
        batches = [dict(v1_batch('reconcile', opening(q, '8'), [rate_purchase], q, price), version=2)]
        if multiple:
            total = str(D(q) + D('1'))
            avg = str((D(q)*D(price) + D('10')) / D(total))
            batches.append(dict(v1_batch('add', opening(q, price), [dict(purchase('1', day=ADD_DATE), fx_rate='0.8')],
                                         total, avg), version=2))
            q, price = total, avg
            parts.append(prior_component('1','10',ADD_DATE,'8'))
    return content(batches), [q,price,'USD'], parts


@pytest.mark.parametrize('multiple', [False, True])
def test_old_v2_document_retains_original_purchases_and_supplied_fx_on_first_sale(tmp_path, multiple):
    path = tmp_path/'holdings.csv'
    text, summary, parts = legacy_reconciliation(multiple=multiple)
    path.write_text(csv_with_document(text, summary, parts))
    event = first_sale(path)
    assert event.quantity == D(summary[0])
    assert [D(p['shares']) for p in event.costs] == [D('2.000000000001')] + ([D('1')] if multiple else [])
    assert all({k:v for k,v in actual.items() if k != 'shares'} == {k:v for k,v in original.items() if k != 'shares'}
               for actual, original in zip(event.costs, parts, strict=True))
    assert resolve_cost(event.costs, 'EUR').exact_amount == (24 if multiple else 16)


@pytest.mark.parametrize('multiple', [False, True])
def test_unmarked_currency_edit_projects_exactly_the_recorded_aggregate(tmp_path, multiple):
    path = tmp_path/'holdings.csv'
    quantity = '2.00000000000000001'
    csv_text = 'id,name,position_key,shares,acquisition_price,acquisition_currency\ninvented,Invented,test,'+quantity+',10,USD\n'
    parts = ([prior_component('0.5','5',BUY_DATE,'4'),prior_component('1.5','15',ADD_DATE,'12')]
             if multiple else [prior_component(day='')])
    path.write_text(csv_with_document(csv_text, ['2.0','10.0','USD'], parts))
    event = first_sale(path)
    assert sum(D(p['shares']) for p in event.costs) == D(quantity)
    assert list(p['conversions'] for p in event.costs) == list(p['conversions'] for p in parts)
    assert list(p['amount'] for p in event.costs) == list(p['amount'] for p in parts)
    assert resolve_cost(event.costs, 'EUR').exact_amount == 16


@pytest.mark.parametrize('field', ['date', 'amount', 'rate'])
def test_conflicting_v2_purchase_evidence_cannot_adjust_units(tmp_path, field):
    path = tmp_path/'holdings.csv'
    text, summary, parts = legacy_reconciliation()
    if field == 'rate':
        parts[0]['conversions']['EUR']['rate'] = '0.75'
    else:
        parts[0][field] = '2024-01-20' if field == 'date' else '19'
    path.write_text(csv_with_document(text, summary, parts))
    with pytest.raises(DataError, match='do not match the recorded shares'):
        first_sale(path)
    assert not load_journal(tmp_path)[0].securities


def test_colliding_edit_supersedes_exact_v2_purchase_evidence(tmp_path):
    path = tmp_path/'holdings.csv'
    text, summary, parts = legacy_reconciliation()
    changed = '2.00000000000100000001'
    path.write_text(csv_with_document(text, summary, parts, shares=changed))
    event = first_sale(path)
    assert event.quantity == D(changed)
    assert event.costs[0]['conversions'] == {} and event.costs[0]['date'] == ''


def test_new_reconcile_followed_by_add_then_first_sale_preserves_confirmed_units(tmp_path):
    path = tmp_path/'holdings.csv'
    path.write_text('id,name,position_key,shares,acquisition_price,acquisition_currency\ninvented,Invented,test,2.000000001,8,USD\n')
    for mode, units, date in [('reconcile', '2', BUY_DATE), ('add','1',ADD_DATE)]:
        save_purchase_batch(path, {}, [dict(purchase(units, day=date), fx_rate='0.8')], mode=mode,
                            currency='USD', position_id='test', expected_revision=read_snapshot(path).revision)
        parts = active_components(read_snapshot(path).holdings.iloc[0])
        assert parts[0]['shares'] == '2' and parts[0]['date'] == BUY_DATE
    event = first_sale(path)
    assert event.quantity == D('3.000000001') and D(event.costs[1]['shares']) == 1
    assert resolve_cost(event.costs, 'EUR').exact_amount == 24


def test_rounded_snapshot_cannot_adjust_unrelated_monetary_evidence(tmp_path):
    path = tmp_path/'holdings.csv'
    csv_text = 'id,name,position_key,shares,acquisition_price,acquisition_currency\ninvented,Invented,test,2.00000000000000001,10,USD\n'
    path.write_text(csv_with_document(csv_text, ['2.0','10.0','USD'], [prior_component(amount='19')]))
    with pytest.raises(DataError, match='do not match the recorded shares'):
        first_sale(path)
