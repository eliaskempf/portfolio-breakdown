"""Independent, hand-calculated accounting oracles; all facts are invented."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pandas as pd
import pytest

from portfolio_app.ledger.account_journal import Account, Event, Journal, replay
from portfolio_app.performance.gain_accounting import current_component_values, summarize_known_gain
from portfolio_app.performance.gain_components import security_gain_components
from portfolio_app.ledger.history_commands import reconstruction_candidate
from portfolio_app.performance.history_observations import CoverageReview, HistoryScope, Observation as O, ObservationStore
from portfolio_app.performance.history_view import history_view
from portfolio_app.performance.personal_performance import calculate_history, ExternalFlow, time_weighted
from portfolio_app.ledger.security_history import Charge, SecurityBook, SecurityEvent as E, effective_holdings, history_fingerprint, replay_securities

START, END = '2024-01-01', '2025-01-01'


def test_internal_account_transfer_needs_no_transfer_date_fx_for_combined_gain():
    from portfolio_app.ledger.account_journal import Account, Event
    journal = Journal(accounts=(Account('a', 'Invented A'), Account('b', 'Invented B')), events=(
        Event('open-a', START, 'cash_open', account_id='a', currency='USD', amount=100),
        Event('open-b', START, 'cash_open', account_id='b', currency='USD', amount=0),
        Event('move', '2024-07-01', 'transfer', account_id='a', other_account_id='b', currency='USD', amount=50)))
    scope = HistoryScope(accounts=('a', 'b'))
    obs = observations(journal, scope, fx=[('USD', 'EUR', START, '.9'), ('USD', 'EUR', END, '1')])
    assert calculate_history(journal, scope, obs, START, END).total_gain == 10
    assert summarize_known_gain(journal, scope, obs, END).amount == 10
    assert summarize_known_gain(journal, HistoryScope(accounts=('a',)), obs, END).amount is None


@pytest.mark.parametrize('kind,known_amount,expected', [('sell', 120, 20), ('distribution', 20, 20), ('fee', 20, -20)])
def test_unknown_event_does_not_erase_supported_gain_subtotals(kind, known_amount, expected):
    events = (E('o', 'opening', START),
        E('b', 'buy', '2024-02-01', quantity=2, amount=200, currency='EUR', charges_known=True),
        E('known', kind, '2024-03-01', quantity=1 if kind == 'sell' else 0,
          amount=known_amount, currency='EUR', charges_known=True),
        E('unknown', kind, '2024-04-01', quantity=1 if kind == 'sell' else 0,
          currency='EUR', charges_known=True))
    journal = Journal(version=5, securities=(book(*events),))
    value = D(0 if kind == 'sell' else 200)
    components = security_gain_components(journal, 'stock', ObservationStore(), END, current_value=value)
    field = {'sell': 'realized', 'distribution': 'income', 'fee': 'expenses'}[kind]
    assert getattr(components, field) is None
    assert getattr(components, 'known_'+field) == abs(expected)
    summary = summarize_known_gain(journal, HistoryScope(('stock',)), ObservationStore(), END, current_values={'stock': value})
    assert summary.amount == expected and summary.partial


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
    assert r.time_weighted == pytest.approx(120/102-1)  # Daily return includes the purchase fee.
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
    from portfolio_app.portfolio.allocation import Allocation, Bucket
    from portfolio_app.performance.gain_comparison import known_category_performance
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
    from portfolio_app.portfolio.allocation import Allocation, Bucket
    from portfolio_app.performance.gain_comparison import known_category_performance
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


def test_applied_records_enable_known_closed_gains_and_returns_without_attestation():
    j=Journal(version=2,securities=(book(E('o','opening',START),E('b','buy','2024-02-01',quantity=1,price=10,currency='EUR',charges_known=True),
        E('s','sell','2024-07-01',quantity=1,price=12,currency='EUR',charges_known=True)),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,review=False)
    r=summarize_known_gain(j,scope,obs,END)
    assert r.amount==2 and not r.partial
    assert r.coverage_note == 'Complete gain history'
    assert calculate_history(j,scope,obs,START,END).money_weighted is not None


def test_unknown_snapshot_cost_retains_measured_gain_but_not_capital():
    j=Journal(version=2,securities=(book(E('o','opening',START,quantity=10,currency='EUR')),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,[('stock',START,10),('stock',END,12)])
    r=summarize_known_gain(j,scope,obs,END)
    assert r.amount==20 and r.capital is None and r.partial
    assert r.coverage_note == 'Incomplete gain history'
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
        E('fix','cost','2024-07-01',amount=90,currency='EUR')),))
    scope=HistoryScope(('stock',));obs=observations(j,scope,[('stock',START,10),('stock',END,12)])
    r=calculate_history(j,scope,obs,START,END)
    assert r.ending_value==120 and r.total_gain is None
    assert r.net_contributions == 0 and r.gain == 20
    assert r.time_weighted == pytest.approx(.2)
    assert r.money_weighted == pytest.approx(1.2**(365/366)-1)
    assert not r.flow_reasons
    assert any(reason.kind == 'cost_basis' for reason in r.capital_reasons)
    # Retain the corrected unrealized amount, not gain against the old cost.
    summary = summarize_known_gain(j, scope, obs, END)
    assert summary.amount == 30 and summary.partial and summary.capital is None


def test_cost_reconciliation_needs_no_dated_fx_for_market_returns_or_unrelated_scopes():
    from portfolio_app.performance.personal_performance import HistoryCalculation
    correction = E('fix', 'cost', '2024-07-01', amount=90, currency='USD')
    stock = book(E('o', 'opening', START, quantity=10, amount=80, currency='EUR'), correction)
    other = book(E('other-open', 'opening', START, quantity=1, amount=100, currency='EUR'), identity='other')
    j = Journal(version=2, securities=(stock, other))
    scope = HistoryScope(('stock', 'other'))
    obs = observations(j, scope, [('stock', START, 10), ('stock', END, 12),
                                 ('other', START, 100), ('other', END, 110)])
    calculation = HistoryCalculation(j, obs)
    result = calculate_history(j, scope, obs, START, END, calculation=calculation)
    assert result.gain == 30 and result.net_contributions == 0
    assert result.time_weighted == pytest.approx(.15)
    assert result.money_weighted == pytest.approx(1.15**(365/366)-1)
    assert result.invested_capital is None and result.total_gain is None
    assert not result.time_weighted_reasons and not result.money_weighted_reasons
    unrelated = calculate_history(j, HistoryScope(('other',)), obs, START, END, calculation=calculation)
    assert unrelated.total_gain == 10 and not unrelated.capital_reasons
    before = calculate_history(j, scope, obs, START, START, calculation=calculation)
    assert before.total_gain == 20 and not before.capital_reasons


@pytest.mark.parametrize('kind', ['note', 'opening'])
def test_cost_reconciliation_does_not_bypass_real_inventory_boundaries(kind):
    boundary = (E('gap', 'note', '2024-08-01', note='Invented unresolved movement') if kind == 'note'
                else E('gap', 'opening', '2024-08-01', quantity=10, amount=90, currency='EUR'))
    j = Journal(version=2, securities=(book(
        E('o', 'opening', START, quantity=10, amount=80, currency='EUR'),
        E('fix', 'cost', '2024-07-01', amount=90, currency='EUR'), boundary),))
    scope = HistoryScope(('stock',))
    obs = observations(j, scope, [('stock', START, 10), ('stock', END, 12)])
    result = calculate_history(j, scope, obs, START, END)
    assert result.gain is None and result.time_weighted is None and result.money_weighted is None
    assert result.flow_reasons


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
    from portfolio_app.ledger.account_journal import PositionSpec
    from portfolio_app.derivatives.derivative_types import ContractSpec
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
    from portfolio_app.performance.personal_performance import value_at
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
    from portfolio_app.performance.personal_performance import value_at
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


@pytest.mark.parametrize('with_price', [False, True])
def test_partial_gain_reuses_dated_ledger_without_changing_supported_components(monkeypatch, with_price):
    from portfolio_app.performance.personal_performance import HistoryCalculation
    import portfolio_app.performance.gain_components as components
    import portfolio_app.performance.gain_accounting as accounting
    # Missing opening cost forces the component fallback, including before the
    # income event. Query out of order so cached future cash cannot leak back.
    journal = Journal(version=2, securities=(book(
        E('o', 'opening', START, quantity=10, currency='EUR'),
        E('d', 'distribution', '2024-07-01', amount=5, currency='EUR', charges_known=True)),))
    scope = HistoryScope(('stock',))
    obs = observations(journal, scope, [('stock', END, 12)] if with_price else [])
    dates = (END, '2024-06-01', '2024-07-01')
    expected = {stamp: security_gain_components(journal, 'stock', obs, stamp) for stamp in dates}
    calculation = HistoryCalculation(journal, obs)
    def unexpected_replay(*args, **kwargs):
        pytest.fail('Component fallback must reuse the calculation ledger')
    monkeypatch.setattr(components, 'replay_securities', unexpected_replay)
    def unexpected_selection(*args):
        pytest.fail('Daily gain marks must use the effective events already selected for this calculation')
    monkeypatch.setattr(accounting, 'selected_events', unexpected_selection)
    scopes = []
    def counted_scope(*args, **kwargs):
        scope = HistoryScope(*args, **kwargs)
        scopes.append(scope)
        return scope
    monkeypatch.setattr(accounting, 'HistoryScope', counted_scope)
    for stamp in dates:
        assert security_gain_components(journal, 'stock', obs, stamp, calculation=calculation) == expected[stamp]
        gain = summarize_known_gain(journal, scope, obs, stamp, calculation=calculation)
        assert gain.amount == (5 if stamp >= '2024-07-01' else None)
        assert gain.capital is None and gain.partial
    assert summarize_known_gain(journal, scope, obs, END, calculation=calculation).value == (120 if with_price else None)
    assert scopes == [scope]


def test_reused_gain_members_follow_current_summary_only_rows():
    from portfolio_app.performance.personal_performance import HistoryCalculation
    journal, store = Journal(), ObservationStore()
    calculation = HistoryCalculation(journal, store)
    scope = HistoryScope(('invented',))
    for shares, expected in ((1, (1, D(4))), (0, (0, None)), (2, (1, D(4)))):
        rows = pd.DataFrame([dict(position_id='invented', shares=shares, unrealized_gain_reporting=4)])
        result = summarize_known_gain(journal, scope, store, END, summary_rows=rows,
                                      current_values={'invented': D(80)}, calculation=calculation)
        assert (result.count, result.amount) == expected


@pytest.mark.parametrize('estimated_units,recorded_units,expected', [(2, 0, None), (1, 1, D(25)), (0, 2, None)])
def test_partial_historical_lots_apply_the_same_fx_evidence_rule_as_complete_cost(estimated_units, recorded_units, expected):
    from portfolio_app.performance.cost_basis import component
    from portfolio_app.performance.personal_performance import HistoryCalculation

    conversion = {'EUR': {'amount': '60', 'method': 'estimate'}}
    parts = [component(estimated_units, 80, 'USD', conversions=conversion)]
    if recorded_units:
        parts.append(component(recorded_units, 20, 'EUR'))
    event = E('opening', 'opening', START, quantity=2, costs=tuple(parts))
    journal = Journal(version=5, securities=(book(event),))
    store = ObservationStore()
    for calculation in (None, HistoryCalculation(journal, store)):
        result = security_gain_components(journal, 'stock', store, END, 'EUR', current_value=D(90), calculation=calculation)
        assert result.remaining_cost is None and result.unrealized == expected
        assert 'Estimated cost conversion is not historical evidence' in result.reasons
    summary = summarize_known_gain(journal, HistoryScope(('stock',)), store, END, current_values={'stock': D(90)})
    assert summary.value == 90 and summary.amount == expected and summary.capital is None
    scope = HistoryScope(('stock',))
    marked = observations(journal, scope, [('stock', '2024-12-31', 40), ('stock', END, 45)])
    chart = history_view(journal, scope, marked, '1M', today=date.fromisoformat(END))
    assert chart.points[-1]['Value'] == 90 and chart.points[-1]['Total gain'] == expected
    # A saved conversion into EUR has no bearing on acquisition cost measured in USD.
    if not recorded_units:
        original = security_gain_components(journal, 'stock', store, END, 'USD', current_value=D(90))
        assert original.remaining_cost == 80 and original.unrealized == 10


def test_partial_lot_basis_reuses_only_costs_across_daily_marks(monkeypatch):
    from portfolio_app.performance import gain_components
    from portfolio_app.performance.personal_performance import HistoryCalculation
    journal = Journal(version=2, securities=(book(
        E('open', 'opening', START, quantity=1, currency='EUR'),
        E('known', 'buy', '2024-02-01', quantity=2, price=6, currency='EUR', charges_known=True),
        E('foreign', 'buy', '2024-03-01', quantity=1, price=5, currency='USD', charges_known=True)),))
    obs = ObservationStore()
    calc = HistoryCalculation(journal, obs)
    calls = []
    resolve = gain_components.resolve_cost
    def counted(*args, **kwargs):
        calls.append(1)
        return resolve(*args, **kwargs)
    monkeypatch.setattr(gain_components, 'resolve_cost', counted)
    def gain(stamp, value):
        return security_gain_components(journal, 'stock', obs, stamp, current_value=D(value), calculation=calc)
    february = gain('2024-02-01', 33)
    assert february.unrealized == 10 and february.remaining_cost is None
    february_calls = len(calls)
    # The quote and gain change while the supported cost and units stay fixed.
    assert gain('2024-02-29', 48).unrealized == 20
    assert len(calls) == february_calls
    assert gain('2024-03-01', 48).unrealized == 12
    march_calls = len(calls)
    assert march_calls > february_calls
    assert gain('2024-02-01', 33) == february
    assert gain('2024-03-17', 60).unrealized == 18
    assert len(calls) == march_calls
    corrected = ObservationStore((O('supplied', 'fx', 'USD/EUR', '2024-03-01', D('.8'), 'USD', 'EUR'),))
    updated = security_gain_components(journal, 'stock', corrected, '2024-03-17', current_value=D(60),
                                       calculation=HistoryCalculation(journal, corrected))
    assert updated.unrealized == 29 and updated.remaining_cost is None


def test_chart_gain_reuses_the_close_and_preserves_individual_marks(monkeypatch):
    from portfolio_app.performance import history_view as views, personal_performance
    from portfolio_app.performance.personal_performance import HistoryCalculation

    purchase = E('buy', 'buy', START, quantity=10, price=10, currency='EUR', charges_known=True)
    security = reconstruction_candidate(book(purchase), ('buy',), starts_at_zero=True)
    journal = Journal(version=2, securities=(security,), accounts=(Account('cash', 'Invented cash'),),
                      events=(Event('open', START, 'cash_open', 'cash', 'EUR', 100),))
    position = HistoryScope(('stock',))
    scope = HistoryScope(('stock',), ('cash',))
    middle = '2024-06-01'
    store = observations(journal, scope, [('stock', START, 10), ('stock', middle, 11), ('stock', END, 12)],
                         extra=(O('whole', 'value', scope.key, middle, D(777), 'EUR'),
                                O('member', 'value', position.key, middle, D(123), 'EUR')))
    calculation = HistoryCalculation(journal, store)
    # A long chart evicts early point-cache entries before reaching their gain calculation.
    full_interval = views._all_history
    def evict_middle(*args, **kwargs):
        result = full_interval(*args, **kwargs)
        calculation._facts.pop(('value', scope, middle, 'EUR'))
        return result
    monkeypatch.setattr(views, '_all_history', evict_middle)
    calculated = []
    original = personal_performance.value_at
    def track(source, selection, observations, stamp, currency, **options):
        if selection == scope:
            calculated.append(stamp)
        return original(source, selection, observations, stamp, currency, **options)
    monkeypatch.setattr(personal_performance, 'value_at', track)
    chart = history_view(journal, scope, store, 'All', today=date.fromisoformat(END), calculation=calculation)
    point = next(p for p in chart.points if p['Date'] == middle)
    assert (point['Value'], point['Invested capital'], point['Total gain']) == (777, 200, 23)
    assert chart.points[0]['Value'] is None
    assert chart.all_known.amount == 20
    assert calculated.count(middle) == 1


def test_overlapping_chart_ranges_reuse_partial_gain_for_the_same_scope_and_currency(monkeypatch):
    from datetime import timedelta
    from portfolio_app.performance import history_view as views
    from portfolio_app.performance.personal_performance import HistoryCalculation

    buy = E('buy', 'buy', START, quantity=10, price=10, currency='EUR', charges_known=True)
    security = reconstruction_candidate(book(buy), ('buy',), starts_at_zero=True)
    journal = Journal(version=2, securities=(security,), accounts=(
        Account('cash', 'Invented cash'), Account('other', 'Invented reserve')),
        events=(Event('open', START, 'cash_open', 'cash', 'EUR', 100),
                Event('reserve', START, 'cash_open', 'other', 'EUR', 300)))
    scope = HistoryScope(('stock',), ('cash',))
    dates = [(date.fromisoformat(START) + timedelta(days=i)).isoformat() for i in range(120)]
    prices = tuple(O('price-'+day, 'price', 'stock', day, D(10+i), 'EUR') for i, day in enumerate(dates))
    rates = tuple(O('fx-'+day, 'fx', 'EUR/USD', day, D('1.5'), 'EUR', target='USD') for day in dates)
    store = ObservationStore(prices + rates)
    today = date.fromisoformat(dates[-1])
    calculation = HistoryCalculation(journal, store)
    first = history_view(journal, scope, store, '1Y', today=today, calculation=calculation)
    assert first.all_result.opening_capital is None
    calculation._facts.clear()  # Older per-member valuations were evicted by another range.
    expected = history_view(journal, scope, store, '1M', today=today)
    original = views.summarize_known_gain
    requested = []
    def count(source, selected, observations, stamp, *args, **kwargs):
        requested.append((selected, stamp))
        return original(source, selected, observations, stamp, *args, **kwargs)
    monkeypatch.setattr(views, 'summarize_known_gain', count)
    projected = []
    amounts = views.chart_gain_amounts
    def count_point(calculation, selected, stamp, target, **kwargs):
        projected.append((selected, stamp, target))
        return amounts(calculation, selected, stamp, target, **kwargs)
    monkeypatch.setattr(views, 'chart_gain_amounts', count_point)
    view = history_view(journal, scope, store, '1M', today=today, calculation=calculation)
    assert view == expected
    assert requested == [(scope, dates[-1])]  # The endpoint also supplies detailed metric explanations.
    assert projected == []
    foreign = history_view(journal, scope, store, '1M', today=today, target='USD', calculation=calculation)
    assert projected and all(selected == scope and target == 'USD' for selected, _, target in projected)
    assert foreign == history_view(journal, scope, store, '1M', today=today, target='USD')
    assert foreign.points[-2]['Total gain'] == 1.5 * view.points[-2]['Total gain']
    other_scope = HistoryScope(('stock',), ('other',))
    other = history_view(journal, other_scope, store, '1M', today=today, calculation=calculation)
    assert other == history_view(journal, other_scope, store, '1M', today=today)
    assert other.points[-2]['Invested capital'] != view.points[-2]['Invested capital']
    changed = replace(store, observations=tuple(replace(mark, amount=mark.amount + 2) if mark == prices[-2] else mark
                                                for mark in store.observations))
    corrected = history_view(journal, scope, changed, '1M', today=today)
    assert corrected.points[-2]['Total gain'] == view.points[-2]['Total gain'] + 20


def test_partial_gain_values_members_once_and_keeps_scope_marks_separate(monkeypatch):
    from portfolio_app.performance import personal_performance as perf

    security = reconstruction_candidate(book(E('buy', 'buy', START, quantity=10, price=10,
        currency='EUR', charges_known=True)), ('buy',), starts_at_zero=True)
    journal = Journal(version=2, securities=(security,), accounts=(Account('cash', 'Invented cash'),),
                      events=(Event('open', START, 'cash_open', 'cash', 'EUR', 100),))
    stock, cash = HistoryScope(('stock',)), HistoryScope(accounts=('cash',))
    scope = HistoryScope(stock.positions, cash.accounts)
    day, late = '2024-06-01', '2024-07-01'
    store = observations(journal, scope, [('stock', START, 10), ('stock', day, 11),
                                         ('stock', late, 12), ('stock', END, 13)], extra=(
        O('stock-mark', 'value', stock.key, day, 121, 'EUR'),
        O('cash-mark', 'value', cash.key, day, 105, 'EUR'),
        O('foreign-mark', 'value', stock.key, late, 900, 'USD')))
    calculation = perf.HistoryCalculation(journal, store)
    calculation.value(scope, day, 'EUR')  # A summary may already have validated this portfolio close.
    calls = []
    original = perf.value_at
    def count(source, selected, observations, stamp, target, **options):
        if stamp in (day, late):
            calls.append((selected, stamp))
        return original(source, selected, observations, stamp, target, **options)
    monkeypatch.setattr(perf, 'value_at', count)
    view = history_view(journal, scope, store, 'All', today=date.fromisoformat(END), calculation=calculation)
    chart = {row['Date']: row for row in view.points}
    assert (chart[day]['Value'], chart[day]['Invested capital'], chart[day]['Total gain']) == (210, 200, 26)
    assert (chart[late]['Value'], chart[late]['Invested capital'], chart[late]['Total gain']) == (220, 200, 20)
    assert calls == [(scope, day), (scope, late)]


def test_member_closes_keep_flat_portfolio_and_individual_account_rounding():
    from portfolio_app.performance.personal_performance import HistoryCalculation

    journal = Journal(accounts=(Account('a', 'Invented A'), Account('b', 'Invented B')), events=(
        Event('a', START, 'cash_open', 'a', 'EUR', D('1e23')),
        Event('b-gbp', START, 'cash_open', 'b', 'GBP', 1),
        Event('b-usd', START, 'cash_open', 'b', 'USD', 1)))
    scope = HistoryScope(accounts=('a', 'b'))
    store = observations(journal, scope, fx=[(native, 'EUR', START, '.00004') for native in ('USD', 'GBP')])
    calculation = HistoryCalculation(journal, store)
    close, members = calculation.value_with_members(scope, START, 'EUR')
    standalone = [calculation.value(HistoryScope(accounts=(a,)), START, 'EUR').amount for a in scope.accounts]
    assert members == standalone
    assert close.amount == calculation.value(scope, START, 'EUR').amount
    assert members == [D('1e23'), D('0.00008')]
    assert close.amount == D('1e23') and close.amount != sum(members)


@pytest.mark.parametrize('missing_last', [False, True])
def test_daily_gain_amounts_only_qualify_the_displayed_endpoint(monkeypatch, missing_last):
    from portfolio_app.performance import gain_accounting as gains, history_view as views
    from portfolio_app.performance.personal_performance import HistoryCalculation

    purchase = E('buy', 'buy', START, quantity=2, price=10, currency='EUR', charges_known=True, estimated=True)
    security = reconstruction_candidate(book(purchase), ('buy',), starts_at_zero=True)
    journal = Journal(version=5, securities=(security,), accounts=(Account('cash', 'Invented cash'),),
                      events=(Event('open', START, 'cash_open', 'cash', 'EUR', 100),))
    scope = HistoryScope(('stock',), ('cash',))
    dates = [f'2024-01-{d:02}' for d in range(1, 16)]
    end = dates[-2] if missing_last else dates[-1]
    store = observations(journal, scope, [('stock', day, 10+i) for i, day in enumerate(dates) if day <= end],
                         extra=(O('scope-mark', 'value', scope.key, dates[3], 150, 'EUR'),))
    calculation = HistoryCalculation(journal, store)
    calculation.event_dates(scope)  # Isolate gain qualification from indexing ledger dates.
    checks = []
    original = gains.affected_positions

    def counted(book, event):
        checks.append(event.id)
        return original(book, event)

    projections = {}
    def chart_amounts(calculation, scope, day, target, **kwargs):
        projections[day] = bool(kwargs.get('member_closes'))
        return gains.chart_gain_amounts(calculation, scope, day, target, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(gains, 'affected_positions', counted)
        patch.setattr(views, 'chart_gain_amounts', chart_amounts)
        view = history_view(journal, scope, store, 'All', today=date.fromisoformat(dates[-1]), calculation=calculation)
    assert checks == [purchase.id]
    assert end not in projections
    assert projections[dates[2]] and not projections[dates[3]]
    assert view.all_end == end
    assert view.all_known == summarize_known_gain(journal, scope, store, end)
    assert view.all_known.coverage_note == 'Estimated gain'
    assert view.all_result.opening_capital is None
    for point in view.points:
        detail = summarize_known_gain(journal, scope, store, point['Date'])
        assert (point['Invested capital'], point['Total gain']) == (detail.capital, detail.amount)
