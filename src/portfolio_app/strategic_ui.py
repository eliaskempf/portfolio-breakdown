"""Strategic allocation overview and bucket navigation."""

from hashlib import sha256

import streamlit as st

from portfolio_app.charts import SUNBURST_PALETTE, hierarchy_chart, sort_allocation_nodes
from portfolio_app.chart_navigation import sync_chart_category
from portfolio_app.display_names import display_name
from portfolio_app.strategic import bucket_paths, bucket_positions, strategic_summary, strategic_tree


def render_strategic_overview(valued, config):
    paths = bucket_paths(config)
    names = {b.id: b.name for b in config.buckets} | {"unassigned": "Unassigned"}
    options = ["", *[b.id for b in config.buckets]]
    if valued.bucket_id.eq("").any():
        options.append("unassigned")
    signature = sha256(repr((config, list(valued.position_id))).encode()).hexdigest()[:16]
    key = f"strategic_category_{signature}"
    st.subheader("Strategic allocation")
    navigation, back = st.columns([5, 1], vertical_alignment="bottom")
    with navigation:
        bucket = st.selectbox("Category", options, key=key,
                              format_func=lambda value: "Portfolio" if not value else " › ".join(names[item] for item in paths[value]),
                              help="Select a category here or click a chart segment to update the chart and tables.")
    with back:
        st.button("Back", disabled=not bucket, width="stretch",
                  on_click=lambda: st.session_state.update({key: paths[bucket][-2] if len(paths[bucket]) > 1 else ""}))
    selected = bucket_positions(valued, config, bucket)
    subtotal = float(selected.current_value_eur.sum())
    missing = int(selected.current_value_eur.isna().sum())
    whole = float(valued.current_value_eur.sum())
    first, second, third = st.columns(3)
    first.metric("Priced value" if missing else "Current value", f"€{subtotal:,.2f}")
    second.metric("Portfolio share", f"{100 * subtotal / whole:.1f}%" if not missing and valued.current_value_eur.notna().all() and whole else "—")
    third.metric("Positions", str(len(selected)))
    if missing:
        st.warning(f"{missing} position(s) missing prices. The chart shows priced value only; full allocation percentages are unavailable.")
    chart, summary = st.columns([3, 2], gap="large")
    with chart:
        tree = sort_allocation_nodes(strategic_tree(valued, config, bucket))
        if not tree.empty and subtotal > 0:
            figure = hierarchy_chart(tree, "Sunburst")
            colors = {b.id: SUNBURST_PALETTE[i % len(SUNBURST_PALETTE)]
                      for i, b in enumerate(config.children())} | {"unassigned": "#7b8493"}
            figure.update_traces(
                maxdepth=3,
                marker_colors=[colors.get(row.path[0], "rgba(127,127,127,0.12)") if row.path else "rgba(127,127,127,0.12)" for row in tree.itertuples()],
                hovertemplate="%{label}<br>€%{value:,.2f}<br>%{customdata[0]:.2%} of " + ("priced value" if missing else "category") + "<extra></extra>",
            )
            figure.update_layout(height=510, margin=dict(t=12, b=12, l=12, r=12))
            chart_key = f"strategic_chart_{signature}_{sha256(bucket.encode()).hexdigest()[:12]}"
            st.plotly_chart(figure, width="stretch", height=510, theme="streamlit", key=chart_key,
                            config={"displaylogo": False, "displayModeBar": False})
            categories = {row.node_id: row.path[-1] if row.path else "" for row in tree.itertuples() if row.kind == "category"}
            categories[""] = paths[bucket][-2] if len(paths[bucket]) > 1 else ""
            sync_chart_category(chart_key, categories, key)
        else:
            st.info("No priced holdings in this category." if missing else "No current holdings in this category.")
    with summary:
        st.markdown("**Allocation**")
        st.dataframe(strategic_summary(valued, config, bucket), hide_index=True, width="stretch", height="content",
                     column_config={
                         "Value (EUR)": st.column_config.NumberColumn(format="€ %.2f"),
                         "Current (%)": st.column_config.NumberColumn(format="%.2f %%", help="Share of the selected category's current value."),
                         "Target (%)": st.column_config.NumberColumn(format="%.2f %%", help="Target within the selected category. Blank means unset."),
                         "Gap (pp)": st.column_config.NumberColumn(format="%+.2f", help="Current minus target, in percentage points."),
                     })
    st.subheader("Positions")
    table = selected.copy()
    table["Investment"] = table["name"].map(display_name)
    table["Category"] = table.bucket_id.map(names).fillna("Unassigned")
    table["Category (%)"] = table.current_value_eur * 100 / subtotal if not missing and subtotal else float("nan")
    table = table.sort_values("current_value_eur", ascending=False, na_position="last")
    columns = ["Investment", "Category", "account", "current_value_eur", "Category (%)"]
    st.dataframe(table[columns], hide_index=True, width="stretch", height="content", column_config={
        "account": st.column_config.TextColumn("Account") if table.account.ne("").any() else None,
        "current_value_eur": st.column_config.NumberColumn("Value (EUR)", format="€ %.2f"),
        "Category (%)": st.column_config.NumberColumn(format="%.2f %%"),
    })
