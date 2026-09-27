"""Streamlit controls and presentation; calculations live in pure modules."""

import argparse
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path

import streamlit as st

from portfolio_app.aggregation import aggregate, aggregate_dimension
from portfolio_app.charts import bar_chart, hierarchy_chart, hierarchy_table, pie_chart, sort_allocation_nodes
from portfolio_app.display_names import display_name
from portfolio_app.etf import effective_exposure_table, expand_etfs, fund_classifications, load_funds, validate_fund_listings
from portfolio_app.etf_ui import render_fund_details, render_snapshot_controls
from portfolio_app.etf_selection import render_etf_selection
from portfolio_app.exposures import normalize_exposures
from portfolio_app.filtering import filter_holdings
from portfolio_app.group_ui import render_group_members, smh_group_control
from portfolio_app.grouping import group_classifications, group_exposures
from portfolio_app.label_ui import render_label_comparison
from portfolio_app.label_presentation import asset_badges, badge_column, taxonomy_colors
from portfolio_app.holdings import DataError, metadata_dimensions
from portfolio_app.position_ui import render_position_editor
from portfolio_app.positions import read_snapshot
from portfolio_app.performance import position_performance
from portfolio_app.performance_ui import render_performance_summary, performance_column_config
from portfolio_app.performance_allocation import performance_exposures, add_performance_column
from portfolio_app.rebalancing import ignore_empty_positions, RebalanceError
from portfolio_app.rebalance_ui import render_rebalancing
from portfolio_app.prices import PriceService, StaticProvider, YahooProvider
from portfolio_app.presentation import allocation_total, apply_style, empty_overview, workspace_header
from portfolio_app.taxonomy import branches, describe, load_classifications, taxonomy_names
from portfolio_app.valuation import portfolio_weights, value_holdings
from portfolio_app.targets import add_target_columns, target_exposures, target_totals
from portfolio_app.target_ui import target_caption, target_column_config
from portfolio_app.allocation import load_allocation, analysis_targets, ignore_empty_by_bucket
from portfolio_app.strategic_ui import render_strategic_overview
from portfolio_app.scoped_ui import render_scoped_rebalancing
from portfolio_app.stock_ui import render_stock_exposure
from portfolio_app.stock_exposure import load_company_identities
from portfolio_app.company_merges import build_plan, load_company_names, load_settings
from portfolio_app.company_merge_ui import render_company_merges


