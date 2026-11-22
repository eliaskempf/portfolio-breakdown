"""Category-aware planning using existing result components and source trades."""
from hashlib import sha256

import pandas as pd
import streamlit as st
from portfolio_app.interface.shared.currency_display import money_label

from portfolio_app.portfolio.category_rebalancing import category_plan
from portfolio_app.portfolio.rebalancing import RebalanceError
from portfolio_app.portfolio.rebalance_tables import position_labels
from portfolio_app.portfolio.strategic import bucket_positions, category_labels
from portfolio_app.interface.views.portfolio.rebalance_results_ui import render_summary, render_trades, render_impact, show_table
from portfolio_app.interface.views.portfolio.rebalance_ui import MODES
from portfolio_app.interface.shared.planning_ui import planning_positions
from portfolio_app.interface.shared.view_state import persistent_editor


def render_category_plan(valued, config, scope):
    names = category_labels(config)
    positions = bucket_positions(valued, config, scope)
    if positions.empty:
        st.info('Add positions to this category before planning.')
        return
    mode_col, amount_col, options_col = st.columns([3, 2, 1], vertical_alignment='bottom')
    mode = mode_col.selectbox('Rebalancing mode', MODES, key='rebalance_category_rebalance_mode')
    kind = {MODES[0]: 'fewest', MODES[1]: 'minimum', MODES[2]: 'contribution'}[mode]
    amount = amount_col.number_input(money_label('New money'), min_value=0., value=500., step=100., key='rebalance_category_rebalance_amount') if kind == 'contribution' else 0.
    with options_col.popover('Options', width='stretch'):
        positions = planning_positions(positions, config)
        if positions.empty:
            st.info('All positions are excluded. Disable empty-position exclusion to plan purchases.')
            return
        labels = position_labels(positions, config)
        eligible = st.multiselect('Positions eligible for buying', list(labels), default=list(labels), format_func=labels.get,
                                 key=f'rebalance_category_buy_{scope}_{tuple(labels)}')
        no_new = st.toggle('Only buy existing positions', key='rebalance_category_no_new')
        maximum = int(st.number_input('Maximum trades', min_value=1, max_value=len(positions), value=len(positions), key=f'rebalance_category_trades_{len(positions)}'))
        buy_all = False
        minimum = .01
        if kind == 'contribution':
            buy_all = st.selectbox('Selection intent', ['Allow skipping positions', 'Buy every selected position'], key='rebalance_category_intent') == 'Buy every selected position'
            minimum = st.number_input(money_label('Minimum purchase'), min_value=.01, value=25., key='rebalance_category_minimum')
        relative = st.selectbox('Tolerance type', ['Percentage points', 'Relative to target'], key='rebalance_category_tolerance_type') == 'Relative to target'
        unit = '% of target' if relative else 'pp of parent'
        category_tolerance = st.number_input(f'Category tolerance ({unit})', min_value=0., max_value=100., value=5. if relative else .5, key=f'rebalance_category_tolerance_{relative}')
        position_tolerance = st.number_input(f'Position tolerance ({unit})', min_value=0., max_value=100., value=5. if relative else .5, key=f'rebalance_category_position_tolerance_{relative}')
        caps = {}
        cap_scope = 'bucket'
        if kind == 'contribution' and st.toggle('Temporary allocation caps', key='rebalance_category_caps'):
            cap_scope = st.selectbox('Cap denominator', ['bucket', 'portfolio'], format_func=lambda v: '% of owning category' if v == 'bucket' else '% of portfolio', key='rebalance_category_cap_scope')
            rows = pd.DataFrame({'position_id': eligible, 'Investment': [labels[k] for k in eligible], 'Maximum %': [None]*len(eligible)})
            edited = persistent_editor(rows, hide_index=True, disabled=['position_id','Investment'],
                column_config={'position_id': None, 'Maximum %': st.column_config.NumberColumn(min_value=0., max_value=100.)}, key=f'rebalance_category_cap_rows_{scope}_{tuple(eligible)}')
            caps = {row.position_id: row['Maximum %']/100 for _, row in edited.iterrows() if pd.notna(row['Maximum %'])}
    options = dict(mode=kind, amount=amount, category_tolerance=category_tolerance, position_tolerance=position_tolerance,
                   relative=relative, eligible_ids=eligible, no_new_positions=no_new, max_trades=maximum,
                   minimum_purchase=minimum, buy_all=buy_all, max_allocations=caps, cap_scope=cap_scope,
                   portfolio_value=float(valued.current_value_reporting.sum()) if valued.current_value_reporting.notna().all() else None)
    fingerprint = sha256((repr((config, scope, options))+positions.to_json()).encode()).hexdigest()
    if st.button('Calculate plan', type='primary', key='rebalance_category_calculate'):
        st.session_state.pop('rebalance_category_rebalance_result', None)
        try:
            if caps and cap_scope == 'portfolio' and options['portfolio_value'] is None:
                raise RebalanceError('Whole-portfolio caps require complete portfolio valuation.')
            with st.spinner('Calculating your trade plan…'):
                plan = category_plan(positions, config, scope, **options)
            st.session_state['rebalance_category_rebalance_result'] = (fingerprint, plan)
        except RebalanceError as exc:
            st.error(str(exc))
    cached = st.session_state.get('rebalance_category_rebalance_result')
    if cached is None or cached[0] != fingerprint:
        return
    plan = cached[1]
    table = plan.table.rename(columns={'name':'Investment', 'account':'Account'}).drop(columns=['position_id','id','ticker','portfolio'], errors='ignore')
    render_summary(table, plan.new_money, plan.unallocated_cash, minimum=kind == 'minimum')
    st.caption('All target ranges satisfied.' if plan.within_bands else 'Some target ranges remain outside tolerance.')
    render_trades(table)
    after = valued.copy()
    after['current_value_reporting'] += after.position_id.map(plan.table.set_index('position_id').Trade).fillna(0.)
    render_impact(valued, after, config, cash=plan.unallocated_cash, key='rebalance_category_plan_impact')
    with st.expander('Plan details'):
        st.metric('Deviation outside ranges', f'{plan.deviation_after:.3f} pp')
        show_table(table, scope=names[scope])
        for note in plan.priority_notes:
            st.caption(note)
