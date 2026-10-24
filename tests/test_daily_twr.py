"""Independent economic examples; all amounts and identities are invented."""
from dataclasses import replace
from decimal import Decimal as D
import pytest

from portfolio_app.account_journal import Journal, Account, Event
from portfolio_app.security_history import SecurityBook, SecurityEvent as E, Charge, history_fingerprint
from portfolio_app.history_observations import Observation as O, ObservationStore, CoverageReview, HistoryScope
from portfolio_app.personal_performance import calculate_history, HistoryCalculation

START, END = '2024-01-01', '2024-01-10'


def example(events, *, quantity=0, prices=(), account=False, opening_cash=200):
    opening = E('opening', 'opening', START, quantity=quantity, amount=quantity*80, currency='EUR',
                source='confirmed_zero_start' if not quantity else 'manual')
    events = (opening, *events)
    book = SecurityBook('invented', 'SYNTH', (str(quantity), '80', 'EUR'), account_id='cash' if account else '',
                        records=events, active_ids=tuple(e.id for e in events))
    journal = Journal(version=2, securities=(book,), accounts=(Account('cash','Invented cash'),) if account else (),
        events=(Event('cash-opening', START, 'cash_open', 'cash', 'EUR', D(opening_cash)),) if account else ())
    scope = HistoryScope(('invented',), ('cash',) if account else ())
    observations = tuple(O(str(i),'price','invented',stamp,D(value),'EUR') for i,(stamp,value) in enumerate(prices))
    store = ObservationStore(observations,(CoverageReview('review',scope,START,END,history_fingerprint(journal)),))
    return journal,scope,store


def trade(identity, kind, stamp, price, fee=0, *, external=False, sequence=0):
    return E(identity,kind,stamp,quantity=1,price=price,currency='EUR',charges_known=True,sequence=sequence,
             charges=(Charge(D(fee),'EUR',external=external),) if fee else ())


@pytest.mark.parametrize('fee',[0,5])
def test_single_purchase_from_zero_needs_no_cancelling_trade_day_close(fee):
    args = example((trade('buy','buy','2024-01-02',100,fee),),prices=((END,110),))
    for method in ('daily','exact'):
        result = calculate_history(*args,START,END,method=method)
        assert result.time_weighted == pytest.approx(110/(100+fee)-1)
        assert result.total_gain == 10-fee and result.money_weighted is not None
        assert result.time_weighted_method == method


def test_net_sale_fee_and_distribution_fee_are_not_separate_contributions():
    args = example((trade('sale','sell','2024-01-03',110,5),),quantity=1,prices=((START,100),))
    for method in ('daily','exact'):
        assert calculate_history(*args,START,END,method=method).time_weighted == pytest.approx(.05)
    distribution=E('dividend','distribution','2024-01-03',amount=10,currency='EUR',charges_known=True,charges=(Charge(D(1),'EUR'),))
    args=example((distribution,),quantity=1,prices=((START,100),('2024-01-03',100),(END,110)))
    # 9% distribution day, then 10% market appreciation (reinvestment convention).
    assert calculate_history(*args,START,END).time_weighted == pytest.approx(1.09*1.1-1)


def test_same_day_round_trip_has_one_inflow_and_net_sale_outflow():
    args=example((trade('buy','buy','2024-01-02',100,5),trade('sale','sell','2024-01-02',110,5)))
    result=calculate_history(*args,START,END)
    assert result.time_weighted == 0 and result.total_gain == 0
    # Genuine independent event ordering is still required for exact TWR.
    assert calculate_history(*args,START,END,method='exact').time_weighted is None


@pytest.mark.parametrize('external, expected',[(False,205/200-1),(True,210/205-1)])
def test_linked_cash_expense_and_external_fee_funding_count_once(external,expected):
    args=example((trade('buy','buy','2024-01-02',100,5,external=external),),account=True,prices=((END,110),))
    result=calculate_history(*args,START,END)
    assert result.time_weighted == pytest.approx(expected)
    assert result.total_gain == 5


def test_separately_dated_fee_and_tax_use_their_actual_currency_and_date():
    purchase=replace(trade('buy','buy','2024-01-02',100), charges=(Charge(D(5),'USD','2024-01-04',kind='tax'),))
    journal,scope,store=example((purchase,),prices=(('2024-01-03',110),(END,120)))
    assert calculate_history(journal,scope,store,START,END).time_weighted is None
    store=replace(store,observations=(*store.observations,O('fx','fx','USD/EUR','2024-01-04',D('.8'),'USD',target='EUR')))
    result=calculate_history(journal,scope,store,START,END)
    assert result.time_weighted == pytest.approx((110/100)*(120/114)-1)
    assert result.total_gain == 16


def test_missing_required_boundary_only_blocks_twr():
    args=example((trade('buy','buy','2024-01-03',100),),quantity=1,prices=((START,100),(END,110)))
    result=calculate_history(*args,START,END)
    assert result.money_weighted is not None and result.time_weighted is None
    assert result.time_weighted_reasons[0].kind == 'boundary'
    assert '2024-01-02' in result.time_weighted_reasons[0]


def test_daily_prefix_reuse_matches_independent_daily_chain_out_of_order():
    events=(trade('buy1','buy','2024-01-02',100,5),trade('buy2','buy','2024-01-04',120),trade('sell','sell','2024-01-06',130,2))
    # Independently given daily equity, contributions and withdrawals.
    values={START:0,'2024-01-02':100,'2024-01-03':110,'2024-01-04':240,'2024-01-05':250,
            '2024-01-06':130,'2024-01-07':135,'2024-01-08':140,'2024-01-09':145,END:150}
    prices=tuple((stamp,value/(2 if '2024-01-04'<=stamp<='2024-01-05' else 1)) for stamp,value in values.items() if value)
    j,scope,store=example(events,prices=prices)
    context=HistoryCalculation(j,store)
    expected={};factor=D(1);previous=D(0)
    for stamp,value in sorted(values.items()):
        incoming=D(105 if stamp=='2024-01-02' else 120 if stamp=='2024-01-04' else 0)
        outgoing=D(128 if stamp=='2024-01-06' else 0)
        if previous+incoming:
            factor *= (D(value)+outgoing)/(previous+incoming)
        expected[stamp]=float(factor-1);previous=D(value)
    for stamp in (END,'2024-01-04','2024-01-07','2024-01-03'):
        actual=calculate_history(j,scope,store,START,stamp,calculation=context)
        assert actual.time_weighted == pytest.approx(expected[stamp])
    # Snapshot acquisition cost (80/unit) never enters the return denominator.
    snapshot=example((),quantity=1,prices=((START,100),(END,110)))
    result=calculate_history(*snapshot,START,END)
    assert result.total_gain==30 and result.time_weighted==pytest.approx(.1)


def test_liquidation_reentry_and_negative_equity_gate():
    events=(trade('buy','buy','2024-01-02',100),trade('sell','sell','2024-01-04',110),trade('again','buy','2024-01-06',50))
    args=example(events,prices=((END,60),))
    assert calculate_history(*args,START,END).time_weighted==pytest.approx(1.1*1.2-1)
    assert calculate_history(*args,START,END,negative_dates=('2024-01-03',)).time_weighted is None
