"""Explicit portfolio and category planning scopes."""
from hashlib import sha256

import pandas as pd
import streamlit as st

from portfolio_app.view_state import persistent_editor
from portfolio_app.strategic import category_labels
from portfolio_app.planning_ui import PLANNING_HELP, planning_positions, restriction_summary
from portfolio_app.rebalance_ui import render_rebalancing
from portfolio_app.rebalance_tables import allocation_table, category_budgets, position_labels
from portfolio_app.rebalance_results_ui import render_impact, render_summary, render_trades, show_table
from portfolio_app.rebalancing import RebalanceError
from portfolio_app.scoped_rebalancing import portfolio_contribution, sleeve_positions


def render_scoped_rebalancing(valued, config):
    if st.session_state.get('planning_scope') == 'Within a bucket':
        st.session_state['planning_scope'] = 'Within a category'
    workflow = st.segmented_control('Planning scope', key='planning_scope',
        options=['Portfolio contribution', 'Within a category'], default='Portfolio contribution') or 'Portfolio contribution'
    if valued is None or valued.empty:
        st.info('Load positions before planning.')
        return
    total = float(valued.current_value_eur.sum()) if valued.current_value_eur.notna().all() else None
    if workflow == 'Within a category':
        names = category_labels(config)
        leaves = sorted(config.leaves(), key=names.get)
        if not leaves:
            st.info('Configure a category in Targets first.')
            return
        if st.session_state.get('planning_bucket') not in leaves:
            st.session_state['planning_bucket'] = leaves[0]
        selected = st.selectbox('Planning category', key='planning_bucket', options=leaves, format_func=names.get)
        try:
            positions = sleeve_positions(valued, config, selected)
            protected = False
            node = next(b for b in config.buckets if b.id == selected)
            while True:
                protected |= node.sell_protected
                if not node.parent:
                    break
                node = next(b for b in config.buckets if b.id == node.parent)
            render_rebalancing(positions, scope=names[selected], portfolio_value=total, sell_protected=protected,
                               portfolio_positions=valued, allocation=config)
        except RebalanceError as exc:
            st.info(str(exc))
        return
    st.caption('Allocate across category targets, then balance positions within each category.')
    amount_col, options_col = st.columns([3, 1], vertical_alignment='bottom')
    amount = amount_col.number_input('Contribution (EUR)', key='planning_amount', min_value=.01, value=500., step=50.)
    original = valued
    with options_col.popover('Options', width='stretch'):
        st.markdown('**Positions**')
        valued = planning_positions(valued, config)
        labels = position_labels(valued, config)
        identity = sha256(repr(tuple(labels)).encode()).hexdigest()[:12]
        ids = st.multiselect('Positions eligible for buying', key=f'planning_eligible_{identity}',
                            options=list(labels), default=list(labels), format_func=labels.get)
        no_new = st.toggle('Only buy existing positions', key='planning_no_new',
                           help='Do not buy zero-quantity positions. Their targets remain unless redistribution is enabled.')
        st.markdown('**Purchase rules**')
        buy_all = st.selectbox('Selection intent', key='planning_intent',
                               options=['Allow skipping positions', 'Buy every selected position']) == 'Buy every selected position'
        minimum = st.number_input('Minimum purchase (EUR)', key='planning_minimum', min_value=.01, value=25.)
        max_trades = int(st.number_input('Maximum trades', key=f'planning_max_trades_{len(valued)}',
                         min_value=1, max_value=max(1, len(valued)), value=max(1, len(valued))))
        fewer = st.toggle('Compare fewer trades', key='planning_fewer')
        st.markdown('**Tolerances & caps**')
        macro_tolerance = st.number_input('Category tolerance (pp of parent)', key='planning_macro_tolerance', min_value=0., max_value=100., value=.5,
            help='A 10% target with 0.5 percentage points of tolerance has a 9.5–10.5% range.')
        position_tolerance = st.number_input('Position tolerance (pp of category)', key='planning_position_tolerance', min_value=0., max_value=100., value=.5)
        caps, cap_scope = {}, 'portfolio'
        if st.toggle('Temporary allocation caps', key='planning_caps_enabled'):
            cap_scope = st.selectbox('Cap denominator', key='planning_cap_scope', options=['portfolio', 'bucket'],
                                     format_func=lambda value: '% of portfolio' if value == 'portfolio' else '% of category')
            rows = pd.DataFrame({'position_id': ids, 'Position': [labels[k] for k in ids], 'Maximum %': [float('nan')] * len(ids)})
            edited = persistent_editor(rows, disabled=['position_id', 'Position'], hide_index=True, height='content',
                key='portfolio_caps_' + sha256(repr((identity, ids)).encode()).hexdigest(),
                column_config={'position_id': None, 'Maximum %': st.column_config.NumberColumn(min_value=0., max_value=100.,
                    help='Blank means no cap. Uses final value including unallocated contribution. Limits are temporary.')})
            caps = {r.position_id: r['Maximum %'] / 100 for _, r in edited.iterrows() if pd.notna(r['Maximum %'])}
        st.caption(PLANNING_HELP)
    restriction_summary(no_new=no_new, caps=caps, selected=len(ids), maximum=max_trades)
    if valued.empty:
        st.info('All positions have zero shares. Disable target redistribution to allocate new money to them.')
        return
    fields = [c for c in ('position_id', 'id', 'name', 'ticker', 'account', 'portfolio', 'shares', 'current_value_eur', 'target_allocation', 'bucket_id', 'within_bucket_target') if c in valued]
    fingerprint = sha256((valued[fields].to_json() + repr((config, amount, ids, buy_all, minimum, no_new, max_trades, macro_tolerance, position_tolerance, caps, cap_scope, fewer,
                                                         st.session_state.get('ignore_empty_positions')))).encode()).hexdigest()
    if st.button('Calculate plan', key='planning_calculate', type='primary'):
        st.session_state.pop('portfolio_contribution_result', None)
        try:
            options = dict(eligible_ids=ids, minimum_purchase=minimum, buy_all=buy_all, no_new_positions=no_new,
                           macro_tolerance=macro_tolerance, position_tolerance=position_tolerance,
                           max_allocations=caps, cap_scope=cap_scope)
            plans = []
            with st.spinner('Calculating your trade plan…'):
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
    # Mount this below the main results, but obtain the selection before rendering them.
    results = st.container()
    with st.expander('Plan details'):
        index = st.selectbox('Plan to inspect', key=f'planning_plan_{fingerprint}', options=range(len(plans)), index=len(plans)-1,
            format_func=lambda i: f'{int(plans[i].trades["Trade (EUR)"].ne(0).sum())} trades · €{plans[i].unallocated_cash:.2f} unallocated') if len(plans) > 1 else 0
        plan = plans[index]
        positions = original.copy()
        positions['target_allocation'] = positions.position_id.map(valued.set_index('position_id').target_allocation)
        positions.loc[~positions.position_id.isin(valued.position_id), 'target_allocation'] = 0.
        table = allocation_table(positions, plan.trades, new_money=amount, config=config)
        st.markdown('**Full allocation**')
        st.caption('Percentages of portfolio. Targets use the current planning options.')
        show_table(table.sort_values('After (EUR)', ascending=False, kind='stable'))
        st.markdown('**Category budgets**')
        st.caption('Reserved budgets are fixed before comparing trade counts. Purchase rules can leave part of a budget unallocated.')
        show_table(category_budgets(plan.budgets, plan.trades, original, config))
    with results:
        render_summary(table, amount, plan.unallocated_cash)
        render_trades(table)
        after = original.copy()
        after['current_value_eur'] += after.position_id.map(plan.trades.set_index('position_id')['Trade (EUR)']).fillna(0.)
        render_impact(original, after, config, cash=plan.unallocated_cash, key='planning_impact_parent')
