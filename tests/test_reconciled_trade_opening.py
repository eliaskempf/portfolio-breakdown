"""An ordinary first trade can adopt a v1-reconciled holding without inventing lots."""
from decimal import Decimal as D, localcontext

import pytest

from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.ledger.security_history import SecurityEvent, SecurityLot, opening_costs, replay_securities
from portfolio_app.ledger.trade_commands import save_trade
from portfolio_app.performance.cost_basis import active_components, component, resolve_cost
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.portfolio.purchases import save_purchase_batch
from test_legacy_purchase_recovery import ADD_DATE, BUY_DATE, SyntheticFX, content, opening, purchase, v1_batch


@pytest.mark.parametrize(('confirmed', 'add_first'), [('2.000000001', False), ('1.999999999', True)])
def test_first_partial_sale_after_reconciliation_preserves_original_cost_evidence(tmp_path, confirmed, add_first):
    path = tmp_path/'holdings.csv'
    with localcontext() as ctx:
        ctx.prec = 50
        avg = str(D('22') / D(confirmed))
    batch = v1_batch('reconcile', opening(confirmed, '8'),
                     [purchase('0.5', '10'), purchase('1.5', '10', ADD_DATE, fees='2')], confirmed, avg)
    path.write_text(content([batch]))
    if add_first:
        snapshot = read_snapshot(path)
        save_purchase_batch(path, {}, [purchase('1', '12', ADD_DATE)], currency='USD', position_id='test',
                            expected_revision=snapshot.revision, historical=SyntheticFX())
    csv_before = path.read_bytes()
    snapshot = read_snapshot(path)
    original = active_components(snapshot.holdings.iloc[0])
    assert [D(p['shares']) for p in original[:2]] == [D('0.5'), D('1.5')]

    sell = SecurityEvent('sale', 'sell', '2024-06-02', quantity=D('1'), price=D('18'),
                         currency='USD', charges_known=True)
    save_trade(tmp_path, sell, position_id='test', expected_holdings_revision=snapshot.revision,
               expected_journal_revision=None, opening_date='2024-06-01')
    assert path.read_bytes() == csv_before
    journal, _ = load_journal(tmp_path)
    boundary, recorded_sale = journal.securities[0].records
    assert boundary.quantity == D(confirmed) + int(add_first)
    assert recorded_sale == sell
    assert [D(p['shares']) for p in boundary.costs[:2]] == [D(confirmed)/4, D(confirmed)*3/4]
    assert all({k:v for k,v in source.items() if k != 'shares'} == {k:v for k,v in saved.items() if k != 'shares'}
               for source, saved in zip(original, boundary.costs, strict=True))
    assert boundary.costs[2:] == tuple(original[2:])
    if add_first:
        assert all(p['conversions']['EUR']['method'] == 'historical' for p in boundary.costs)
    assert resolve_cost(boundary.costs, 'EUR', SyntheticFX()).exact_amount == D('19.30') + D('10.80') * int(add_first)
    state = replay_securities(journal.securities).positions['test']
    assert state.quantity == boundary.quantity - sell.quantity
    # A reconciled aggregate opening remains an aggregate, not new dated FIFO lots.
    assert all(p['amount'] == '' for p in state.costs)


def test_opening_cannot_use_a_reconciliation_for_a_different_quantity(tmp_path):
    path = tmp_path/'holdings.csv'
    confirmed = '2.000000001'
    batch = v1_batch('reconcile', opening(confirmed, '8'), [purchase()], confirmed,
                     '9.9999999950000000024999999987500000006249999996875')
    path.write_text(content([batch]))
    row = read_snapshot(path).holdings.iloc[0]
    with pytest.raises(DataError, match='Opening quantity differs'):
        opening_costs(row, D(confirmed) + D('1e-20'))
    with pytest.raises(DataError, match='do not match'):
        SecurityLot('ordinary', D(confirmed), (component('2', '20', 'USD', BUY_DATE),))


def test_first_trade_after_an_exact_csv_edit_uses_the_edited_aggregate(tmp_path):
    path = tmp_path/'holdings.csv'
    confirmed = '2.000000001'
    changed = '2.00000000100000000001'
    batch = v1_batch('reconcile', opening(confirmed, '8'), [purchase()], confirmed,
                     '9.9999999950000000024999999987500000006249999996875')
    path.write_text(content([batch], shares=changed))
    snapshot = read_snapshot(path)
    assert snapshot.holdings.iloc[0].shares == float(confirmed)
    sell = SecurityEvent('sale', 'sell', '2024-06-02', quantity=D('1'), price=D('18'),
                         currency='USD', charges_known=True)
    save_trade(tmp_path, sell, position_id='test', expected_holdings_revision=snapshot.revision,
               expected_journal_revision=None, opening_date='2024-06-01')
    boundary = load_journal(tmp_path)[0].securities[0].records[0]
    assert boundary.quantity == D(changed)
    assert len(boundary.costs) == 1
    assert boundary.costs[0]['date'] == ''
    assert boundary.costs[0]['conversions'] == {}
