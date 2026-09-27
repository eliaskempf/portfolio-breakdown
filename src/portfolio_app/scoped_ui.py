"""Explicit portfolio and sleeve planning scopes."""
from hashlib import sha256

import pandas as pd
import streamlit as st
from portfolio_app.view_state import persistent_editor

from portfolio_app.allocation import macro_table
from portfolio_app.strategic import category_labels
from portfolio_app.rebalance_ui import render_rebalancing
from portfolio_app.rebalancing import RebalanceError
from portfolio_app.scoped_rebalancing import portfolio_contribution, sleeve_positions


def render_scoped_rebalancing(valued, config):
    workflow = st.radio('Planning scope', key='planning_scope', options=['Portfolio contribution', 'Within a bucket'])
    if valued is None:
        st.info('Load positions before planning.')
        return
    total = float(valued.current_value_eur.sum()) if valued.current_value_eur.notna().all() else None
    if workflow == 'Within a bucket':
        names = category_labels(config)
        leaves = sorted(config.leaves())
        if not leaves:
            st.info('Configure a bucket first.')
            return
        selected = st.selectbox('Planning bucket', key='planning_bucket', options=leaves, format_func=names.get)
        try:
            positions = sleeve_positions(valued, config, selected)
            protected = next(b.sell_protected for b in config.buckets if b.id == selected)
            # Parent protection applies to descendants as well.
            node = next(b for b in config.buckets if b.id == selected)
            while node.parent:
                node = next(b for b in config.buckets if b.id == node.parent)
                protected |= node.sell_protected
            plan = render_rebalancing(positions, scope=names[selected], portfolio_value=total, sell_protected=protected)
            if plan is not None:
                after = valued.copy()
                deltas = plan.table.set_index('position_id')['Trade (EUR)']
                after['current_value_eur'] += after.position_id.map(deltas).fillna(0)
                st.subheader('Portfolio impact')
                st.dataframe(macro_table(after, config, extra_cash=plan.unallocated_cash), hide_index=True)
        except RebalanceError as exc:
            st.info(str(exc))
        return
    st.caption('Route new money by strategic bucket targets first, then optimize each internal mix. Budgets stay fixed during trade-count comparisons. All orders refer to source instruments.')
    amount = st.number_input('Portfolio contribution (EUR)', key='planning_amount', min_value=.01, value=500., step=50.)
    with st.expander('Advanced planning settings'):
        macro_tolerance = st.number_input('Bucket tolerance (pp of parent)', key='planning_macro_tolerance', min_value=0., max_value=100., value=.5)
        position_tolerance = st.number_input('Position tolerance (pp of bucket)', key='planning_position_tolerance', min_value=0., max_value=100., value=.5)
        labels = {r.position_id: f'{r["name"]} · {r.get("account", "")} · {next((b.name for b in config.buckets if b.id == r.bucket_id), "Unassigned")}' for _, r in valued.iterrows()}
        ids = st.multiselect('Positions eligible for portfolio contribution', key='planning_eligible', options=list(labels), default=list(labels), format_func=labels.get)
        buy_all = st.radio('Portfolio purchase intent', key='planning_intent', options=['Allow skipping positions', 'Buy every selected position']) == 'Buy every selected position'
        minimum = st.number_input('Minimum portfolio purchase (EUR)', key='planning_minimum', min_value=.01, value=25.)
        no_new = st.checkbox('No new positions in portfolio contribution', key='planning_no_new')
        max_trades = int(st.number_input('Maximum portfolio trades', key='planning_max_trades', min_value=1, max_value=max(1, len(valued)), value=max(1, len(valued))))
        fewer = st.checkbox('Compare fewer portfolio trades', key='planning_fewer')
        cap_scope = st.selectbox('Portfolio plan cap denominator', key='planning_cap_scope', options=['portfolio', 'bucket'], format_func=lambda value: '% of whole portfolio' if value == 'portfolio' else '% of selected category')
        caps = {}
        if st.checkbox('Temporary allocation caps', key='planning_caps_enabled'):
            rows = pd.DataFrame({'position_id': ids, 'Position': [labels[k] for k in ids], 'Maximum %': [float('nan')] * len(ids)})
            edited = persistent_editor(rows, disabled=['position_id', 'Position'], hide_index=True,
                                    key='portfolio_caps_' + sha256(repr(ids).encode()).hexdigest(),
                                    column_config={'position_id': None, 'Maximum %': st.column_config.NumberColumn(min_value=0., max_value=100.)})
            caps = {r.position_id: r['Maximum %'] / 100 for _, r in edited.iterrows() if pd.notna(r['Maximum %'])}
    fields = [c for c in ('position_id', 'id', 'name', 'ticker', 'account', 'portfolio', 'shares', 'current_value_eur', 'target_allocation', 'bucket_id', 'within_bucket_target') if c in valued]
    fingerprint = sha256((valued[fields].to_json() + repr((config, amount, ids, buy_all, minimum, no_new, max_trades, macro_tolerance, position_tolerance, caps, cap_scope, fewer))).encode()).hexdigest()
    if st.button('Calculate portfolio contribution', type='primary'):
        st.session_state.pop('portfolio_contribution_result', None)
        try:
            options = dict(eligible_ids=ids, minimum_purchase=minimum, buy_all=buy_all, no_new_positions=no_new,
                           macro_tolerance=macro_tolerance, position_tolerance=position_tolerance,
                           max_allocations=caps, cap_scope=cap_scope)
            plans = []
            for limit in (range(1, max_trades + 1) if fewer else [max_trades]):
                try:
                    plans.append(portfolio_contribution(valued, config, amount, max_trades=limit, **options))
                except RebalanceError:
                    if limit == max_trades:
                        raise
            st.session_state['portfolio_contribution_result'] = (fingerprint, plans)
        except RebalanceError as exc:
            st.error(str(exc))
    saved = st.session_state.get('portfolio_contribution_result')
    if not saved or saved[0] != fingerprint:
        return
    plans = saved[1]
    index = st.selectbox('Portfolio plan to inspect', key='planning_plan', options=range(len(plans)), index=len(plans)-1,
                         format_func=lambda i: f'{int(plans[i].trades["Trade (EUR)"].ne(0).sum())} trades · €{plans[i].unallocated_cash:.2f} unallocated') if len(plans) > 1 else 0
    plan = plans[index]
    st.dataframe(plan.budgets, hide_index=True)
    st.dataframe(plan.trades, hide_index=True)
    st.metric('Unallocated contribution', f'€{plan.unallocated_cash:,.2f}')
    st.caption('Unallocated contribution stays in the portfolio denominator. It is not a saved cash holding or an executed trade.')
    st.dataframe(macro_table(plan.after, config, extra_cash=plan.unallocated_cash), hide_index=True)