def render_app(data_dir: Path, *, demo: bool = False, demo_dir: Path | None = None, price_service: PriceService | None = None) -> None:
    st.set_page_config(page_title="Portfolio breakdown", layout="wide")
    apply_style()
    if demo_dir is not None:
        workspace = st.sidebar.radio("Portfolio workspace", ["My portfolio", "Demo portfolio"], index=1 if demo else 0, key="active_portfolio")
        demo = workspace == "Demo portfolio"
        if demo:
            data_dir = demo_dir
        context = (str(data_dir.resolve()), demo)
        if st.session_state.get("portfolio_workspace_context") != context:
            for key in list(st.session_state):
                if key.startswith(("position_edit_", "filter_", "label_compare_", "allocation_group_", "rebalance_", "strategic_")) or key in {"position_saved_notice", "ignore_empty_positions"}:
                    del st.session_state[key]
            st.session_state["portfolio_workspace_context"] = context
    workspace_header(demo)
    if demo:
        st.caption("Demo · Synthetic data · Resets on restart")
    try:
        snapshot = read_snapshot(data_dir / "holdings.csv")
        holdings = snapshot.holdings
        allocation = load_allocation(data_dir / 'allocation.yaml', holdings)
        classifications_path = data_dir / "classifications.yaml"
        classifications = load_classifications(classifications_path) if classifications_path.exists() else {}
        funds = load_funds(data_dir / "etfs")
        validate_fund_listings(holdings, funds)
    except DataError as exc:
        st.error(str(exc))
        st.info("Edit holdings.csv and classifications.yaml in the data directory, then rerun the app.")
        return
    def reset_position_filters():
        # A changed position universe must not leave restored rows hidden by a
        # selection made while those rows were unavailable.
        for key in list(st.session_state):
            if key.startswith("filter_"):
                del st.session_state[key]

    refresh = st.sidebar.button("Refresh prices", disabled=demo)
    ignore_empty = st.sidebar.checkbox(
        "Ignore empty positions", key="ignore_empty_positions", on_change=reset_position_filters,
        help="Hide zero-quantity positions from Exposure and rebalancing, redistributing their targets within each bucket. Strategic overview retains planned categories. Saved targets stay unchanged. Switching this option resets exposure filters.",
    )
    analysis_holdings = analysis_targets(holdings, allocation) if allocation else holdings
    analysis_error = None
    if ignore_empty:
        try:
            analysis_holdings = (analysis_targets(ignore_empty_by_bucket(analysis_holdings), allocation)
                                 if allocation else ignore_empty_positions(holdings))
        except RebalanceError as exc:
            analysis_error = str(exc)
    pages = st.tabs(["Overview", *(["Exposure"] if allocation else []), "Rebalance", "Manage positions"],
                    default="Manage positions" if holdings.empty else "Overview", key="main_tabs")
    overview, rebalance, positions = pages[0], pages[-2], pages[-1]
    exposure = pages[1] if allocation else overview
    with positions:
        render_position_editor(data_dir / "holdings.csv", snapshot, funds, demo=demo, embedded=True, allocation=allocation)
    valued = None
    if allocation and price_service is None:
        provider = StaticProvider(data_dir / 'demo_prices.json') if demo else YahooProvider(data_dir / '.cache' / 'yahoo')
        price_service = PriceService(provider, None if demo else data_dir / '.cache' / 'prices.json')
    with exposure:
        if analysis_error:
            st.error(analysis_error)
        elif analysis_holdings.empty:
            empty_overview()
            if not holdings.empty:
                st.info("All positions have zero shares. Disable Ignore empty positions to include them.")
        else:
            if ignore_empty:
                st.caption("Ignoring empty positions · Targets shown here include their equally redistributed allocations. Saved targets are unchanged.")
            valued = render_analysis(data_dir, analysis_holdings, classifications, funds, demo=demo, price_service=price_service, refresh=refresh)
    if allocation:
        # Use the same refreshed observations, with all source positions and no
        # exposure/label filters. Empty strategic buckets remain visible.
        macro_valued = value_holdings(analysis_targets(holdings, allocation), price_service, refresh=refresh and valued is None)
        with overview:
            render_strategic_overview(macro_valued, allocation)
    with overview:
        from portfolio_app.analytics_ui import render_portfolio_analytics
        render_portfolio_analytics(holdings, data_dir, funds, allocation=allocation, demo=demo, price_service=price_service)
    with rebalance:
        if analysis_error:
            st.error(analysis_error)
        if allocation:
            if valued is None:
                valued = value_holdings(analysis_holdings, price_service)
            render_scoped_rebalancing(valued, allocation)
        else:
            render_rebalancing(valued)


