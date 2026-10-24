"""Fractional FIFO regressions with invented records and rational-number oracles."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D, localcontext
from fractions import Fraction as F

import pytest

from portfolio_app.account_journal import Journal, journal_from_dict, journal_to_dict
from portfolio_app.cost_basis import component
from portfolio_app.gain_components import security_gain_components
from portfolio_app.history_observations import ObservationStore
from portfolio_app.holdings import DataError
from portfolio_app.security_history import SecurityLot, SecurityBook, SecurityEvent as E, Charge, _consume, replay_securities


def active(*events, identity='invented'):
    return SecurityBook(identity,'SYNTH',('0','',''),records=events,active_ids=tuple(e.id for e in events))


def test_one_third_sale_preserves_exact_units_fees_and_gains():
    book=active(E('o','opening','2024-01-01'),
        E('b','buy','2024-02-01',quantity=3,amount=30,currency='EUR',charges=(Charge(3,'EUR'),),charges_known=True),
        E('s','sell','2024-03-01',quantity=1,amount=15,currency='EUR',charges_known=True))
    journal=Journal(version=2,securities=(book,))
    state=replay_securities(journal.securities)
    remaining=state.positions['invented']
    assert remaining.quantity==2
    assert [D(p['shares']) for p in remaining.costs]==[2,0]
    assert [D(p['amount']) for p in remaining.costs]==[20,2]
    sold=state.disposals[0].lots[0]
    assert sold.quantity==1
    assert [D(p['shares']) for p in sold.costs]==[1,0]
    assert [D(p['amount']) for p in sold.costs]==[10,1]
    result=security_gain_components(journal,'invented',ObservationStore(),'2024-03-01',current_value=D(24))
    assert (result.remaining_cost,result.realized,result.unrealized)==(22,4,2)
    assert journal_from_dict(journal_to_dict(journal))==journal


@pytest.mark.parametrize('units,take', [('3','1'),('7','2'),('0.12345','0.02341'),('13.73157','8.97013'),('0.00000003','0.00000001')])
@pytest.mark.parametrize('multiple', [False,True])
@pytest.mark.parametrize('precision', [28,50])
def test_partial_lots_conserve_each_original_and_converted_cost(units,take,multiple,precision):
    q,t=D(units),D(take)
    shares=(q/2,q/2) if multiple else (q,)
    parts=tuple(component(s,'17.03','USD','2024-01-01',
        {'EUR':{'amount':'15.71','method':'supplied_cost'}}) for s in shares)
    parts+= (component(0,'0.07','USD','2024-01-01',{'EUR':{'amount':'0.06','method':'historical','observed_on':'2024-01-01'}}),)
    lot=SecurityLot('l',q,parts,True,'2024-01-01'); original=deepcopy(lot)
    with localcontext() as ctx:
        ctx.prec=precision
        remaining,moved=_consume((lot,),t)
    left,sold=remaining[0],moved[0]
    assert F(left.quantity)+F(sold.quantity)==F(q)
    assert sold.quantity==t
    for piece in (left,sold):
        assert sum(F(p['shares']) for p in piece.costs)==F(piece.quantity)
        assert D(piece.costs[-1]['shares'])==0
        # Validation must not depend on the decimal precision used by replay.
        with localcontext() as ctx:
            ctx.prec=28
            assert replace(piece)==piece
    for old,a,b in zip(parts,left.costs,sold.costs):
        for field in ('shares','amount'):
            assert F(a[field])+F(b[field])==F(old[field])
        assert F(a['conversions']['EUR']['amount'])+F(b['conversions']['EUR']['amount'])==F(old['conversions']['EUR']['amount'])
        assert abs(F(b['amount'])-F(old['amount'])*F(t)/F(q)) < F(1,10**(precision-3))
    assert lot==original


def test_repeated_partial_sales_then_liquidation_conserve_all_cost():
    lot=SecurityLot('l',D(7),(component(7,'20.03','EUR'),component(0,'0.07','EUR')),True,'2024-01-01')
    remaining=(lot,); disposed=[]
    with localcontext() as ctx:
        ctx.prec=50
        for _ in range(7):
            remaining,moved=_consume(remaining,D(1));disposed.extend(moved)
    assert not remaining
    assert sum(F(l.quantity) for l in disposed)==7
    assert sum(F(l.costs[0]['amount']) for l in disposed)==F('20.03')
    assert sum(F(l.costs[1]['amount']) for l in disposed)==F('0.07')


def test_partial_transfer_then_sale_and_split_keep_fifo_costs():
    source=active(E('o','opening','2024-01-01'),
        E('b','buy','2024-02-01',quantity=3,amount=30,currency='EUR',charges_known=True),
        E('t','transfer','2024-03-01',quantity=1,other_position_id='other'))
    other=active(E('o2','opening','2024-01-01'),
        E('split','split','2024-04-01',quantity=2),
        E('s','sell','2024-05-01',quantity=1,amount=8,currency='EUR',charges_known=True),identity='other')
    state=replay_securities((source,other))
    assert state.positions['invented'].quantity==2
    assert state.positions['other'].quantity==1
    assert D(state.positions['invented'].costs[0]['amount'])==20
    assert D(state.positions['other'].costs[0]['amount'])==5
    assert D(state.disposals[-1].lots[0].costs[0]['amount'])==5
    assert [c.kind for c in state.cash]==['buy','sell']


def test_partial_aggregate_opening_stays_unknown_and_full_disposal_keeps_known_cost():
    lot=SecurityLot('o',D(3),(component(3,30,'EUR'),),False,'2024-01-01')
    remaining,moved=_consume((lot,),D(1))
    assert remaining[0].costs[0]['amount']==moved[0].costs[0]['amount']==''
    assert remaining[0].quantity==2 and moved[0].quantity==1
    remaining,moved=_consume((lot,),D(3))
    assert not remaining and moved[0].costs[0]['amount']=='30'


def test_real_component_quantity_mismatch_is_still_rejected():
    with pytest.raises(DataError,match='Purchase cost quantities'):
        SecurityLot('bad',D(3),(component('2.99999999999999999999999999999',30,'EUR'),))


def test_three_quantity_components_keep_exact_sum_after_one_third_sale():
    lot=SecurityLot('l',D(3),tuple(component(1,10,'EUR') for _ in range(3))+(component(0,3,'EUR'),),True,'2024-01-01')
    with localcontext() as ctx:
        ctx.prec=50
        remaining,moved=_consume((lot,),D(1))
    for piece in (*remaining,*moved):
        assert sum(F(p['shares']) for p in piece.costs)==F(piece.quantity)
        assert replace(piece)==piece
    for original,left,right in zip(lot.costs,remaining[0].costs,moved[0].costs):
        assert F(left['shares'])+F(right['shares'])==F(original['shares'])
        assert F(left['amount'])+F(right['amount'])==F(original['amount'])
