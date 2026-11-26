"""Return qualification follows the monetary inputs used by the selected interval."""
from decimal import Decimal

import pytest

from portfolio_app.ledger.account_journal import Account, Event, Journal
from portfolio_app.ledger.security_history import Charge, SecurityBook, SecurityEvent
from portfolio_app.performance.history_observations import HistoryScope, Observation, ObservationStore
from portfolio_app.performance.personal_performance import HistoryCalculation, calculate_history, estimated_return_inputs


def journal_for(event):
    opening = SecurityEvent('open', 'opening', '2024-01-01', quantity=1, amount=10, currency='EUR')
    book = SecurityBook('stock', 'invented', ('1', '10', 'EUR'), account_id='cash',
                        records=(opening, event), active_ids=(opening.id, event.id))
    return Journal(version=5, securities=(book,), accounts=(Account('cash', 'Invented cash'),),
                   events=(Event('opening-cash', '2024-01-01', 'cash_open', 'cash', 'EUR', 100),))


@pytest.mark.parametrize('external', [True, False])
def test_separately_dated_estimated_fee_uses_its_own_date_and_payer(external):
    event = SecurityEvent('reward', 'reward', '2024-01-02', quantity=1, estimated=True,
                          charges=(Charge(Decimal(2), 'EUR', date='2024-01-04', external=external),))
    journal = journal_for(event)
    observations = ObservationStore(tuple(Observation(str(d), 'price', 'stock', f'2024-01-0{d}', 10, 'EUR')
                                          for d in range(1, 7)))
    calculation = HistoryCalculation(journal, observations)
    for scope, fee_inside in ((HistoryScope(('stock',)), True), (HistoryScope(accounts=('cash',)), not external),
                              (HistoryScope(('stock',), ('cash',)), True)):
        for start, end, expected in (('01', '03', False), ('03', '04', fee_inside), ('02', '05', fee_inside),
                                     ('04', '06', False), ('01', '03', False)):
            first, last = '2024-01-'+start, '2024-01-'+end
            assert estimated_return_inputs(journal, scope, first, last, calculation=calculation) == expected
            result = calculate_history(journal, scope, observations, first, last, calculation=calculation)
            assert result.estimated == expected
            assert result == calculate_history(journal, scope, observations, first, last)


@pytest.mark.parametrize('supplied', [False, True])
def test_transfer_qualifies_the_amount_used_for_its_flow(supplied):
    event = SecurityEvent('transfer', 'transfer_in', '2024-01-02', quantity=1,
                          amount=12 if supplied else None, currency='EUR', estimated=True,
                          valuation_amount=None if supplied else 30, valuation_currency='' if supplied else 'EUR')
    journal = journal_for(event)
    scope = HistoryScope(('stock',))
    prices = ObservationStore((Observation('mark', 'price', 'stock', '2024-01-02', 10, 'EUR'),))
    calculation = HistoryCalculation(journal, prices)
    flows, _ = calculation.flows(scope, '2024-01-01', '2024-01-03', 'EUR')
    assert [(flow.amount, flow.estimated) for flow in flows] == [(12 if supplied else 10, supplied)]
    assert estimated_return_inputs(journal, scope, '2024-01-01', '2024-01-03', calculation=calculation) == supplied


def test_supplied_zero_distribution_retains_estimate_but_zero_charge_does_not():
    scope = HistoryScope(('stock',))
    zero_cash = SecurityEvent('paid', 'distribution', '2024-01-02', amount=0, currency='EUR', estimated=True)
    zero_charge = SecurityEvent('reward', 'reward', '2024-01-02', quantity=1, estimated=True,
                                charges=(Charge(Decimal(0), 'EUR'),))
    for event, expected in ((zero_cash, True), (zero_charge, False)):
        assert estimated_return_inputs(journal_for(event), scope, '2024-01-01', '2024-01-03') == expected
