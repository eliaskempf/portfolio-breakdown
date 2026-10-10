"""Hypothetical exposure trades with explicitly permitted instruments."""
import streamlit as st

from portfolio_app.exposure_planning import plan_exposure
from portfolio_app.exposure_targets import load_exposure_targets
from portfolio_app.holdings import DataError


def render_exposure_plan(directory, result, allocation, valued, *, future=None):
    if allocation is None:
        st.info('Save strategic categories and an exposure policy first.')
        return
    try:
        targets, _ = load_exposure_targets(directory, allocation)
        if targets is None:
            raise DataError('The active exposure policy is missing. Save targets or activate capital planning.')
    except DataError as exc:
        st.error(str(exc))
        return
    st.caption(f'Exposure plan · {targets.universe} universe · {targets.multiple:g}× net equity · Gap = desired − planned')
    names = dict(zip(valued.position_id, valued.name))
    if future:
        names['future:overlay'] = future.instrument + ' future'
    permitted = st.multiselect('Permitted instruments', list(names), format_func=names.get, key='planning_exposure_instruments')
    contribution = st.number_input('External contribution (EUR)', min_value=0., value=0., key='planning_exposure_contribution')
    with st.expander('Funding, fees & execution constraints', expanded=True):
        sales = st.checkbox('Allow funded-asset sales', value=False, key='planning_exposure_sales')
        fee = st.number_input('Funded trade fee per unit (EUR)', min_value=0., value=0., key='planning_exposure_fee')
        future_fee = collateral = funding = 0.
        margin, limit, shorts = None, 10000, False
        if 'future:overlay' in permitted:
            collateral = st.number_input('Eligible existing future collateral (EUR)', min_value=0., value=None, key='planning_exposure_collateral')
            funding = st.number_input('Contribution dedicated to future (EUR)', min_value=0., value=0., key='planning_exposure_funding')
            margin = st.number_input('Declared initial margin per contract (EUR)', min_value=0., value=None, key='planning_exposure_margin')
            future_fee = st.number_input('Future fee per traded contract (EUR)', min_value=0., value=0., key='planning_exposure_future_fee')
            limit = st.number_input('Maximum absolute contracts', min_value=0, max_value=10000, value=max(1, abs(future.contracts)), key='planning_exposure_limit')
            shorts = st.checkbox('Permit short futures', value=False, key='planning_exposure_shorts')
        st.caption('Purchases use only the contribution and permitted sale proceeds. Future funding is part of that contribution; notional never consumes a cash purchase budget. '
                   'Declare eligible collateral and initial margin; no automatic transfers or broker eligibility are assumed. Funded units may be fractional; contracts are whole. Fees reduce equity.')
    if st.button('Calculate exposure plan', type='primary'):
        try:
            plan = plan_exposure(result, targets, allocation, valued, permitted=permitted, future=future,
                contribution=contribution, allow_sales=sales, fee_per_unit=fee,
                future_fee=future_fee, collateral_limit=collateral, future_funding=funding,
                margin_per_contract=margin, max_contracts=int(limit), allow_short=shorts)
        except DataError as exc:
            st.error(str(exc))
            return
        st.subheader('Suggested exposure trades')
        if plan.trades.empty:
            st.info('No improving trades within the permitted instruments and constraints.')
        else:
            st.dataframe(plan.trades.drop(columns='Position ID'), hide_index=True, width='stretch')
        st.success('Targets reached within EUR 0.01.') if plan.complete else st.warning('Remaining target gaps: the permitted trades and constraints do not reach every target.')
        st.dataframe(plan.gaps.drop(columns='Bucket ID'), hide_index=True, width='stretch')
        st.caption(f'Planned equity €{plan.equity:,.2f} · Fees €{plan.fees:,.2f} · Unallocated contribution / sale proceeds €{plan.unallocated_cash:,.2f}')
        if 'future:overlay' in permitted:
            st.caption(f'Initial margin €{plan.margin_required:,.2f} · Declared collateral after fees €{plan.collateral_remaining:,.2f}')
        st.caption('Preview only, at current marks. Minimizes total absolute category gaps, then turnover. Residual cash remains in net equity with zero risk-bearing exposure. No orders or funding transactions are saved.')
