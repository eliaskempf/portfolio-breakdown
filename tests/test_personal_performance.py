"""Return examples are deliberately invented and use injected observations only."""
from dataclasses import replace
from decimal import Decimal as D
from uuid import uuid4
import math
import pandas as pd
import pytest

from portfolio_app.account_journal import Account, Event, Journal
from portfolio_app.history_observations import (
    HistoryScope, Observation, ObservationStore, CoverageReview, rate,
    add_observation, load_observations, save_observations,
)
from portfolio_app.personal_performance import calculate_history, value_at, external_flows, ExternalFlow, xirr
from portfolio_app.security_history import SecurityBook, SecurityEvent as E, history_fingerprint
from portfolio_app.personal_market import as_traded_prices


def journal():
    events = (E('open', 'opening', '2024-01-01', quantity=D(10), amount=D(20), currency='EUR'),
        E('buy', 'buy', '2024-07-01', quantity=D(5), price=D(12), currency='EUR', charges_known=True))
    book = SecurityBook('holding', 'invented', ('10','2','EUR'), records=events, active_ids=('open','buy'))
    return Journal(version=2, securities=(book,))


def observed(j, scope, values=(('2024-01-01','10'), ('2025-01-01','15'))):
    observations = tuple(Observation(uuid4().hex, 'price', 'holding', stamp, D(value), 'EUR') for stamp,value in values)
    review = CoverageReview('review', scope, '2024-01-01','2025-01-01', history_fingerprint(j))
    return ObservationStore(observations, (review,))


def test_opening_market_value_separates_pre_snapshot_gain_and_contribution():
    j, scope = journal(), HistoryScope(('holding',))
    result = calculate_history(j, scope, observed(j, scope), '2024-01-01', '2025-01-01')
    assert result.opening_value == 100  # Historical acquisition cost was only 20.
    assert result.ending_value == 225
    assert result.net_contributions == 60
    assert result.invested_capital == 80  # Recorded cost 20 + purchase payment 60.
    assert result.total_gain == 145
    assert result.opening_known_gain == 80
    assert result.gain == 65
    assert result.money_weighted is not None
    assert result.time_weighted is None  # Endpoint prices do not supply a flow-boundary valuation.


def test_missing_daily_prices_do_not_block_xirr_but_missing_endpoint_does():
    j, scope = journal(), HistoryScope(('holding',))
    store = observed(j, scope)
    assert calculate_history(j, scope, store, '2024-01-01', '2025-01-01').money_weighted is not None
    store = replace(store, observations=store.observations[:1])
    result = calculate_history(j, scope, store, '2024-01-01', '2025-01-01')
    assert result.net_contributions == 60
    assert result.ending_value is None and result.money_weighted is None


def test_unresolved_event_blocks_reconstructed_value_until_new_opening():
    j, scope = journal(), HistoryScope(('holding',))
    book = j.securities[0]
    unresolved = E('action', 'note', '2024-08-01', note='Invented corporate action awaiting records')
    book = replace(book, records=(*book.records, unresolved), active_ids=(*book.active_ids, unresolved.id))
    j = replace(j, securities=(book,))
    store = observed(j, scope)
    value = value_at(j, scope, store, '2025-01-01', 'EUR')
    assert value.amount is None and any('resolve historical events' in r for r in value.reasons)
    assert value_at(j, scope, store, '2024-01-01', 'EUR').amount == 100
    supplied = add_observation(store, Observation('total', 'value', scope.key, '2025-01-01', D(220), 'EUR'))
    assert value_at(j, scope, supplied, '2025-01-01', 'EUR').amount == 220
    opening = E('new-opening', 'opening', '2025-01-01', quantity=D(14), amount=D(100), currency='EUR')
    book = replace(book, records=(*book.records, opening), active_ids=(*book.active_ids, opening.id))
    j = replace(j, securities=(book,))
    assert value_at(j, scope, store, '2025-01-01', 'EUR').amount == 210


def test_exact_twr_requires_reconciling_before_after_flow_values():
    j, scope = journal(), HistoryScope(('holding',))
    store = observed(j, scope)
    for side, value in [('before',120),('after',180)]:
        store = add_observation(store, Observation(side,'value',scope.key,'2024-07-01',D(value),'EUR',side=side,boundary='security:buy'))
    result = calculate_history(j, scope, store, '2024-01-01','2025-01-01')
    assert result.time_weighted == pytest.approx(.5)


def test_whole_portfolio_trade_is_internal_cash_and_asset_conserved():
    j = journal()
    b = replace(j.securities[0], account_id='account')
    j = replace(j, securities=(b,), accounts=(Account('account','Invented account'),),
        events=(Event('cash','2024-01-01','cash_open','account','EUR',D(100)),))
    scope = HistoryScope(('holding',),('account',))
    store = observed(j,scope)
    result = calculate_history(j, scope, store, '2024-01-01','2025-01-01')
    assert result.opening_value == 200
    assert result.ending_value == 265
    assert result.net_contributions == 0
    assert result.gain == 65
    assert result.time_weighted == pytest.approx(.325)


def test_review_invalidated_by_changed_records_and_missing_fees_are_not_zero():
    j, scope = journal(), HistoryScope(('holding',))
    store = observed(j,scope)
    event = replace(j.securities[0].records[1], charges_known=False)
    j = replace(j, securities=(replace(j.securities[0],records=(j.securities[0].records[0],event)),))
    result = calculate_history(j,scope,store,'2024-01-01','2025-01-01')
    assert result.ending_value == 225
    assert result.net_contributions is None and result.gain is None
    assert any('charges' in r for r in result.flow_reasons)


