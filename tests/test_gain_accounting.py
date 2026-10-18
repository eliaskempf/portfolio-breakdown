"""Independent, hand-calculated accounting oracles; all facts are invented."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pandas as pd
import pytest

from portfolio_app.account_journal import Account, Event, Journal, replay
from portfolio_app.gain_accounting import current_component_values, summarize_known_gain
from portfolio_app.gain_components import security_gain_components
from portfolio_app.history_commands import reconstruction_candidate
from portfolio_app.history_observations import CoverageReview, HistoryScope, Observation as O, ObservationStore
from portfolio_app.history_view import history_view
from portfolio_app.personal_performance import calculate_history, ExternalFlow, time_weighted
from portfolio_app.security_history import Charge, SecurityBook, SecurityEvent as E, effective_holdings, history_fingerprint, replay_securities

START, END = '2024-01-01', '2025-01-01'


def book(*events, identity='stock', account=''):
    return SecurityBook(identity, 'INVENTED', ('0','',''), account_id=account,
                        records=events, active_ids=tuple(e.id for e in events))


def observations(journal, scope, prices=(), fx=(), *, start=START, review=True, extra=()):
    evidence = [O(str(i), 'price', identity, stamp, D(price), 'EUR') for i,(identity,stamp,price) in enumerate(prices)]
    evidence += [O('fx'+str(i), 'fx', source+'/'+target, stamp, D(rate), source, target=target)
                 for i,(source,target,stamp,rate) in enumerate(fx)]
    reviews = (CoverageReview('r', scope, start, END, history_fingerprint(journal)),) if review else ()
    return ObservationStore((*evidence,*extra), reviews)


def test_snapshot_cost_capital_and_market_return_are_independent():
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=100,amount=8000,currency='EUR')),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,[('stock',START,100),('stock',END,110)])
    result=calculate_history(j,scope,obs,START,END)
    assert (result.invested_capital,result.opening_known_gain,result.gain,result.total_gain)==(8000,2000,1000,3000)
    assert result.time_weighted == pytest.approx(.10)
    assert result.money_weighted == pytest.approx(1.1**(365/366)-1)
    all_view=history_view(j,scope,obs,'All',today=date(2025,1,1))
    assert all_view.points[0]['Total gain']==2000
    assert all_view.all_known.amount==3000 and all_view.all_known.partial
    assert all_view.all_known.value-all_view.all_known.capital==3000


def test_zero_inception_includes_first_payment_and_fee_and_xirr_residual():
    buy=E('b','buy',START,quantity=10,price=10,currency='EUR',charges=(Charge(D(2),'EUR'),),charges_known=True)
    original=book(buy)
    b=reconstruction_candidate(original,('b',),starts_at_zero=True)
    j=Journal(version=2,securities=(b,));scope=HistoryScope(('stock',))
    obs=observations(j,scope,[('stock',START,10),('stock',END,12)],start='2023-12-31')
    r=calculate_history(j,scope,obs,'2023-12-31',END)
    assert (r.opening_value,r.net_contributions,r.invested_capital,r.total_gain)==(0,102,102,18)
    assert -102+120/(1+r.money_weighted)**(366/365)==pytest.approx(0,abs=1e-10)
    assert r.time_weighted is None  # Price closes do not establish flow boundaries.
    flat=replace(obs,observations=(*obs.observations[:1],replace(obs.observations[1],amount=D(10))))
    assert calculate_history(j,scope,flat,'2023-12-31',END).total_gain == -2


def test_partial_sale_retains_realized_income_and_remaining_cost_separately():
    events=(E('o','opening',START),E('b','buy','2024-02-01',quantity=10,price=10,currency='EUR',charges_known=True),
            E('s','sell','2024-07-01',quantity=5,price=15,currency='EUR',charges_known=True),
            E('d','distribution','2024-08-01',amount=5,currency='EUR',charges_known=True))
    j=Journal(version=2,securities=(book(*events),));scope=HistoryScope(('stock',))
    obs=observations(j,scope,[('stock',END,20)])
    state=replay_securities(j.securities)
    assert state.positions['stock'].quantity==5
    assert sum(D(c['amount']) for c in state.positions['stock'].costs)==50
    assert sum(D(c['amount']) for lot in state.disposals[0].lots for c in lot.costs)==50
    r=calculate_history(j,scope,obs,START,END)
    assert (r.total_gain,r.invested_capital)==(80,20)  # 50 unrealized + 25 realized + 5 income.
    parts=security_gain_components(j,'stock',obs,END)
    assert (parts.remaining_cost,parts.unrealized,parts.realized,parts.income,parts.expenses)==(50,50,25,5,0)
    assert parts.purchase_payments==100 and parts.sale_proceeds==75
    assert parts.unrealized+parts.realized+parts.income-parts.expenses==r.total_gain
    current=pd.DataFrame([dict(position_id='stock',current_value_reporting=100,unrealized_gain_reporting=50)])
    assert summarize_known_gain(j,scope,obs,END,current_values=current_component_values(current,j),summary_rows=current).amount==80


def test_complete_liquidation_and_removed_row_preserve_gain_and_xirr():
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=10,amount=100,currency='EUR'),
        E('s','sell','2024-07-01',quantity=10,price=12,currency='EUR',charges=(Charge(D(2),'EUR'),),charges_known=True),
        E('d','distribution','2024-08-01',amount=5,currency='EUR',charges_known=True)),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,[('stock',START,10)])
    r=calculate_history(j,scope,obs,START,END)
    assert (r.ending_value,r.total_gain,r.invested_capital)==(0,23,-23)
    assert -100+118/(1+r.money_weighted)**(182/365)+5/(1+r.money_weighted)**(213/365)==pytest.approx(0,abs=1e-10)
    current=pd.DataFrame(columns=['position_id','current_value_reporting'])
    assert effective_holdings(current,j.securities).empty
    assert summarize_known_gain(j,scope,obs,END,current_values=current_component_values(current,j),summary_rows=current).amount==23


def test_removed_closed_gain_remains_in_disjoint_category_comparison():
    from portfolio_app.allocation import Allocation, Bucket
    from portfolio_app.gain_comparison import known_category_performance
    j=Journal(version=2,securities=(book(E('o','opening',START),
        E('b','buy','2024-02-01',quantity=1,price=100,currency='EUR',charges_known=True),
        E('s','sell','2024-07-01',quantity=1,price=120,currency='EUR',charges_known=True)),))
    scope=HistoryScope(('stock',));obs=observations(j,scope)
    current=pd.DataFrame(columns=['position_id','bucket_id','shares','current_value_reporting'])
    config=Allocation((Bucket('core','Core'),))
    result=known_category_performance(current,current,config,j,obs,current,today=date(2025,1,1))
    assert result.set_index('Category').loc['Unassigned','Gain']==20
    assert result.Gain.sum()==20
    leaf=known_category_performance(current,current,config,j,obs,current,'unassigned',today=date(2025,1,1))
    assert leaf.Gain.tolist()==[20]


def test_summary_security_account_association_does_not_turn_it_into_account_equity():
    from portfolio_app.allocation import Allocation, Bucket
    from portfolio_app.gain_comparison import known_category_performance
    j=Journal(accounts=(Account('a','Invented'),))
    rows=pd.DataFrame([dict(position_id='summary',name='Invented security',shares=1,
        bucket_id='core',account_id='a',journal_managed=False,current_value_reporting=120,
        unrealized_gain_reporting=20,cost_basis_reporting=100)])
    result=known_category_performance(rows,rows,Allocation((Bucket('core','Core'),)),j,
        ObservationStore(),rows,'core',today=date(2025,1,1))
    assert result.Gain.tolist()==[20] and result.Status.tolist()==['Partial']


@pytest.mark.parametrize('opening,ending,flow,before,after',[(0,120,100,0,100),(100,0,-120,120,0)])
def test_exact_twr_supports_initial_funding_and_terminal_liquidation(opening,ending,flow,before,after):
    scope=HistoryScope(('stock',));flow=ExternalFlow('f','2024-07-01',0,D(flow))
    obs=ObservationStore(tuple(O(side,'value',scope.key,flow.date,D(value),'EUR',side=side,boundary='f')
                               for side,value in [('before',before),('after',after)]))
    result,reason=time_weighted(D(opening),D(ending),(flow,),obs,scope,'EUR')
    assert not reason and result==pytest.approx(.2)


def test_exact_twr_links_subperiods_and_empty_reopening_without_fabricated_gain():
    scope=HistoryScope(('stock',))
    flows=(ExternalFlow('exit','2024-03-01',0,D(-120)),ExternalFlow('reopen','2024-07-01',0,D(50)))
    obs=ObservationStore(tuple(O(f.id+side,'value',scope.key,f.date,D(value),'EUR',side=side,boundary=f.id)
        for f,pair in zip(flows,[(120,0),(0,50)]) for side,value in zip(('before','after'),pair)))
    assert time_weighted(D(100),D(55),flows,obs,scope,'EUR')[0]==pytest.approx(1.2*1.1-1)
    assert time_weighted(D(100),D(0),(),ObservationStore(),scope,'EUR')[0]==-1
    assert time_weighted(D(0),D(120),(),ObservationStore(),scope,'EUR')[0] is None


def test_all_never_substitutes_later_start_and_known_cost_needs_no_opening_quote():
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=10,amount=100,currency='EUR')),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,[('stock','2024-07-01',11),('stock',END,12)])
    view=history_view(j,scope,obs,'All',today=date(2025,1,1))
    assert view.start==START and view.result.gain is None
    assert view.all_known.amount==20 and view.all_result.total_gain==20
    assert view.all_result.money_weighted is None and view.all_result.time_weighted is None


@pytest.mark.parametrize('treatment',['retain','withdraw','reinvest'])
def test_distribution_is_counted_once_inside_accounts(treatment):
    events=[E('o','opening',START),E('b','buy','2024-02-01',quantity=10,price=10,currency='EUR',charges_known=True),
            E('d','distribution','2024-07-01',amount=12,currency='EUR',charges_known=True)]
    cash=[Event('cash',START,'cash_open','a','EUR',D(100))]
    if treatment=='withdraw':
        cash.append(Event('withdraw','2024-07-01','cash_flow','a','EUR',D(-12),sequence=1))
    if treatment=='reinvest':
        events.append(E('reinvest','buy','2024-07-01',sequence=1,quantity=1,price=12,currency='EUR',charges_known=True))
    j=Journal(version=2,securities=(book(*events,account='a'),),accounts=(Account('a','Invented'),),events=tuple(cash))
    scope=HistoryScope(('stock',),('a',));obs=observations(j,scope,[('stock',END,12)])
    r=calculate_history(j,scope,obs,START,END)
    assert replay(j).cash['a','EUR']==(12 if treatment=='retain' else 0)
    assert r.total_gain==32
    assert summarize_known_gain(j,scope,obs,END).amount==32


def test_foreign_sale_fee_and_income_use_separate_dated_fx():
    events=(E('o','opening',START),E('b','buy','2024-02-01',quantity=10,price=10,currency='USD',charges=(Charge(D(2),'GBP'),),charges_known=True),
        E('s','sell','2024-07-01',quantity=10,price=12,currency='USD',charges=(Charge(D(1),'USD'),),charges_known=True),
        E('d','distribution','2024-08-01',amount=5,currency='USD',charges_known=True))
    j=Journal(version=2,securities=(book(*events),));scope=HistoryScope(('stock',))
    fx=[('USD','EUR','2024-02-01','.8'),('GBP','EUR','2024-02-01','1.2'),('USD','EUR','2024-07-01','.9'),('USD','EUR','2024-08-01','.85')]
    obs=observations(j,scope,fx=fx)
    assert calculate_history(j,scope,obs,START,END).total_gain==D('28.95')  # 107.10 + 4.25 - 80 - 2.40.
    parts=security_gain_components(j,'stock',obs,END)
    assert parts.realized==D('24.70') and parts.income==D('4.25') and parts.remaining_cost==0
    assert parts.purchase_payments==D('82.40') and parts.sale_proceeds==D('107.10')
    missing=replace(obs,observations=obs.observations[1:])
    r=calculate_history(j,scope,missing,START,END)
    assert r.ending_value==0 and r.total_gain is None and r.money_weighted is None


def test_mixed_history_and_summary_only_exposes_partial_subtotal_without_returns():
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=10,amount=100,currency='EUR'),
        E('d','distribution','2024-07-01',amount=5,currency='EUR',charges_known=True)),))
    scope=HistoryScope(('stock','summary','unknown'));obs=observations(j,scope,[('stock',START,10),('stock',END,12)])
    rows=pd.DataFrame([dict(position_id='stock',current_value_reporting=120,unrealized_gain_reporting=20),
        dict(position_id='summary',current_value_reporting=60,unrealized_gain_reporting=10),
        dict(position_id='unknown',current_value_reporting=40,unrealized_gain_reporting=None)])
    r=summarize_known_gain(j,scope,obs,END,current_values=current_component_values(rows,j),summary_rows=rows)
    assert r.amount==35 and r.value==220 and r.capital is None
    assert r.coverage=='2 of 3' and r.status=='Partial'
    assert calculate_history(j,scope,obs,START,END).money_weighted is None


def test_unknown_charges_withhold_aggregate_gain_and_complete_returns():
    j=Journal(version=2,securities=(book(E('o','opening',START),E('b','buy','2024-02-01',quantity=1,price=10,currency='EUR')),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,[('stock',END,12)])
    r=summarize_known_gain(j,scope,obs,END)
    assert r.amount is None and r.capital is None


def test_summary_estimate_remains_labelled_and_cannot_create_historical_capital():
    scope=HistoryScope(('summary',))
    rows=pd.DataFrame([dict(position_id='summary',shares=1,current_value_reporting=120,
                           unrealized_gain_reporting=20,cost_estimated=True)])
    result=summarize_known_gain(Journal(),scope,ObservationStore(),END,
        current_values={'summary':D(120)},summary_rows=rows)
    assert result.amount==20 and result.partial and result.capital is None
    assert any('estimated cost conversion' in r for r in result.reasons)


def test_unreviewed_records_keep_known_closed_gains_but_never_enable_returns():
    j=Journal(version=2,securities=(book(E('o','opening',START),E('b','buy','2024-02-01',quantity=1,price=10,currency='EUR',charges_known=True),
        E('s','sell','2024-07-01',quantity=1,price=12,currency='EUR',charges_known=True)),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,review=False)
    r=summarize_known_gain(j,scope,obs,END)
    assert r.amount==2 and r.partial
    assert r.review_required_only and r.coverage_note == 'History not confirmed'
    assert calculate_history(j,scope,obs,START,END).money_weighted is None


def test_unknown_snapshot_cost_retains_measured_gain_but_not_capital():
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=10,currency='EUR')),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,[('stock',START,10),('stock',END,12)])
    r=summarize_known_gain(j,scope,obs,END)
    assert r.amount==20 and r.capital is None and r.partial
    assert not r.review_required_only and r.coverage_note == 'Incomplete gain history'
    assert calculate_history(j,scope,obs,START,END).time_weighted==pytest.approx(.2)


def test_backfill_replaces_snapshot_bridge_without_adding_shares_or_gain_twice():
    snapshot=E('snapshot','opening','2024-07-01',quantity=10,amount=80,currency='EUR')
    b=book(snapshot)
    j=Journal(version=2,securities=(b,));scope=HistoryScope(('stock',))
    obs=observations(j,scope,[('stock','2024-07-01',10),('stock',END,12)])
    assert summarize_known_gain(j,scope,obs,END).amount==40
    buy=E('buy','buy','2024-02-01',quantity=10,price=8,currency='EUR',charges_known=True)
    b=replace(b,records=(*b.records,buy),active_ids=('buy',))
    b=reconstruction_candidate(b,('buy',),starts_at_zero=True)
    j=replace(j,securities=(b,));obs=observations(j,scope,[('stock',END,12)])
    assert replay_securities(j.securities).positions['stock'].quantity==10
    assert summarize_known_gain(j,scope,obs,END).amount==40
    assert len(b.records)==3  # Original snapshot remains evidence, not authority.


def test_transfer_and_split_preserve_total_cost_and_scope_gain_conservation():
    a=book(E('o','opening',START),E('b','buy','2024-02-01',quantity=10,price=10,currency='EUR',charges_known=True),
        E('move','transfer','2024-07-01',quantity=4,other_position_id='other'))
    b=book(E('other-o','opening',START),identity='other')
    j=Journal(version=2,securities=(a,b));scope=HistoryScope(('stock','other'))
    obs=observations(j,scope,[('stock','2024-07-01',12),('stock',END,15),('other',END,15)])
    assert summarize_known_gain(j,scope,obs,END).amount==50
    assert summarize_known_gain(j,HistoryScope(('stock',)),obs,END).amount==38
    assert summarize_known_gain(j,HistoryScope(('other',)),obs,END).amount==12
    split=E('split','split','2024-09-01',quantity=2)
    a=replace(a,records=(*a.records,split),active_ids=(*a.active_ids,'split'))
    j=replace(j,securities=(a,b));obs=observations(j,scope,[('stock','2024-07-01',12),('stock',END,'7.5'),('other',END,15)])
    assert summarize_known_gain(j,scope,obs,END).amount==50


def test_account_equity_does_not_include_linked_ordinary_security_twice():
    j=Journal(accounts=(Account('a','Invented'),))
    rows=pd.DataFrame([dict(position_id='ordinary',account_id='a',journal_managed=False,current_value_reporting=120),
        dict(position_id='account-cash:a:EUR',account_id='a',journal_managed=True,current_value_reporting=50),
        dict(position_id='account-loan:a:EUR:L',account_id='a',journal_managed=True,current_value_reporting=-20)])
    values=current_component_values(rows,j)
    assert values['ordinary']==120 and values['account:a']==30


def test_latest_event_without_quote_does_not_shorten_full_history_return():
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=10,amount=100,currency='EUR'),
        E('b','buy',END,quantity=1,price=12,currency='EUR',charges_known=True)),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,[('stock',START,10),('stock','2024-12-31',12)])
    v=history_view(j,scope,obs,'All',today=date(2025,1,1))
    assert v.all_end=='2024-12-31'
    assert v.all_result.money_weighted is None and v.all_result.time_weighted is None
    assert 'latest recorded event' in v.all_result.money_weighted_reasons[0]


def test_negative_equity_blocks_percentages_and_same_day_has_no_annualization():
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=1,amount=100,currency='EUR')),))
    scope=HistoryScope(('stock',))
    negative=O('negative','value',scope.key,'2024-06-01',D(-1),'EUR')
    obs=observations(j,scope,[('stock',START,100),('stock',END,120)],extra=(negative,))
    r=calculate_history(j,scope,obs,START,END)
    assert r.total_gain==20 and r.money_weighted is None and r.time_weighted is None
    assert calculate_history(j,scope,obs,START,START).money_weighted is None


def test_cost_reconciliation_is_not_a_fabricated_cash_flow_or_lifetime_profit():
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=10,amount=80,currency='EUR'),
        E('fix','cost','2024-07-01',amount=100,currency='EUR')),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,[('stock',START,10),('stock',END,12)])
    r=calculate_history(j,scope,obs,START,END)
    assert r.ending_value==120 and r.total_gain is None
    assert any('boundary' in reason for reason in r.flow_reasons)


def test_first_day_account_fee_and_external_deposit_are_not_lost_in_opening_close():
    j=Journal(accounts=(Account('a','Invented'),),events=(
        Event('o',START,'cash_open','a','EUR',D(100)),
        Event('deposit',START,'cash_flow','a','EUR',D(50),sequence=1),
        Event('fee',START,'fee','a','EUR',D(2),sequence=2)))
    scope=HistoryScope(accounts=('a',));obs=observations(j,scope)
    r=calculate_history(j,scope,obs,START,END)
    assert (r.opening_value,r.ending_value,r.net_contributions,r.total_gain)==(100,148,50,-2)
    assert summarize_known_gain(j,scope,obs,END).amount==-2


def test_first_day_futures_fee_is_included_before_and_after_settlement():
    from portfolio_app.account_journal import PositionSpec
    from portfolio_app.derivative_types import ContractSpec
    contract=ContractSpec('INVENTED','2025-03-20','EUR',D(10),D(1))
    j=Journal(accounts=(Account('a','Invented'),),contracts=(contract,),positions=(PositionSpec('f','a',contract.id),),
        events=(Event('o',START,'cash_open','a','EUR',D(1000)),
                Event('trade',START,'trade',position_id='f',quantity=1,price=D(100),sequence=1),
                Event('fee',START,'fee',position_id='f',amount=D(2),sequence=2)))
    scope=HistoryScope(accounts=('a',))
    obs=observations(j,scope,[('f',END,110)])
    r=calculate_history(j,scope,obs,START,END)
    assert (r.opening_value,r.ending_value,r.total_gain)==(1000,1098,98)
    settled=replace(j,events=(*j.events,Event('settle','2024-07-01','settlement',position_id='f',price=D(105))))
    obs=observations(settled,scope,[('f',END,110)])
    assert calculate_history(settled,scope,obs,START,END).total_gain==98


def test_missing_value_retains_recorded_income_as_partial_without_inventing_equity():
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=10,currency='EUR'),
        E('d','distribution','2024-07-01',amount=5,currency='EUR',charges_known=True)),))
    scope=HistoryScope(('stock',));obs=observations(j,scope)
    r=summarize_known_gain(j,scope,obs,END)
    assert r.amount==5 and r.value is None and r.capital is None and r.partial


def test_cost_precision_and_purchase_tax_are_not_lost_or_counted_twice():
    fee=D('1.00000000000000000001')
    j=Journal(version=2,securities=(book(E('o','opening',START),
        E('b','buy','2024-02-01',quantity=1,price=10,currency='EUR',charges=(Charge(fee,'EUR'),Charge(D(2),'EUR',kind='tax')),charges_known=True)),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,[('stock',END,15)])
    r=calculate_history(j,scope,obs,START,END)
    parts=security_gain_components(j,'stock',obs,END)
    assert parts.remaining_cost==10+fee
    assert parts.purchase_payments==12+fee and parts.expenses==2
    assert r.total_gain==3-fee==parts.unrealized+parts.realized+parts.income-parts.expenses


def test_journal_negative_equity_blocks_returns_after_recovery_but_preserves_gain():
    j=Journal(accounts=(Account('a','Invented'),),events=(
        Event('o',START,'cash_open','a','EUR',D(100)),
        Event('fee','2024-06-01','fee','a','EUR',D(150)),
        Event('income','2024-12-01','cash_adjust','a','EUR',D(100))))
    scope=HistoryScope(accounts=('a',))
    obs=replace(observations(j,scope),classifications=(('income','income'),))
    from portfolio_app.personal_performance import value_at
    assert value_at(j,scope,obs,'2024-06-01','EUR').amount == -50
    r=calculate_history(j,scope,obs,START,END)
    assert r.ending_value == 50 and r.total_gain == -50
    assert r.money_weighted is None and r.time_weighted is None
    assert 'negative equity' in r.money_weighted_reasons[0]
    # The full-history and hover paths must retain the same guard after recovery.
    view=history_view(j,scope,obs,'All',today=date(2025,1,1))
    assert view.all_result.money_weighted is None
    assert view.points[-1]['Total gain'] == -50 and view.points[-1]['TWR'] is None
    # An interval entirely after recovery does not inherit earlier ineligibility.
    later=calculate_history(j,scope,obs,'2024-12-01',END)
    assert later.money_weighted == pytest.approx(0) and later.time_weighted == 0


@pytest.mark.parametrize('movement', ['price','fx'])
def test_price_or_fx_evidence_of_negative_equity_blocks_returns(movement):
    from portfolio_app.personal_performance import value_at
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=1,amount=100,currency='EUR')),),
        accounts=(Account('a','Invented'),),events=(
            Event('cash',START,'cash_open','a','EUR',D(0)),
            Event('debt',START,'debt_open','a','EUR',D(80),reference='loan')))
    scope=HistoryScope(('stock',),('a',))
    prices=[('stock',START,100),('stock','2024-06-01',50),('stock',END,120)]
    obs=observations(j,scope,prices)
    if movement == 'fx':
        obs=replace(obs,observations=tuple(
            O(str(i),'price','stock',stamp,D(100),'USD') for i,(_,stamp,_) in enumerate(prices)) + tuple(
            O('fx'+str(i),'fx','USD/EUR',stamp,D(value)/100,'USD',target='EUR')
            for i,(_,stamp,value) in enumerate(prices)))
    assert value_at(j,scope,obs,'2024-06-01','EUR').amount == -30
    r=calculate_history(j,scope,obs,START,END)
    assert r.opening_value == 20 and r.ending_value == 40 and r.total_gain == 20
    assert r.money_weighted is None and r.time_weighted is None
    assert 'negative equity' in r.time_weighted_reasons[0]


def test_negative_cash_covered_by_assets_is_not_negative_portfolio_equity():
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=1,amount=200,currency='EUR')),),
        accounts=(Account('a','Invented'),),events=(
            Event('cash',START,'cash_open','a','EUR',D(100)),
            Event('fee','2024-06-01','fee','a','EUR',D(150)),
            Event('income','2024-12-01','cash_adjust','a','EUR',D(100))))
    scope=HistoryScope(('stock',),('a',))
    obs=replace(observations(j,scope,[('stock',stamp,200) for stamp in
        (START,'2024-06-01','2024-12-01',END)]),classifications=(('income','income'),))
    assert replay(j,as_of='2024-06-01').cash['a','EUR'] == -50
    r=calculate_history(j,scope,obs,START,END)
    assert r.total_gain == -50 and r.ending_value == 250
    assert r.time_weighted == pytest.approx(250/300-1)
    assert r.money_weighted == pytest.approx((250/300)**(365/366)-1)
