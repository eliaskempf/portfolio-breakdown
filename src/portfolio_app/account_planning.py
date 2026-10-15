"""Integer futures exposure plans against actual account cash and declared margin.

Planning does not transfer, convert or borrow cash, and never posts a trade.
Funded holdings remain in the existing capital planner.
"""
from dataclasses import dataclass, replace
import math
import numpy as np
import pandas as pd
from scipy.optimize import milp, Bounds, LinearConstraint

from portfolio_app.holdings import DataError
from portfolio_app.exposure_targets import exposure_gaps, leaf_composition


@dataclass(frozen=True)
class AccountPlan:
    trades: pd.DataFrame
    gaps: pd.DataFrame
    equity: float
    fees: float
    complete: bool


def plan_account_futures(result, targets, allocation, valued, journal, *, permitted,
                         margins, fees=None, limits=None, allow_short=False):
    from portfolio_app.account_journal import replay
    fees, limits = fees or {}, limits or {}
    state = replay(journal)
    permitted = set(permitted)
    if not permitted or permitted - set(state.positions):
        raise DataError('Select existing actual futures positions for this plan.')
    base = exposure_gaps(result, targets, allocation).set_index('Bucket ID')
    currency = result.reporting_currency
    current_col, desired_col = f'Current exposure ({currency})', f'Desired exposure ({currency})'
    selected = valued.set_index('position_id')
    keys = list(base.index)
    records = []
    protected = set().union(*(allocation.leaves(b.id) for b in allocation.buckets if b.sell_protected))
    for identity in sorted(permitted):
        p = state.positions[identity]
        if identity not in selected.index:
            raise DataError('A planned position has no current valuation.')
        row = selected.loc[identity]
        margin, fee, limit = float(margins.get(identity, 0)), float(fees.get(identity, 0)), limits.get(identity, 10000)
        if p.spec.bucket_id not in allocation.leaves() or p.spec.bucket_id in protected:
            raise DataError('Permitted futures need a valid, unprotected leaf category.')
        rate, unit = row.current_fx_rate, row.unit_exposure_reporting
        if not all(math.isfinite(x) for x in (margin, fee, rate, unit)) or margin <= 0 or fee < 0 or rate <= 0 or unit <= 0:
            raise DataError('Current FX, mark, positive initial margin and nonnegative fees are required.')
        if type(limit) is not int or not 0 <= limit <= 10000:
            raise DataError('Contract limits must be integers between zero and 10000.')
        records.append(dict(id=identity, quantity=p.quantity, bucket=p.spec.bucket_id, unit=unit, rate=rate,
            group=(p.spec.account_id,p.contract.currency), margin=margin, fee=fee, limit=limit, name=row['name']))
    n,k=len(records),len(keys)
    size=4*n+k
    lower=np.zeros(size); upper=np.full(size,np.inf); integer=np.zeros(size)
    constraints=[]; lo=[]; hi=[]
    def constraint(v,minimum=-np.inf,maximum=np.inf):
        constraints.append(v);lo.append(minimum);hi.append(maximum)
    changes={key:np.zeros(size) for key in keys}; charges=np.zeros(size)
    cash_groups={r['group'] for r in records}
    for i,item in enumerate(records):
        buy,sell,absolute,direction=4*i,4*i+1,4*i+2,4*i+3
        integer[[buy,sell,absolute,direction]]=1
        bound=20000+abs(item['quantity']);upper[[buy,sell]]=bound;upper[absolute]=item['limit'];upper[direction]=1
        v=np.zeros(size);v[buy]=1;v[sell]=-1
        constraint(v,(-item['limit'] if allow_short else 0)-item['quantity'],item['limit']-item['quantity'])
        a=v.copy();a[absolute]=-1;constraint(a,maximum=-item['quantity'])
        a=-v;a[absolute]=-1;constraint(a,maximum=item['quantity'])
        a=np.zeros(size);a[buy]=1;a[direction]=-bound;constraint(a,maximum=0)
        a=np.zeros(size);a[sell]=1;a[direction]=bound;constraint(a,maximum=bound)
        changes[item['bucket']][buy]=item['unit'];changes[item['bucket']][sell]=-item['unit']
        charges[[buy,sell]]=item['fee']*item['rate']
    for group in cash_groups:
        native_cash=float(state.cash.get(group,0))
        covered = {r.position_id for r in journal.margin_requirements
                   if (r.account_id,r.currency)==group and r.initial > 0}
        if any(p.quantity and identity not in permitted and identity not in covered
               for identity,p in state.positions.items()
               if (p.spec.account_id,p.contract.currency)==group):
            raise DataError('Record initial margin for every unselected open position in this account before planning.')
        # Unselected positions keep their declared account margin requirement.
        held_back=sum(float(r.initial) for r in journal.margin_requirements if (r.account_id,r.currency)==group and r.position_id and r.position_id not in permitted)
        account_floor=sum(float(r.initial) for r in journal.margin_requirements if (r.account_id,r.currency)==group and not r.position_id)
        obligations=sum(max(0.,-float(p.amount)) for p in state.pending.values() if (p.account_id,p.currency)==group)
        for identity,balance in state.positions.items():
            if (balance.spec.account_id,balance.contract.currency)==group and balance.quantity:
                if identity not in selected.index or not math.isfinite(selected.loc[identity].get('mark_price',float('nan'))):
                    raise DataError('Every open position in a planned account needs a current mark.')
                obligations+=max(0.,-float(balance.unsettled_pnl(selected.loc[identity].mark_price)))
        reservation=sum(float(r.amount) for r in state.reservations.values() if (r.account_id,r.currency)==group and not r.holding_id)
        budget=native_cash-held_back-obligations
        v=np.zeros(size)
        for i,item in enumerate(records):
            if item['group']==group:
                v[4*i]=v[4*i+1]=item['fee'];v[4*i+2]=item['margin']
        constraint(v,maximum=budget)
        # Fees cannot spend cash separately reserved away from this plan.
        v=np.zeros(size)
        for i,item in enumerate(records):
            if item['group']==group:
                v[4*i]=v[4*i+1]=item['fee']
        constraint(v,maximum=native_cash-max(reservation,account_floor)-obligations)
    composition=leaf_composition(targets,allocation) if targets.policy=='composition' else {targets.overlay_bucket:1.}
    for j,key in enumerate(keys):
        fraction=targets.multiple*composition.get(key,0.)
        difference=float(base.loc[key,desired_col]-base.loc[key,current_col])
        v=changes[key]+fraction*charges;v[4*n+j]=-1;constraint(v,maximum=difference)
        v=-changes[key]-fraction*charges;v[4*n+j]=-1;constraint(v,maximum=-difference)
    constraint(charges,maximum=result.denominator-1e-8)
    objective=np.zeros(size);objective[4*n:]=1
    options={'time_limit':15.}
    def solve(c):
        return milp(c,integrality=integer,bounds=Bounds(lower,upper),constraints=LinearConstraint(np.array(constraints),lo,hi),options=options)
    solution=solve(objective)
    if not solution.success:
        raise DataError('No verified feasible plan within recorded cash, declared margin and contract limits.')
    constraint(objective,maximum=solution.fun+1e-7)
    turnover=np.zeros(size)
    for i,item in enumerate(records):
        turnover[4*i:4*i+2]=item['unit']
    refined=solve(turnover);x=refined.x if refined.success else solution.x
    activity=np.array(constraints)@x
    if np.any(activity<np.array(lo)-1e-5) or np.any(activity>np.array(hi)+1e-5) or np.any(abs(x[integer==1]-np.round(x[integer==1]))>1e-5):
        raise DataError('Solver returned an unverifiable plan.')
    updated=result.exposures.copy();trades=[]
    for i,item in enumerate(records):
        quantity=int(round(x[4*i]-x[4*i+1]));delta=quantity*item['unit']
        mask=updated.source_position_id.eq(item['id']);prior=updated.loc[mask,targets.field].sum()
        if prior:
            updated.loc[mask,targets.field]*=(prior+delta)/prior
        else:
            updated.loc[mask,targets.field]=0.
            updated.loc[updated.index[mask][0],targets.field]=delta
        if quantity:
            trades.append({'Position ID':item['id'],'Instrument':item['name'],'Contracts':quantity,'Final contracts':item['quantity']+quantity,
                f'Exposure change ({currency})':delta,f'Fees ({currency})':abs(quantity)*item['fee']*item['rate']})
    total_fees=float(charges@x);equity=result.net_equity-total_fees
    gaps=exposure_gaps(replace(result,exposures=updated,net_equity=equity),targets,allocation)
    return AccountPlan(pd.DataFrame(trades),gaps,equity,total_fees,bool(gaps[f'Gap ({currency})'].abs().le(.01).all()))


