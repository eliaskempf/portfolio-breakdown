"""A direct comparison of selected labels, alongside the advanced tree views."""

from hashlib import sha256

import pandas as pd
import streamlit as st

from portfolio_app.charts import bar_chart, hierarchy_chart, pie_chart, sort_allocation_nodes
from portfolio_app.label_comparison import (
    available_labels, compare_labels, comparison_assets, comparison_tree, immediate_children, label_memberships,
)
from portfolio_app.presentation import allocation_total
from portfolio_app.label_presentation import asset_badges, badge_column, color_label_chart, taxonomy_colors
from portfolio_app.taxonomy import Classifications, UNCLASSIFIED, taxonomy_names
from portfolio_app.targets import TargetExposures, add_target_columns, target_totals
from portfolio_app.target_ui import target_caption, target_column_config


def render_label_comparison(exposures: pd.DataFrame, classifications: Classifications, *,
                            targets: TargetExposures | None = None, portfolio_value: float = 0., valuation_complete: bool = True) -> None:
    st.subheader("Allocation across labels")
    names = taxonomy_names(classifications)
    if not names:
        st.info("Add classification labels to your investments to compare their allocation.")
        return
    with st.sidebar:
        taxonomy = "labels" if "labels" in names else st.selectbox("Label set", names, key="label_compare_set")
        choices = available_labels(classifications, taxonomy)
        by_key = {label.key: label for label in choices}
        defaults = [label.key for label in choices if len(label.path) == 1 and label.path != UNCLASSIFIED]
        signature = sha256(repr(list(by_key)).encode()).hexdigest()[:16]
        selected = st.multiselect("Labels to compare", list(by_key), default=defaults,
                                  format_func=lambda key: by_key[key].title,
                                  key=f"label_compare_selection_{signature}",
                                  help="Select any labels. Each includes assets assigned to it or to a category beneath it.")
    labels = [by_key[key] for key in selected]
    colors = taxonomy_colors(classifications, taxonomy)
    if not labels:
        st.info("Choose one or more labels to compare their combined asset allocations.")
        return
    memberships = label_memberships(exposures, classifications, labels)
    has_overlap = any(row.value > 0 and len(memberships[row.asset_id]) > 1 for row in exposures.itertuples())
    if targets is not None:
        for measure in (targets.known, targets.missing):
            target_memberships = label_memberships(measure, classifications, labels)
            has_overlap |= any(row.value > 0 and len(target_memberships[row.asset_id]) > 1 for row in measure.itertuples())
    policy = "split"  # With disjoint labels, both policies give the same result.
    with st.sidebar:
        if has_overlap:
            choice = st.radio("Assets matching multiple labels", ["Split equally", "Count in each label"], index=None,
                              key="label_compare_overlap",
                              help="Splitting gives a 100% allocation. Counting in each label shows overlapping exposures whose percentages can exceed 100%.")
            if choice is None:
                st.info("Choose how to count assets that match multiple selected labels.")
                return
            policy = "split" if choice == "Split equally" else "overlap"
        chart_types = ["Bar"] if has_overlap and policy == "overlap" else ["Bar", "Pie", "Sunburst", "Treemap"]
        chart_type = st.selectbox("Chart", chart_types, index=chart_types.index("Sunburst") if "Sunburst" in chart_types else 0,
                                  key=f"label_compare_chart_{policy}")
    comparison = compare_labels(exposures, classifications, labels, overlap=policy)
    target_comparisons = None
    if targets is not None:
        target_comparisons = [compare_labels(measure, classifications, labels, overlap=policy)
                              for measure in (targets.known, targets.missing)]
        target_caption(valuation_complete=valuation_complete)
    if not comparison.matched_value:
        st.info("No positive valued assets match these labels in the current portfolio selection.")
    else:
        coverage = comparison.matched_value / comparison.portfolio_value
        st.caption(f"These labels cover €{comparison.matched_value:,.2f} ({coverage:.1%}) of the filtered portfolio. "
                   f"€{comparison.unmatched_value:,.2f} falls outside them. Unvalued assets are excluded.")
        if has_overlap:
            st.caption(f"€{comparison.overlapping_value:,.2f} matches multiple selected labels. " +
                       ("Its value is split equally between those labels." if policy == "split" else
                        "Each matching label includes its full value. Label percentages can exceed 100% in total; bars show this overlap."))
        else:
            st.caption("Each selected label aggregates all matching assets, including their ETF exposure in look-through mode.")
        roots = sorted({path[:length] for path in comparison.allocations["path"] for length in range(1, len(path) + 1)})
        detail_key = "label_compare_detail_" + sha256(repr(roots).encode()).hexdigest()[:16]
        root = st.selectbox("Detail view", [(), *roots],
                            format_func=lambda path: "All selected labels" if not path else " › ".join((by_key[path[0]].title, *path[1:])),
                            key=detail_key,
                            help="Choose a label or subcategory to update both the chart and its individual-assets table.")
        if root:
            st.button("Back to overview", on_click=lambda: st.session_state.update({detail_key: ()}))
        assets_only = (st.checkbox("Show individual assets directly", key="label_compare_assets_only")
                       if root or chart_type in {"Sunburst", "Treemap"} else False)
        tree = sort_allocation_nodes(comparison_tree(comparison, labels, root=root, assets_only=assets_only))
        if root:
            nodes = immediate_children(tree)
        else:
            nodes = sort_allocation_nodes(comparison.nodes)
        if chart_type in {"Sunburst", "Treemap"}:
            # Full descendants are present even while collapsed. Plotly's native
            # branch clicks can zoom all the way from labels to individual assets.
            figure = hierarchy_chart(tree, chart_type)
            figure.update_traces(maxdepth=3,
                                 hovertemplate="%{label}<br>€%{value:,.2f}<br>%{customdata[0]:.2%} of " +
                                 ("detail total" if root else "selected labels") + "<extra></extra>")
            st.caption("Click a category to open its subcategories and assets. Click the centre (sunburst) or breadcrumb (treemap) to go back. "
                       "Chart zoom keeps the table unchanged; use Detail view to inspect a branch’s asset table.")
        else:
            figure = bar_chart(nodes) if chart_type == "Bar" else pie_chart(nodes)
        color_label_chart(figure, tree if chart_type in {"Sunburst", "Treemap"} else nodes, colors,
                          detail_color=colors[by_key[root[0]].title] if root else None)
        if float(tree.iloc[0]["value"]) > 0:
            st.plotly_chart(figure, width="stretch", height=figure.layout.height, theme=None,
                            config={"responsive": True, "displaylogo": False})
        else:
            st.info("This branch has no current allocation. Its targets are shown below.")
        if root:
            allocation_total(" › ".join((by_key[root[0]].title, *root[1:])), float(tree.iloc[0]["value"]))
            st.caption("Asset percentages use this detail’s assigned value. Assets with several paths within a label share its value equally across those paths.")
            assets = comparison_assets(comparison, root, include_ids=True)
            if target_comparisons is not None:
                target_details = [item.allocations.loc[item.allocations["path"].map(lambda path: path[:len(root)] == root)]
                                  for item in target_comparisons]
                # Include targets for assets awaiting a quote in this branch.
                identities = target_details[0][["asset_id", "asset_name"]].drop_duplicates("asset_id").rename(columns={"asset_name": "Investment"})
                assets = assets.merge(identities, on="asset_id", how="outer", suffixes=("", "_target"))
                assets["Investment"] = assets["Investment"].fillna(assets.pop("Investment_target"))
                assets = add_target_columns(assets, assets["asset_id"].tolist(), target_totals(*target_details, key="asset_id"),
                                            portfolio_value=portfolio_value, valuation_complete=valuation_complete)
                assets = assets.sort_values(["EUR value", "Investment"], ascending=[False, True],
                                             na_position="last", kind="stable", ignore_index=True)
            assets["Labels"] = assets.pop("asset_id").map(lambda asset: asset_badges(classifications, asset, taxonomy))
            st.dataframe(assets, hide_index=True, height="content", width="stretch", column_config={
                "Labels": badge_column("Labels", colors),
                "EUR value": st.column_config.NumberColumn(format="€ %.2f"),
                "Allocation %": st.column_config.NumberColumn("Within this detail (%)", format="%.2f %%"),
            } | target_column_config())
            return
        allocation_total("Assets matching the selected labels — counted once", comparison.matched_value)
    table = comparison.table.copy()
    if target_comparisons is not None:
        table = add_target_columns(table, table["Label"].tolist(),
                                   target_totals(*(item.table for item in target_comparisons), key="Label", value="EUR value"),
                                   portfolio_value=portfolio_value, valuation_complete=valuation_complete)
    table["Label"] = table["Label"].map(lambda label: [label])
    st.dataframe(table, hide_index=True, height="content", width="stretch", column_config={
        "Label": badge_column("Label", colors),
        "EUR value": st.column_config.NumberColumn(format="€ %.2f"),
        "Selected labels %": st.column_config.NumberColumn("Within selected labels (%)", format="%.2f %%",
                                                           help="Share of the unique value matching any selected label."),
        "Portfolio %": st.column_config.NumberColumn("Of filtered portfolio (%)", format="%.2f %%"),
        "Assets": st.column_config.NumberColumn(help="Unique matching assets; direct and ETF-derived exposure to the same asset count once."),
    } | target_column_config())
