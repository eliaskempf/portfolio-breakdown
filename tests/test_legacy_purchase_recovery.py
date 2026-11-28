"""Reconciliation examples from the v1 purchase format, using invented positions."""
import csv
from decimal import Decimal as D, localcontext
from io import StringIO
import json

import pytest

from portfolio_app.market.fx import DatedRate
from portfolio_app.performance.cost_basis import FIELD, active_components, read_details, resolve_cost
from portfolio_app.portfolio.holdings import DataError, parse_holdings
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.portfolio.purchases import save_purchase_batch


BUY_DATE = '2024-01-02'
ADD_DATE = '2024-03-02'


def purchase(units='2', price='10', day=BUY_DATE, fees='0'):
    return dict(shares=units, price=price, fees=fees, date=day)


def v1_batch(mode, opening, purchase_rows, quantity, average):
    return dict(version=1, mode=mode, saved_at='2024-04-01', currency='USD',
                opening=opening, purchases=purchase_rows, result_shares=quantity, result_average=average)


def opening(quantity, price):
    return dict(shares=quantity, acquisition_price=price, acquisition_currency='USD')


def content(batches, **edits):
    last = batches[-1]
    values = dict(id='invented', name='Invented', position_key='test', **opening(last['result_shares'], last['result_average']),
                  purchase_history=json.dumps(batches))
    values.update(edits)
    stream = StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(values))
    writer.writeheader()
    writer.writerow(values)
    return stream.getvalue()


class SyntheticFX:
    def get(self, source, target, day):
        assert (source, target) == ('USD', 'EUR')
        return DatedRate({BUY_DATE: 0.8, ADD_DATE: 0.9}[day], day, 'Invented historic FX')


def test_v1_reconcile_then_add_uses_recorded_balance_and_keeps_dated_amounts():
    quantity = '2.00000000000000001'
    avg = '9.9999999999999999500000000000000002500000000000000'
    first = v1_batch('reconcile', opening(quantity, '9'), [purchase()], quantity, avg)
    second = v1_batch('add', opening(quantity, avg), [purchase('0.5', '15', ADD_DATE)],
                      '2.50000000000000001', '10.999999999999999956000000000000000176000000000000')
    for batches, expected in (([first], D('16')), ([first, second], D('22.75'))):
        parts = active_components(parse_holdings(content(batches)).iloc[0])
        assert [(part['shares'], part['date']) for part in parts] == [('2', BUY_DATE), ('0.5', ADD_DATE)][:len(batches)]
        assert resolve_cost(parts, 'EUR', SyntheticFX()).exact_amount == expected
    wrong_opening = dict(second, opening=opening('2', avg))
    assert active_components(parse_holdings(content([first, wrong_opening])).iloc[0])[0]['date'] == ''


def test_thirty_one_digit_quantity_uses_writer_precision_for_shares_and_cost():
    units = '1.123456789012345678901234567891'
    batch = v1_batch('add', {}, [purchase(units)], units, '10')
    parts = active_components(parse_holdings(content([batch])).iloc[0])
    assert parts[0]['shares'] == units and parts[0]['date'] == BUY_DATE
    assert D(parts[0]['amount']) == D('11.23456789012345678901234567891')
    assert resolve_cost(parts, 'EUR', SyntheticFX()).amount is not None


@pytest.mark.parametrize('purchased,confirmed,valid', [
    ('2','2.000000001', True), ('2','2.000000001000000001', False),
    ('0.1000000001','0.1', True), ('0.1000000001000000001','0.1', False),
])
def test_allowance_applies_only_inside_the_reconciliation(purchased, confirmed, valid):
    with localcontext() as ctx:
        ctx.prec = 50
        avg = str(D(purchased) * D('10') / D(confirmed))
    batch = v1_batch('reconcile', opening(confirmed, '10'), [purchase(purchased)], confirmed, avg)
    row = parse_holdings(content([batch])).iloc[0]
    assert bool(active_components(row)[0]['date']) == valid
    # A mismatched CSV quantity, price or currency must still retire old costs.
    for changes in ({'shares':confirmed + '1'}, {'acquisition_price':avg+'1'}, {'acquisition_currency':'EUR'}):
        changed = parse_holdings(content([batch], **changes)).iloc[0]
        assert active_components(changed)[0]['date'] == ''


def test_followup_purchase_after_v1_reconcile_preserves_original_units_and_freezes_fx(tmp_path):
    path = tmp_path/'holdings.csv'
    quantity = '2.000000001'
    batch = v1_batch('reconcile', opening(quantity, '8'), [purchase()], quantity,
                     '9.9999999950000000024999999987500000006249999996875')
    path.write_text(content([batch]))
    before = path.read_bytes()
    snapshot = read_snapshot(path)
    assert active_components(snapshot.holdings.iloc[0])[0]['date'] == BUY_DATE
    assert path.read_bytes() == before
    for additional in range(1, 3):
        save_purchase_batch(path, {}, [purchase('0.5', '15', ADD_DATE)], currency='USD', expected_revision=snapshot.revision,
                            position_id='test', historical=SyntheticFX(), allow_repeat=True)
        snapshot = read_snapshot(path)
        row = snapshot.holdings.iloc[0]
        parts = active_components(row)
        assert [p['date'] for p in parts] == [BUY_DATE] + [ADD_DATE]*additional
        assert [p['shares'] for p in parts] == ['2'] + ['0.5']*additional
        assert resolve_cost(parts, 'EUR').exact_amount == D('16') + additional * D('6.75')
        assert all(p['conversions']['EUR']['method'] == 'historical' for p in parts)
    document = json.loads(row[FIELD])
    with pytest.raises(DataError):
        read_details(json.dumps({**document, 'legacy_reconciliation': {'component_count': 1, 'confirmed_shares': '2.000000002'}}))
    with pytest.raises(DataError):
        read_details(json.dumps({k:v for k,v in document.items() if k != 'legacy_reconciliation'}))