def render_account_plan(directory,result,allocation,valued,journal):
    import streamlit as st
    from portfolio_app.exposure_targets import load_exposure_targets
    from portfolio_app.account_journal import replay
    if allocation is None:
        st.info('Save categories and an exposure target before planning futures trades.')
        return
    targets,_=load_exposure_targets(directory,allocation)
    if targets is None:
        st.info('Save an exposure target first.')
        return
    state=replay(journal)
    names={p.id:f'{next(c for c in journal.contracts if c.id==p.contract_id).product} · {p.id[:8]}' for p in journal.positions}
    permitted=st.multiselect('Permitted actual futures',list(names),format_func=names.get,key='planning_actual_permitted')
    margins={};fees={};limits={}
    for identity in permitted:
        p=state.positions[identity]
        with st.expander(names[identity],expanded=True):
            margins[identity]=st.number_input(f'Initial margin per contract ({p.contract.currency})',min_value=0.,value=0.,key=f'planning_actual_margin_{identity}')
            fees[identity]=st.number_input(f'Fee per traded contract ({p.contract.currency})',min_value=0.,value=0.,key=f'planning_actual_fee_{identity}')
            limits[identity]=st.number_input('Maximum absolute contracts',min_value=0,max_value=10000,value=max(1,abs(p.quantity)),key=f'planning_actual_limit_{identity}')
    short=st.checkbox('Permit short positions',key='planning_actual_short')
    st.caption('Futures exposure plan using recorded cash separately for each account and currency. Enter initial-margin assumptions. Unsettled losses and pending debits reserve cash; unposted gains are unavailable. Recorded account-total requirements remain a fixed collateral floor. Funded holdings use the capital planner; this preview makes no transfers, conversions or loans.')
    if st.button('Calculate actual futures plan',key='planning_actual_calculate'):
        try:
            plan=plan_account_futures(result,targets,allocation,valued,journal,permitted=permitted,margins=margins,fees=fees,limits=limits,allow_short=short)
            st.dataframe(plan.trades,hide_index=True);st.dataframe(plan.gaps,hide_index=True)
            st.caption(f'Planned equity {plan.equity:,.2f} {result.reporting_currency} · Fees {plan.fees:,.2f}')
            st.success('Targets reached within 0.01 reporting-currency units.') if plan.complete else st.warning('Remaining target gaps are shown; whole-contract and funding limits constrain this plan.')
        except DataError as exc:
            st.error(str(exc))