def test_xirr_short_period_and_nonconventional_flow_withholding():
    value, reason = xirr(D(100),D(101),(),'2024-01-01','2024-01-02')
    assert value == pytest.approx(1.01**365-1)
    assert not reason
    flows=(ExternalFlow('a','2024-03-01',0,D(-230)), ExternalFlow('b','2024-06-01',0,D(132)))
    value, reason=xirr(D(100),D(1),flows,'2024-01-01','2025-01-01')
    assert value is None and 'unique' in reason
    assert xirr(D(100),D(100),(),'2024-01-01','2024-01-01')[0] is None


def test_manual_fx_is_exact_date_and_persists_corrections(tmp_path):
    store = ObservationStore((Observation('fx','fx','USD/EUR','2024-01-01',D('.8'),'USD',target='EUR'),))
    assert rate(store,'USD','EUR','2024-01-02') is None
    assert rate(store,'EUR','USD','2024-01-01') == D('1.25')
    save_observations(tmp_path,store,None)
    loaded,revision=load_observations(tmp_path)
    assert loaded == store
    corrected=add_observation(store,Observation('correction','fx','USD/EUR','2024-01-01',D('.9'),'USD',target='EUR',replaces='fx'))
    save_observations(tmp_path,corrected,revision)
    assert len(load_observations(tmp_path)[0].observations)==2


def test_split_normalization_uses_actions_after_requested_window():
    frame=pd.DataFrame({'Close':[10,12,13], 'Stock Splits':[0,2,0]},index=pd.to_datetime(['2024-01-01','2024-02-01','2024-03-01']))
    assert as_traded_prices(frame)==(('2024-01-01',D(20)),('2024-02-01',D(12)),('2024-03-01',D(13)))


def test_account_transfer_and_borrowing_do_not_become_portfolio_contributions():
    j=Journal(accounts=(Account('a','Invented A'),Account('b','Invented B')),events=(
        Event('aopen','2024-01-01','cash_open','a','EUR',D(100)),
        Event('bopen','2024-01-01','cash_open','b','EUR',D(0)),
        Event('borrow','2024-02-01','borrow','a','EUR',D(20),reference='loan'),
        Event('move','2024-03-01','transfer','a','EUR',D(50),other_account_id='b'),))
    scope=HistoryScope(accounts=('a','b'))
    store=ObservationStore(reviews=(CoverageReview('review',scope,'2024-01-01','2025-01-01',history_fingerprint(j)),))
    assert value_at(j,scope,store,'2025-01-01','EUR').amount==100
    assert external_flows(j,scope,store,'2024-01-01','2025-01-01','EUR')[0]==()
    subset=HistoryScope(accounts=('b',))
    assert external_flows(j,subset,store,'2024-01-01','2025-01-01','EUR')[0][0].amount==50


def test_futures_settlement_moves_pnl_to_cash_without_creating_contributions():
    from portfolio_app.account_journal import PositionSpec
    from portfolio_app.derivative_types import ContractSpec
    contract=ContractSpec('SYNTH','2025-03-20','EUR',D(10),D('.5'))
    j=Journal(accounts=(Account('a','Invented'),),contracts=(contract,),positions=(PositionSpec('future','a',contract.id),),
        events=(Event('cash','2024-01-01','cash_open','a','EUR',D(1000)),
                Event('open','2024-01-01','snapshot',position_id='future',quantity=2,price=D(100)),
                Event('settle','2024-07-01','settlement',position_id='future',price=D(105))))
    scope=HistoryScope(accounts=('a',))
    store=ObservationStore((Observation('m1','price','future','2024-01-01',D(100),'EUR'),
                            Observation('m2','price','future','2025-01-01',D(120),'EUR')),
        (CoverageReview('r',scope,'2024-01-01','2025-01-01',history_fingerprint(j)),))
    result=calculate_history(j,scope,store,'2024-01-01','2025-01-01')
    assert result.opening_value==1000 and result.ending_value==1400
    assert result.net_contributions==0 and result.gain==400


def test_replacement_boundary_prevents_return_across_unexplained_balance_change():
    j=journal();b=j.securities[0]
    boundary=E('reset','opening','2024-09-01',quantity=D(15),amount=D(160),currency='EUR')
    j=replace(j,securities=(replace(b,records=(*b.records,boundary),active_ids=(*b.active_ids,'reset')),))
    scope=HistoryScope(('holding',))
    result=calculate_history(j,scope,observed(j,scope),'2024-01-01','2025-01-01')
    assert result.ending_value==225
    assert result.gain is None
    assert any('boundary' in r for r in result.flow_reasons)


def test_external_security_transfer_is_contribution_without_fabricating_purchase_cost():
    events=(E('o','opening','2024-01-01'),E('gift','transfer_in','2024-02-01',quantity=D(2),amount=D(50),currency='EUR',charges_known=True))
    b=SecurityBook('holding','invented',('0','',''),records=events,active_ids=('o','gift'))
    j=Journal(version=2,securities=(b,));scope=HistoryScope(('holding',))
    flows,reasons=external_flows(j,scope,ObservationStore(),'2024-01-01','2024-03-01','EUR')
    assert not reasons and flows[0].amount==50
    from portfolio_app.security_history import replay_securities
    assert replay_securities((b,)).positions['holding'].costs[0]['amount']==''
