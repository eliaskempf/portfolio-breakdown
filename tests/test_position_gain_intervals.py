from dataclasses import replace
from decimal import Decimal as D
import pytest

from portfolio_app.ledger.account_journal import Journal
from portfolio_app.ledger.security_history import AssetCharge, Charge, SecurityTimeline, replay_securities
from portfolio_app.performance import gain_components
from portfolio_app.performance.history_observations import ObservationStore
from portfolio_app.performance.personal_performance import HistoryCalculation
from portfolio_app.portfolio.holdings import DataError
from test_gain_accounting import book, E


def test_gain_facts_rebuild_only_for_affected_positions_and_dated_fees(monkeypatch):
    days = [f'2024-01-{d:02}' for d in range(1, 13)]
    stock = book(E('open-stock', 'opening', days[0], quantity=10, amount=100, currency='EUR'),
                 E('purchase', 'buy', days[6], quantity=2, price=12, currency='EUR', charges_known=False,
                   charges=(Charge(D(2), 'EUR', date=days[8]),)))
    other = book(E('open-other', 'opening', days[0], quantity=4, amount=44, currency='EUR'),
                 E('incoming', 'transfer', days[3], quantity=1, other_position_id='stock'),
                 E('other-buy', 'buy', days[5], quantity=1, price=15, currency='EUR', charges_known=True,
                   asset_charges=(AssetCharge('stock', D('.5')),)), identity='other')
    independent = book(E('open-separate', 'opening', days[0]),
                       *(E(f'u{i}', 'buy', day, quantity=1, price=9, currency='EUR', charges_known=True)
                         for i, day in enumerate(days[1:])), identity='unrelated')
    journal = Journal(version=5, securities=(stock, other, independent))
    store = ObservationStore()
    expected = {}
    for day in days:
        state = replay_securities(journal.securities, as_of=day)
        expected[day] = (gain_components.recorded_security_gains(stock, state, store, day, 'EUR'),
                         gain_components.partial_lot_costs(state.positions['stock'], store, 'EUR'))
    builds, lot_builds = [], []
    record, partial = gain_components.recorded_security_gains, gain_components.partial_lot_costs
    def counted(book, state, observations, end, target, selected):
        builds.append(end)
        return record(book, state, observations, end, target, selected)
    def counted_lots(*args):
        lot_builds.append(True)
        return partial(*args)
    monkeypatch.setattr(gain_components, 'recorded_security_gains', counted)
    monkeypatch.setattr(gain_components, 'partial_lot_costs', counted_lots)
    calc = HistoryCalculation(journal, store)
    for day in (*days, *reversed(days)):
        assert (calc.security_gains('stock', day, 'EUR'), calc.partial_lot_costs('stock', day, 'EUR')) == expected[day]
    assert builds == [days[i] for i in (0, 3, 5, 6, 8)]
    assert len(lot_builds) == len(builds)


@pytest.mark.parametrize('method', ['security_gains', 'partial_lot_costs'])
def test_unchanged_position_gain_still_validates_other_books(method):
    stock = book(E('own-open', 'opening', '2024-01-01', quantity=1, amount=10, currency='EUR'))
    other = book(E('other-open', 'opening', '2024-01-01'),
                 E('oversold', 'sell', '2024-01-03', quantity=2, price=10, currency='EUR', charges_known=True),
                 identity='other')
    valid = replace(other, records=(other.records[0], replace(other.records[1], kind='buy')))
    calculation = HistoryCalculation(Journal(version=5, securities=(stock, valid)), ObservationStore())
    # A Journal validates all its books at construction. Inject a failing
    # timeline to exercise the independent prefix guarantee of this query.
    calculation._security_timeline = SecurityTimeline((stock, other))
    query = getattr(calculation, method)
    before = query('stock', '2024-01-02', 'EUR')
    for day in ('2024-01-03', '2024-01-04'):
        with pytest.raises(DataError, match='exceeds'):
            query('stock', day, 'EUR')
        assert query('stock', '2024-01-02', 'EUR') == before