def render_analysis(data_dir, holdings, classifications, funds, *, demo, price_service, refresh=False):
    dimensions = metadata_dimensions(holdings)
    with st.expander("Exposure settings"):
        show_tickers = st.checkbox("Show tickers", value=False, key="display_tickers")
        performance_percent = st.radio("Performance display", ["%", "Amount"], horizontal=True, key="display_performance") == "%"
        with st.expander("ETF snapshots"):
            funds = render_snapshot_controls(funds, demo=demo)
        lookthrough, expanded_funds = render_etf_selection(holdings, funds, data_dir)
        representation = 'ETF look-through' if lookthrough else 'Instruments'
        try:
            settings = load_settings(data_dir / 'company-merges.yaml')
            plan = build_plan(holdings, funds, load_company_identities(data_dir / 'company-identities.yaml'), settings, classifications,
                              load_company_names(data_dir / 'company-names.yaml'))
            render_company_merges(plan, settings, data_dir / 'company-merges.yaml')
            if lookthrough:
                holdings, funds, classifications = plan.apply(holdings, funds, classifications)
                active_isins = {fund.isin for fund in expanded_funds}
                expanded_funds = [fund for fund in funds if fund.isin in active_isins]
        except DataError as exc:
            st.error(str(exc))
            return
        classifications = fund_classifications(classifications, funds, holdings)
        names = taxonomy_names(classifications)
        display_group = smh_group_control(holdings, funds)
        with st.expander("Filter positions"):
            metadata = {}
            for dimension in dimensions:
                choices = sorted(holdings[dimension].unique())
                metadata[dimension] = st.multiselect(
                    dimension.replace("_", " ").title(), choices, default=choices,
                    format_func=lambda value: value or "Unspecified", key=f"filter_meta_{dimension}",
                )
            ids = list(holdings["id"].unique())
            labels = {row.id: (f"{display_name(row.name)} ({row.ticker or row.id.upper()})" if show_tickers else display_name(row.name)) for row in holdings.itertuples()}
            counts = Counter(labels.values())
            labels = {asset: f"{label} [{index + 1}]" if counts[label] > 1 else label for index, (asset, label) in enumerate(labels.items())}
            # Streamlit serializes multiselect options by their formatted text.
            # A changed display mode needs a fresh widget while selections stay
            # tied to stable asset IDs, not the previous display strings.
            filter_key = "filter_holdings_" + sha256(repr(labels).encode()).hexdigest()[:16]
            defaults = [asset for asset in st.session_state.get("filter_holdings_selection", ids) if asset in ids]
            st.session_state["filter_holdings_widget"] = filter_key
            # Send the remembered selection as the widget default too, so a
            # remounted browser control cannot publish an unintended empty list.
            selected_ids = st.multiselect("Holdings", ids, default=defaults, format_func=labels.get, key=filter_key,
                                         help="Enable Show tickers to distinguish exchange listings with the same name.")
            st.session_state["filter_holdings_selection"] = selected_ids
            taxonomy_filters = {}
            with st.expander("Taxonomy branch filters"):
                st.caption("Include whole instruments matching any selected branch, before ETF expansion. Filters across taxonomies are combined.")
                for name in names:
                    selected = st.multiselect(
                        f"{name} branches", branches(classifications, ids, name),
                        format_func=lambda path: " > ".join(path), key=f"filter_taxonomy_{name}",
                    )
                    if selected:
                        taxonomy_filters[name] = selected
    if price_service is None:
        try:
            provider = StaticProvider(data_dir / "demo_prices.json") if demo else YahooProvider(data_dir / ".cache" / "yahoo")
            price_service = PriceService(provider, None if demo else data_dir / ".cache" / "prices.json")
        except (OSError, ValueError) as exc:
            st.error(f"Cannot load demo prices: {exc}")
            return
    with st.spinner("Valuing holdings…"):
        valued = value_holdings(holdings, price_service, refresh=refresh)
        cost_currencies = set(valued.loc[(valued.shares > 0) & valued.acquisition_price.notna(), "acquisition_currency"].dropna()) if "acquisition_currency" in valued else set()
        rates = {}
        for currency in cost_currencies - {"", "EUR"}:
            matching = valued.loc[valued.quote_currency == currency, "fx_to_eur"].dropna()
            if not matching.empty:
                rates[currency] = float(matching.iloc[0])
            elif ((valued.get("acquisition_currency", "") == currency) & (valued.quote_currency != currency)).any():
                quote = price_service.fx(currency, refresh=refresh).quote
                if quote is not None and quote.currency == "EUR":
                    rates[currency] = quote.price
        valued = position_performance(valued, rates)
    if price_service.cache_warning:
        st.warning(price_service.cache_warning)
    selected = filter_holdings(
        valued, classifications, metadata=metadata, asset_ids=selected_ids, taxonomy_branches=taxonomy_filters,
    )
    selected["portfolio_weight"] = portfolio_weights(selected["current_value_eur"])
    total = valued["current_value_eur"].sum()
    selected_total = selected["current_value_eur"].sum()
    missing = int(selected["current_value_eur"].isna().sum())
    all_missing = int(valued["current_value_eur"].isna().sum())
    first, second, third = st.columns(3)
    first.metric("Portfolio value", f"€{total:,.2f}")
    second.metric("Selected value", f"€{selected_total:,.2f}")
    third.metric("Awaiting a price", str(missing))
    with st.expander("Performance"):
        render_performance_summary(valued)
    if missing:
        st.warning("Missing prices: allocation excludes unvalued positions. See Show price details for affected holdings.")
    if selected.empty:
        st.info("No holdings match the selected filters.")
        return valued
    exposures = normalize_exposures(selected)
    if representation == "ETF look-through":
        try:
            exposures = expand_etfs(exposures, expanded_funds, holdings)
        except DataError as exc:
            st.error(str(exc))
            return valued
    exposures["asset_name"] = exposures["asset_name"].map(display_name)
    effective_exposures = exposures
    saved_classifications = classifications
    targets = None
    if display_group is not None:
        try:
            exposures = group_exposures(exposures, display_group)
            classifications = group_classifications(classifications, display_group)
        except DataError as exc:
            st.error(str(exc))
            return valued
    if "target_allocation" in valued and valued["target_allocation"].notna().any():
        try:
            targets = target_exposures(selected, expanded_funds, lookthrough=lookthrough, group=display_group, holdings=holdings)
            for measure in (targets.known, targets.missing):
                measure["asset_name"] = measure["asset_name"].map(display_name)
        except DataError as exc:
            st.error(str(exc))
            return valued
    try:
        performance = performance_exposures(selected, expanded_funds, lookthrough=lookthrough, group=display_group, holdings=holdings)
    except DataError as exc:
        st.error(str(exc))
        return valued
    for measure in performance.measures():
        measure["asset_name"] = measure["asset_name"].map(display_name)
    if lookthrough and any(group.enabled and group.basis == 'Estimated name match' for group in plan.groups):
        st.caption('* Estimated company match · Review or undo in Exposure settings → Company merges')
    with st.expander("Chart settings"):
        options = [("holding", "Holding"), *[(f"metadata:{name}", name.replace("_", " ").title()) for name in dimensions],
                   *[(f"taxonomy:{name}", f"Taxonomy: {name}") for name in names]]
        label_option = [("selected_labels", "Selected labels")]
        options = label_option + options if "labels" in names else options + label_option
        view = st.selectbox("Group by", [key for key, _ in options], format_func=dict(options).get)
        root = ()
        depth = None
        include_holdings = False
        if view.startswith("taxonomy:"):
            taxonomy = view.removeprefix("taxonomy:")
            if taxonomy == "ai":
                st.caption("AI themes describe business roles, not the proportion of company revenue from AI.")
            asset_ids = exposures["asset_id"].tolist()
            roots = [(), *branches(classifications, asset_ids, taxonomy)]
            root = st.selectbox("Hierarchy root", roots, format_func=lambda path: " > ".join(path) if path else "Entire taxonomy", key=f"root_{taxonomy}")
            max_depth = max((len(path) - len(root) for path in roots if path[:len(root)] == root), default=0)
            depth = st.selectbox("View depth", [None, *range(1, max_depth + 1)], format_func=lambda value: "Full tree" if value is None else f"{value} level(s) below root", key=f"depth_{taxonomy}_{root}")
            include_holdings = st.checkbox("Show holdings beneath labels")
        if view != "selected_labels":
            chart_type = st.selectbox("Chart", ["Sunburst", "Treemap", "Bar", "Pie"])
    chart_exposures = exposures.copy()
    if show_tickers:
        # Prefer the direct listing's ticker for every row of the same asset,
        # including ETF-derived rows, so their holding leaves remain combined.
        tickers = dict(zip(exposures["asset_id"], exposures["ticker"]))
        tickers.update(dict(zip(holdings["id"], holdings["ticker"])))
        chart_exposures["asset_name"] = [f"{row.asset_name} ({tickers[row.asset_id]})" if tickers[row.asset_id] else row.asset_name for row in exposures.itertuples()]
    if view == "selected_labels":
        render_label_comparison(chart_exposures, classifications, targets=targets, portfolio_value=total, valuation_complete=all_missing == 0,
                                performance=performance, performance_percent=performance_percent)
    else:
        if view.startswith("taxonomy:"):
            nodes = aggregate(chart_exposures, classifications, taxonomy=taxonomy, root=root, depth=depth, include_holdings=include_holdings and chart_type not in {"Bar", "Pie"})
        else:
            nodes = aggregate_dimension(exposures, "holding" if view == "holding" else view.removeprefix("metadata:"), show_tickers=show_tickers)
            nodes.loc[nodes["parent_id"] == "", "label"] = "Selected holdings"
        nodes = sort_allocation_nodes(nodes)
        st.subheader("Allocation")
        if nodes.empty or (selected_total <= 0 and targets is None):
            st.info("No positive valued allocation is available for this selection.")
        else:
            root_value = nodes.iloc[0]["value"]
            if root_value <= 0 and targets is None:
                st.info("The selected hierarchy root has no positive valued allocation.")
            else:
                st.caption("Allocation is relative to the selected category.")
                if root_value > 0:
                    figure = bar_chart(nodes) if chart_type == "Bar" else pie_chart(nodes) if chart_type == "Pie" else hierarchy_chart(nodes, chart_type)
                    st.plotly_chart(figure, width="stretch", height=figure.layout.height, theme="streamlit", config={"responsive": True, "displaylogo": False})
                else:
                    st.info("This selection has no current allocation. Its targets are shown below.")
                show_paths = False
                if view.startswith("taxonomy:"):
                    show_paths = st.checkbox("Show classification paths", help="The breadcrumb locating a category in the taxonomy, for example Technology › Semiconductors. This is not a file path.")
                    st.caption("Parent rows include their descendants.")
                total_label = f"{nodes.iloc[0]['label']} — total" if view.startswith("taxonomy:") else "Selected holdings — total"
                allocation_total(total_label, root_value)
                allocation = hierarchy_table(nodes, show_paths=show_paths)
                if targets is not None:
                    measures = []
                    for measure in (targets.known, targets.missing):
                        if view.startswith("taxonomy:"):
                            measures.append(aggregate(measure, classifications, taxonomy=taxonomy, root=root, depth=depth,
                                                      include_holdings=include_holdings and chart_type not in {"Bar", "Pie"}))
                        else:
                            measures.append(aggregate_dimension(measure, "holding" if view == "holding" else view.removeprefix("metadata:")))
                    totals = target_totals(*measures, key="node_id")
                    allocation = add_target_columns(allocation, nodes.loc[nodes["parent_id"] != "", "node_id"].tolist(), totals,
                                                    portfolio_value=total, valuation_complete=all_missing == 0)
                    target_caption(valuation_complete=all_missing == 0)
                performance_measures = []
                for measure in performance.measures():
                    if view.startswith("taxonomy:"):
                        performance_measures.append(aggregate(measure, classifications, taxonomy=taxonomy, root=root, depth=depth,
                                                              include_holdings=include_holdings and chart_type not in {"Bar", "Pie"}))
                    else:
                        performance_measures.append(aggregate_dimension(measure, "holding" if view == "holding" else view.removeprefix("metadata:")))
                allocation = add_performance_column(allocation, nodes.loc[nodes["parent_id"] != "", "node_id"].tolist(),
                                                    performance_measures, key="node_id", percent=performance_percent)
                label_config = {}
                if view == "holding" and names:
                    label_set = "labels" if "labels" in names else "sector" if "sector" in names else names[0]
                    allocation["Labels"] = [asset_badges(classifications, json.loads(node.node_id)[1][0], label_set)
                                            for node in nodes.loc[nodes["parent_id"] != ""].itertuples()]
                    label_config["Labels"] = badge_column("Labels", taxonomy_colors(classifications, label_set))
                st.dataframe(allocation, hide_index=True, width="stretch", height="content", column_config={
                    "Category": "Investment" if view == "holding" else "Category",
                    "Classification path": st.column_config.TextColumn(help="Full breadcrumb within the selected taxonomy."),
                    "EUR value": st.column_config.NumberColumn(format="€ %.2f"),
                    "Allocation %": st.column_config.NumberColumn(format="%.2f %%"),
                } | label_config | target_column_config() | performance_column_config(percent=performance_percent))
    classifications = saved_classifications
    if display_group is not None:
        render_group_members(selected, display_group)
    if representation == "ETF look-through":
        st.subheader("Effective exposure")
        st.caption("Direct + ETF exposure · % of selected portfolio")
        st.dataframe(effective_exposure_table(effective_exposures), hide_index=True, width="stretch", height="content", column_config={
            column: st.column_config.NumberColumn(format="€ %.2f")
            for column in ("Direct (EUR)", "ETF-derived (EUR)", "Total (EUR)")
        } | {"Ticker": "Ticker" if show_tickers else None, "Allocation %": st.column_config.NumberColumn(format="%.2f %%")})
    displayed_classifications = ["labels"] if view == "selected_labels" and "labels" in names else names
    render_fund_details(funds, selected, holdings=holdings, classifications=classifications, show_tickers=show_tickers,
                        classification_names=displayed_classifications)
    st.subheader("Holdings")
    if representation == "ETF look-through":
        st.caption("Source positions · Before ETF look-through")
    table = selected.sort_values("portfolio_weight", ascending=False, kind="stable", na_position="last").copy()
    if 'within_bucket_target' in table:
        bucket_values = valued.groupby('bucket_id').current_value_eur.agg(lambda values: values.sum() if values.notna().all() else float('nan'))
        table['Current bucket %'] = 100 * table.current_value_eur / table.bucket_id.map(bucket_values).replace(0, float('nan'))
        table['Target bucket %'] = table.within_bucket_target * 100
    table["name"] = table["name"].map(display_name)
    for name in names:
        table[f"classification:{name}"] = table["id"].map(lambda asset_id: describe(classifications, asset_id, name))
    if "labels" in displayed_classifications:
        table["classification:labels"] = table["id"].map(lambda asset_id: asset_badges(classifications, asset_id, "labels"))
    table["portfolio_weight"] *= 100
    columns = ["id", "name", "shares", "ticker", "quote_currency", "current_price", "fx_to_eur", "current_value_eur", "portfolio_weight", *dimensions]
    if "target_allocation" in table and table["target_allocation"].notna().any():
        table["target_allocation"] *= 100
        columns.insert(columns.index("portfolio_weight") + 1, "target_allocation")
    if table["acquisition_price"].notna().any():
        table["Performance"] = table["return_pct" if performance_percent else "unrealized_gain"]
        performance_columns = ["Performance"]
        if "acquisition_currency" in table:
            performance_columns.append("acquisition_currency")
        columns[columns.index("shares"):columns.index("shares")] = performance_columns
        columns += ["acquisition_price", "cost_basis", "performance_note"]
    columns += [f"classification:{name}" for name in displayed_classifications]
    if 'within_bucket_target' in table:
        columns += ['Current bucket %', 'Target bucket %']
    if 'holdings_confirmed_on' in table:
        columns += ['holdings_confirmed_on']
    if 'quantity_unit' in table and table.quantity_unit.ne('').any():
        columns += ['quantity_unit']
    if st.checkbox("Show price details", help="Quote timestamps, FX status and valuation notes"):
        columns += ["price_status", "price_observed_at", "price_age_hours", "fx_status", "fx_observed_at", "fx_age_hours", "valuation_note"]
    st.dataframe(table[columns], hide_index=True, width="stretch", height="content", column_config={
        "id": None, "name": "Investment", "ticker": "Ticker" if show_tickers else None, "shares": st.column_config.NumberColumn('Quantity', format='%.10f'),
        'Current bucket %': st.column_config.NumberColumn('Current (% of bucket)', format='%.2f %%'),
        'Target bucket %': st.column_config.NumberColumn('Target (% of bucket)', format='%.2f %%'),
        'holdings_confirmed_on': 'Holdings last confirmed', 'quantity_unit': 'Quantity unit',
        "quote_currency": "Currency", "fx_to_eur": None,
        "current_value_eur": st.column_config.NumberColumn("Current value (EUR)", format="€ %.2f"),
        "portfolio_weight": st.column_config.NumberColumn("Selected weight (%)", format="%.2f %%"),
        "target_allocation": st.column_config.NumberColumn("Target allocation (% of whole portfolio)", format="%.2f %%"),
        "current_price": st.column_config.NumberColumn("Price (quote currency)", format="%.4f"),
        "acquisition_price": st.column_config.NumberColumn("Average buy-in per unit", format="%.6f"),
        "acquisition_currency": st.column_config.TextColumn("Buy-in currency"),
        "cost_basis": st.column_config.NumberColumn("Cost basis (buy-in currency)", format="%.2f"),
        "performance_note": st.column_config.TextColumn("Performance details"),
    } | {f"classification:{name}": (badge_column("Labels", taxonomy_colors(classifications, name)) if name == "labels" else
                                    "AI theme" if name == "ai" else name.replace("_", " ").title()) for name in names} | performance_column_config(percent=performance_percent, grouped=False))
    st.caption("Latest available daily close · Prices may be delayed")
    render_stock_exposure(valued, expanded_funds, data_dir)
    return valued


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path.cwd() / "data" / "portfolio")
    parser.add_argument("--demo-dir", type=Path)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    render_app(args.data_dir, demo=args.demo, demo_dir=args.demo_dir)
