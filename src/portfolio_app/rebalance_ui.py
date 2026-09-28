"""Interactive, read-only position plans, shared by legacy and category scopes."""
from hashlib import sha256

import pandas as pd
import numpy as np
import streamlit as st
from portfolio_app.view_state import persistent_editor
from portfolio_app.planning_ui import PLANNING_HELP, planning_positions, restriction_summary
from portfolio_app.rebalance_tables import allocation_table, position_labels
from portfolio_app.rebalance_results_ui import render_impact, render_summary, render_trades, show_table
from portfolio_app.rebalancing import (
    RebalanceError, balanced_cash_tradeoffs, cash_tradeoffs, minimum_new_money, minimum_trades, prepare_rebalance, spread_new_money,
)

MODES = ["Fewest trades (buys and sells)", "Minimum new money (no sells)", "Allocate new money"]


def render_rebalancing(valued: pd.DataFrame | None, *, scope: str = '', portfolio_value: float | None = None,
                       sell_protected: bool = False, portfolio_positions=None, allocation=None):
    if valued is None:
        st.info("A portfolio valuation is required before calculating.")
        return
    original = valued
    st.caption(f'Targets relative to {scope or "portfolio"}.' + (' Selling is protected for this category.' if sell_protected else ''))
    mode_col, amount_col, options_col = st.columns([3, 2, 1], vertical_alignment='bottom')
    mode = mode_col.selectbox("Rebalancing mode", MODES, key="rebalance_mode")
    new_money = amount_col.number_input("New money (EUR)", min_value=0., value=500., step=100., key="rebalance_cash") if mode == MODES[2] else 0.
    with options_col.popover('Options', width='stretch'):
        st.markdown('**Positions**')
        valued = planning_positions(valued)
        no_new = st.toggle("Only buy existing positions", key="rebalance_no_new",
                           help="Do not buy zero-quantity positions. Their targets remain unless redistribution is enabled.")
        distribution = "Optimize rebalancing"
        eligible_ids = None
        buy_all, prefer_fewer, minimum_purchase, extra_error = False, False, .01, 0.
        max_allocations = {}
        cap_scope = 'portfolio'
        labels = position_labels(valued)
        identity = sha256(valued[[c for c in ('position_id', 'name', 'account', 'portfolio') if c in valued]].to_json().encode()).hexdigest()[:16]
        limited = mode == MODES[2] and st.toggle("Limit buys to selected positions", key="rebalance_limit_buys")
        if limited:
            eligible_ids = st.multiselect("Positions eligible for buying", list(labels), format_func=labels.get,
                                          key=f"rebalance_buy_positions_{identity}",
                                          help="Unselected positions keep their targets and receive no trades.")
        st.markdown('**Purchase rules**')
        if limited:
            distribution = st.selectbox("Distribution", ["Rebalance selected positions", "Spread by target weights", "Optimize rebalancing"],
                key="rebalance_distribution", help="Rebalance selected positions minimizes squared percentage-point gaps to exact targets. Spread by target weights divides the contribution proportionally. Optimize rebalancing minimizes deviation outside tolerance ranges.")
            if distribution == "Rebalance selected positions":
                buy_all = st.selectbox("Selection intent", ["Buy every selected position", "Allow skipping positions"],
                                       key="rebalance_buy_intent") == "Buy every selected position"
                minimum_purchase = st.number_input("Minimum purchase (EUR)", min_value=.01, value=25., step=5.,
                                                    key="rebalance_minimum_purchase")
                if buy_all:
                    st.caption(f'Minimum contribution for this selection: €{len(eligible_ids) * minimum_purchase:,.2f}')
                else:
                    prefer_fewer = st.toggle("Prefer fewer trades", key="rebalance_prefer_fewer",
                        help="Choose the fewest trades within your allowed extra target error, while investing as much as possible.")
                    if prefer_fewer:
                        extra_error = st.number_input("Allowed extra target error (pp)", min_value=0., value=.1, step=.05,
                            key="rebalance_extra_error", help="Additional RMS target gap compared with the best plan under your trade limit. Separate from tolerance ranges.")
        spreading = distribution != "Optimize rebalancing"
        balancing = distribution == "Rebalance selected positions"
        max_trades = int(st.number_input("Maximum trades", min_value=1, max_value=max(1, len(valued)),
            value=min(4, max(1, len(valued))), step=1, key=f"rebalance_max_trades_{len(valued)}")) if mode == MODES[2] and (not spreading or prefer_fewer) else len(valued)
        st.markdown('**Tolerances & caps**')
        tolerance_type = st.selectbox("Tolerance type", ["Percentage points", "Relative to target"], key="rebalance_tolerance_type",
            help="A 10% target with ±0.5 percentage points or 5% relative tolerance has a 9.5–10.5% range. Zero targets have a zero relative range.")
        relative = tolerance_type == "Relative to target"
        tolerance = st.number_input("Allowed deviation (% of target)" if relative else "Allowed deviation (pp)",
                                    min_value=0., max_value=100., value=5. if relative else .5, step=.1,
                                    key=f"rebalance_tolerance_{relative}")
        if balancing and st.toggle("Limit allocations for this rebalance", key="rebalance_limit_allocations"):
            if scope:
                cap_scope = st.selectbox('Cap denominator', ['portfolio', 'bucket'], key='rebalance_cap_scope',
                                         format_func=lambda v: '% of portfolio' if v == 'portfolio' else '% of category')
            active = valued.loc[valued.position_id.isin(eligible_ids)]
            cap_rows = active[["position_id", "target_allocation"]].copy().reset_index(drop=True)
            cap_rows["Investment"] = cap_rows.position_id.map(labels)
            cap_rows["Target %"] = cap_rows.target_allocation * 100
            cap_rows["Max allocation %"] = pd.Series([None] * len(cap_rows), dtype="float64")
            cap_key = sha256(repr((identity, tuple(active.position_id))).encode()).hexdigest()[:16]
            cap_rows = persistent_editor(cap_rows.drop(columns="target_allocation"), hide_index=True, width="stretch", height='content',
                disabled=["position_id", "Investment", "Target %"], key=f"rebalance_caps_{cap_key}",
                column_config={"position_id": None,
                    "Target %": st.column_config.NumberColumn(f"Target (% of {scope or 'portfolio'})", format="%.2f %%"),
                    "Max allocation %": st.column_config.NumberColumn("Maximum allocation (%)", min_value=0., max_value=100., step=.1, format="%.2f %%",
                        help="Blank means no cap. Uses final value including unallocated contribution. Limits are temporary and reset when the selection changes.")})
            max_allocations = {row.position_id: float(row["Max allocation %"]) / 100 for _, row in cap_rows.iterrows()
                               if pd.notna(row["Max allocation %"])}
        st.caption(PLANNING_HELP)
    restriction_summary(no_new=no_new, caps=max_allocations, selected=None if eligible_ids is None else len(eligible_ids),
                        maximum=max_trades if mode == MODES[2] and (not spreading or prefer_fewer) else None)
    if valued.empty:
        st.info('All positions have zero shares or this category has no positions. Disable target redistribution or add positions to allocate new money.')
        return
    try:
        problem = prepare_rebalance(valued, tolerance=tolerance, tolerance_type="relative" if relative else "pp")
    except RebalanceError as exc:
        st.info(str(exc))
        return
    fields = [column for column in ("position_id", "id", "name", "ticker", "account", "portfolio", "shares", "current_value_eur", "target_allocation") if column in valued]
    fingerprint = sha256((valued[fields].to_json() + repr((st.session_state.get("ignore_empty_positions"), scope, portfolio_value, sell_protected, cap_scope, mode, tolerance_type, tolerance, no_new, new_money, max_trades, distribution, buy_all, minimum_purchase, prefer_fewer, extra_error, tuple(sorted(max_allocations.items())), None if eligible_ids is None else tuple(sorted(eligible_ids))))).encode()).hexdigest()
    entered_caps = max_allocations.copy()
    if scope and cap_scope == 'portfolio' and max_allocations:
        if portfolio_value is None:
            st.info('Whole-portfolio caps require complete portfolio valuation; select category-relative caps instead.')
            return
        max_allocations = {key: min(1., cap * (portfolio_value + new_money) / (problem.total + new_money)) for key, cap in max_allocations.items()}
    if st.button("Calculate plan", type="primary", key="rebalance_calculate"):
        st.session_state.pop("rebalance_result", None)
        try:
            with st.spinner("Calculating your trade plan…"):
                if mode == MODES[0]:
                    plans = [minimum_trades(problem, no_new_positions=no_new,
                                            sell_allowed=np.zeros(len(problem.values), dtype=bool) if sell_protected else None)]
                elif mode == MODES[1]:
                    plans = [minimum_new_money(problem, no_new_positions=no_new)]
                elif balancing:
                    plans = balanced_cash_tradeoffs(problem, new_money, eligible_position_ids=eligible_ids,
                                                     minimum_purchase=minimum_purchase, buy_all=buy_all,
                                                     max_trades=max_trades, no_new_positions=no_new, max_allocations=max_allocations)
                    if not prefer_fewer:
                        plans = plans[-1:]
                elif spreading:
                    plans = [spread_new_money(problem, new_money, eligible_position_ids=eligible_ids,
                                               method="target", no_new_positions=no_new)]
                else:
                    bar = st.progress(0, text="Comparing trade counts")
                    try:
                        plans = cash_tradeoffs(problem, new_money, max_trades=max_trades, no_new_positions=no_new, eligible_position_ids=eligible_ids,
                                               progress=lambda done, total: bar.progress(done / total, text=f"Checked up to {done} trades"))
                    finally:
                        bar.empty()
            st.session_state["rebalance_result"] = (fingerprint, plans)
        except RebalanceError as exc:
            st.error(str(exc))
            return
    cached = st.session_state.get("rebalance_result")
    if cached is None or cached[0] != fingerprint:
        return
    plans = cached[1]
    index = len(plans) - 1
    if prefer_fewer:
        least_cash = min(plan.unallocated_cash for plan in plans)
        best_error = min(plan.target_rms for plan in plans if plan.unallocated_cash == least_cash)
        index = next(i for i, plan in enumerate(plans) if plan.unallocated_cash == least_cash and plan.target_rms <= best_error + extra_error + 1e-10)
    results = st.container()
    with st.expander('Plan details'):
        if len(plans) > 1:
            frontier = pd.DataFrame({
                "Trades": [plan.trade_count for plan in plans],
                **({"RMS target gap (pp)": [plan.target_rms for plan in plans]} if balancing else {}),
                "Unallocated cash (EUR)": [plan.unallocated_cash for plan in plans],
                "Deviation outside ranges (pp)": [plan.deviation_after for plan in plans],
                "All positions in range": [plan.within_bands for plan in plans],
            })
            show_table(frontier)
            index = st.selectbox("Plan to inspect", list(range(len(plans))), index=index,
                                 format_func=lambda i: f"{plans[i].trade_count} trades · €{plans[i].unallocated_cash:.2f} unallocated",
                                 key=f"rebalance_plan_{fingerprint}")
        plan = plans[index]
        st.metric("Deviation outside ranges", f"{plan.deviation_after:.3f} pp",
                  help="Sum of position distances outside their allowed ranges. Zero means every position is within range, not necessarily exactly on target.")
        if balancing:
            st.metric("RMS target gap", f"{plan.target_rms:.3f} pp",
                      help="Root mean squared percentage-point gap to targets. Lower is better; large gaps count more.")
        positions = original.copy()
        positions['target_allocation'] = positions.position_id.map(valued.set_index('position_id').target_allocation).where(
            positions.position_id.isin(valued.position_id), 0.)
        table = allocation_table(positions, plan.table, new_money=plan.new_money)
        for column in ('Lower %', 'Upper %'):
            table[column] = positions.position_id.map(plan.table.set_index('position_id')[column]).to_numpy()
        if entered_caps:
            table['Max allocation %'] = positions.position_id.map(entered_caps).to_numpy() * 100
        st.markdown('**Full allocation**')
        st.caption(f'Percentages of {scope or "portfolio"}, including unallocated contribution. Targets use the current planning options.')
        show_table(table.sort_values('After (EUR)', ascending=False, kind='stable'), scope=scope or 'portfolio',
                   cap_scope='category' if cap_scope == 'bucket' else 'portfolio')
    with results:
        render_summary(table, plan.new_money, plan.unallocated_cash, minimum=mode == MODES[1])
        if plan.within_bands:
            st.success("Every position is within its target range.")
        else:
            st.info("Some positions remain outside their target ranges.")
        if plan.deviation_after > plan.deviation_before + 1e-5:
            st.warning("This contribution increases deviation from the planning scope’s target ranges.")
        render_trades(table)
        if allocation is not None and portfolio_positions is not None:
            after = portfolio_positions.copy()
            after['current_value_eur'] += after.position_id.map(plan.table.set_index('position_id')['Trade (EUR)']).fillna(0.)
            render_impact(portfolio_positions, after, allocation, cash=plan.unallocated_cash, key='rebalance_impact_parent')
    return plan
