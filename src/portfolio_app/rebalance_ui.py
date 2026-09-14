"""Interactive, read-only rebalance plans for the whole portfolio."""

from collections import Counter
from hashlib import sha256

import pandas as pd
import streamlit as st

from portfolio_app.display_names import display_name
from portfolio_app.rebalancing import (
    RebalanceError, cash_tradeoffs, minimum_new_money, minimum_trades, prepare_rebalance, spread_new_money,
)

MODES = ["Fewest trades (buys and sells)", "Minimum new money (no sells)", "Allocate new money"]


def render_rebalancing(valued: pd.DataFrame | None) -> None:
    st.subheader("Rebalance your portfolio")
    st.caption("Plans use the whole portfolio’s position targets. Overview filters, label selections and ETF display groups do not change the trade universe. "
               "Ignore empty positions applies here too. One trade means a net buy or sell for one position/account row.")
    if valued is None:
        st.info("A portfolio valuation is required before calculating.")
        return
    mode = st.selectbox("Rebalancing mode", MODES, key="rebalance_mode")
    left, right = st.columns(2)
    with left:
        tolerance_type = st.selectbox("Tolerance type", ["Percentage points", "Relative to target"], key="rebalance_tolerance_type")
        relative = tolerance_type == "Relative to target"
        tolerance = st.number_input("Allowed deviation (% of target)" if relative else "Allowed deviation (pp)",
                                    min_value=0., max_value=100., value=5. if relative else .5, step=.1,
                                    key=f"rebalance_tolerance_{relative}")
    with right:
        no_new = st.checkbox("No new positions", key="rebalance_no_new",
                             help="Only buy position rows that already hold shares. Empty positions keep their targets unless Ignore empty positions is enabled.")
        new_money = st.number_input("New money (EUR)", min_value=0., value=500., step=100., key="rebalance_cash") if mode == MODES[2] else 0.
    distribution = "Optimize rebalancing"
    eligible_ids = None
    if mode == MODES[2] and st.checkbox("Limit buys to selected positions", key="rebalance_limit_buys"):
        identity_fields = [column for column in ("position_id", "id", "name", "ticker", "account", "portfolio") if column in valued]
        identity = sha256(valued[identity_fields].to_json().encode()).hexdigest()[:16]
        labels = {row.position_id: " · ".join(str(part) for part in (
            display_name(row["name"]),
            row.get("account", "") or "No account", row.get("portfolio", "") or "No portfolio") if part)
                  for _, row in valued.iterrows()}
        duplicates = Counter(labels.values())
        labels = {key: f"{label} [row {i + 1}]" if duplicates[label] > 1 else label for i, (key, label) in enumerate(labels.items())}
        eligible_ids = st.multiselect("Positions eligible for buying", list(labels), format_func=labels.get,
                                      key=f"rebalance_buy_positions_{identity}",
                                      help="Choose instrument/account rows that may receive new money. Unselected positions stay invested, keep their targets and receive no trades.")
        active = valued.loc[valued.position_id.isin(eligible_ids)]
        allowed_count = int((active.shares > 0).sum()) if no_new else len(active)
        distribution = st.selectbox("Distribution", ["Spread equally", "Spread by target weights", "Optimize rebalancing"], key="rebalance_distribution")
        st.caption(f"{len(eligible_ids)} selected · {allowed_count} eligible after position restrictions.")
        if distribution == "Optimize rebalancing":
            st.caption("Choose buys that reduce whole-portfolio deviation. This can put the entire contribution into one position.")
        else:
            st.caption("Split the new contribution across the eligible selection. Existing holdings are kept. "
                       "Spread equally gives every eligible row an equal amount; target weights split the money in proportion to their targets, with zero targets receiving nothing. "
                       "This uses one trade per recipient, without a maximum-trade limit. Amounts are rounded to cents while preserving the total.")
    spreading = distribution != "Optimize rebalancing"
    max_trades = int(st.number_input("Maximum trades", min_value=1, max_value=max(1, len(valued)),
                                     value=min(4, max(1, len(valued))), step=1, key=f"rebalance_max_trades_{len(valued)}")) if mode == MODES[2] and not spreading else len(valued)
    st.caption("±0.5 pp gives a 10% target a 9.5–10.5% range. A 5% relative tolerance gives that same range; a zero target has a zero relative range. "
               "Plans allow fractional shares, fully invest new money, and exclude fees, taxes, spreads and lot-size rules. EUR amounts are rounded for display.")
    try:
        problem = prepare_rebalance(valued, tolerance=tolerance, tolerance_type="relative" if relative else "pp")
    except RebalanceError as exc:
        st.info(str(exc))
        return
    fields = [column for column in ("position_id", "id", "name", "ticker", "account", "portfolio", "shares", "current_value_eur", "target_allocation") if column in valued]
    fingerprint = sha256((valued[fields].to_json() + repr((mode, tolerance_type, tolerance, no_new, new_money, max_trades, distribution, None if eligible_ids is None else tuple(sorted(eligible_ids))))).encode()).hexdigest()
    if st.button("Calculate rebalance", type="primary", key="rebalance_calculate"):
        st.session_state.pop("rebalance_result", None)
        try:
            with st.spinner("Calculating your trade plan…"):
                if mode == MODES[0]:
                    plans = [minimum_trades(problem, no_new_positions=no_new)]
                elif mode == MODES[1]:
                    plans = [minimum_new_money(problem, no_new_positions=no_new)]
                elif spreading:
                    plans = [spread_new_money(problem, new_money, eligible_position_ids=eligible_ids,
                                               method="equal" if distribution == "Spread equally" else "target", no_new_positions=no_new)]
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
        st.caption("Choose your settings and calculate. Plans are refreshed when you calculate again; they never place orders or change your saved holdings.")
        return
    plans = cached[1]
    index = len(plans) - 1
    if len(plans) > 1:
        st.caption("Compare how much each additional trade improves the allocation. Choose a lower-trade plan if the extra improvement is small.")
        frontier = pd.DataFrame({
            "Trades": [plan.trade_count for plan in plans],
            "Deviation outside ranges (pp)": [plan.deviation_after for plan in plans],
            "Improvement (pp)": [plan.deviation_before - plan.deviation_after for plan in plans],
            "All positions in range": [plan.within_bands for plan in plans],
        })
        st.dataframe(frontier, hide_index=True, height="content", width="stretch", column_config={
            name: st.column_config.NumberColumn(format="%.3f") for name in ("Deviation outside ranges (pp)", "Improvement (pp)")
        })
        index = st.selectbox("Plan to inspect", list(range(len(plans))), index=index,
                             format_func=lambda i: f"{plans[i].trade_count} trades · {plans[i].deviation_after:.3f} pp outside ranges",
                             key=f"rebalance_plan_{fingerprint}")
    plan = plans[index]
    first, second, third = st.columns(3)
    first.metric("Trades", plan.trade_count, help=f"{plan.buy_count} buys and {plan.sell_count} sells")
    second.metric("Minimum new money" if mode == MODES[1] else "New money", f"€{plan.new_money:,.2f}")
    third.metric("Deviation outside ranges", f"{plan.deviation_after:.3f} pp")
    st.caption("Deviation is the sum of each position’s distance outside its allowed range. Zero means every position is within range; it does not require exact target weights. "
               "Targets are evaluated against the final portfolio value, including new money.")
    if plan.within_bands:
        st.success("Every position is within its target range.")
    elif spreading:
        st.info("The contribution is spread as requested. Some positions remain outside their target ranges.")
    else:
        st.info("This is the closest allocation within the trade limit and restrictions; some positions remain outside their ranges.")
    if plan.deviation_after > plan.deviation_before + 1e-5:
        st.warning("This contribution increases deviation from the whole portfolio’s target ranges." if spreading else
                   "Fully investing this amount under these restrictions increases deviation. Try more trades or a different cash amount.")
    table = plan.table.rename(columns={"name": "Investment", "account": "Account", "portfolio": "Portfolio"}).copy()
    table["Investment"] = table["Investment"].map(display_name)
    config = {"position_id": None, "id": None, "ticker": None,
              **{name: st.column_config.NumberColumn(format="€ %.2f") for name in ("Trade (EUR)", "Current (EUR)", "After (EUR)")},
              **{name: st.column_config.NumberColumn(format="%.2f %%") for name in ("Current %", "After %", "Target %", "Lower %", "Upper %")},
              "Gap (pp)": st.column_config.NumberColumn(format="%+.3f")}
    trades = table.loc[table.Action != "Hold"].copy()
    trades = trades.sort_values("Trade (EUR)", key=lambda amounts: amounts.abs(), ascending=False, kind="stable")
    if trades.empty:
        st.info("No trades are needed.")
    else:
        st.dataframe(trades, hide_index=True, height="content", width="stretch", column_config=config)
        st.caption(f"Buy €{trades['Trade (EUR)'].clip(lower=0).sum():,.2f} · Sell €{-trades['Trade (EUR)'].clip(upper=0).sum():,.2f}. Negative trade amounts are sells.")
    with st.expander("Full allocation after rebalancing"):
        st.dataframe(table.sort_values("After %", ascending=False, kind="stable"), hide_index=True,
                     height="content", width="stretch", column_config=config)
