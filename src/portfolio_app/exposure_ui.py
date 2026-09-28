"""Streamlit controls and presentation; calculations live in pure modules."""

import json
from collections import Counter
from hashlib import sha256

import streamlit as st

from portfolio_app.aggregation import aggregate, aggregate_dimension
from portfolio_app.charts import bar_chart, hierarchy_chart, hierarchy_table, pie_chart, sort_allocation_nodes
from portfolio_app.display_names import display_name, instrument_name, named_holdings
from portfolio_app.etf import matching_fund, expand_etfs, fund_classifications
from portfolio_app.etf_refresh_ui import render_refresh_controls, render_refresh_status
from portfolio_app.etf_selection import render_etf_choices, render_etf_toggle
from portfolio_app.exposure_assets_ui import render_assets
from portfolio_app.exposure_tables import complete_exposures
from portfolio_app.strategic import bucket_positions, category_labels
from portfolio_app.chart_navigation import sync_chart_category
from portfolio_app.exposures import normalize_exposures
from portfolio_app.filtering import filter_holdings
from portfolio_app.group_ui import render_group_members, smh_group_control
from portfolio_app.grouping import group_classifications, group_exposures
from portfolio_app.label_ui import render_label_comparison
from portfolio_app.label_presentation import asset_badges, badge_column, taxonomy_colors
from portfolio_app.holdings import DataError, metadata_dimensions
from portfolio_app.portfolio import prepare_portfolio
from portfolio_app.performance_ui import performance_column_config
from portfolio_app.performance_allocation import performance_exposures, add_performance_column
from portfolio_app.prices import PriceService, StaticProvider, YahooProvider
from portfolio_app.presentation import allocation_total, value_metric
from portfolio_app.performance import summarize_performance
from portfolio_app.taxonomy import branches, describe, taxonomy_names
from portfolio_app.valuation import portfolio_weights
from portfolio_app.targets import add_target_columns, target_exposures, target_totals
from portfolio_app.target_ui import target_caption, target_column_config
from portfolio_app.stock_ui import render_stock_exposure
from portfolio_app.stock_exposure import load_company_identities
from portfolio_app.company_merges import build_plan, load_company_names, load_settings
from portfolio_app.company_merge_ui import render_company_merges
from portfolio_app.input_cache import cached_input
from portfolio_app.geography import resolve_geography
from portfolio_app.geography_ui import render_geography


def render_analysis(data_dir, holdings, classifications, funds, *, demo, price_service, refresh=False, source_valued=None, performance_percent=False, allocation=None, etf_revision=0, on_toggle_gain=None):
    dimensions = metadata_dimensions(holdings)
    with st.container(key='exposure_toolbar'):
        scope_col, search_col, breakdown_col, filter_col, settings_col = st.columns([2, 2, 1.4, 1, 1.4], vertical_alignment='bottom')
        with scope_col:
            scope_labels = {'': 'Entire portfolio', **(category_labels(allocation) if allocation else {})}
            if allocation:
                if holdings.bucket_id.eq('').any():
                    scope_labels['unassigned'] = 'Unassigned'
            else:
                scope_labels.update({name: name for name in sorted(set(holdings.portfolio) - {''})})
            if st.session_state.get('exposure_scope', '') not in scope_labels:
                st.session_state['exposure_scope'] = ''
            scope = st.selectbox('Source scope', list(scope_labels), format_func=scope_labels.get, key='exposure_scope')
        with search_col:
            query = st.text_input('Search exposure', placeholder='Investment or ticker…', key='exposure_search')
        with breakdown_col:
            lookthrough = render_etf_toggle(data_dir)
        with filter_col:
            filters = st.popover('Filters', width='stretch')
        with settings_col:
            settings_panel = st.popover('Data & settings', width='stretch')
    # Place results before the settings content in the page's document order.
    summary_area = st.container(key='exposure_summary')
    results_area = st.container(key='exposure_results')
    with settings_panel:
        show_tickers = st.checkbox('Show tickers', value=False, key='display_tickers')
        show_chart = st.checkbox('Show largest exposures chart', key='exposure_show_chart')
        with st.expander('Individual ETFs'):
            expanded_funds = render_etf_choices(holdings, funds, data_dir, enabled=lookthrough)
        refresh_panel = st.expander('ETF refresh & snapshots')
        with refresh_panel:
            render_refresh_controls(data_dir, holdings, funds, demo=demo)
        display_group = smh_group_control(holdings, funds)
        try:
            settings = load_settings(data_dir / 'company-merges.yaml')
            identities = load_company_identities(data_dir / 'company-identities.yaml')
            company_names = load_company_names(data_dir / 'company-names.yaml')
            signature = sha256((str(data_dir.resolve()) + holdings.to_json()
                + repr((settings, identities, company_names, classifications))
                + ''.join(repr((f.isin, f.name, f.as_of, f.equity_fund, f.proxy_source)) + f.constituents.to_json()
                          for f in funds)).encode()).hexdigest()
            plan = cached_input('company_plan', signature,
                lambda: build_plan(holdings, funds, identities, settings, classifications, company_names))
            render_company_merges(plan, settings, data_dir / 'company-merges.yaml')
            if lookthrough:
                holdings, funds, classifications = plan.apply(holdings, funds, classifications)
                active_isins = {fund.isin for fund in expanded_funds}
                expanded_funds = [fund for fund in funds if fund.isin in active_isins]
        except DataError as exc:
            st.error(str(exc))
            return
    # Resolve from manual classifications before adding provider fallbacks, so
    # provider metadata is never mistaken for an authoritative manual override.
    geography = cached_input('geography', (signature, lookthrough),
                             lambda: resolve_geography(holdings, funds, classifications))
    classifications = fund_classifications(classifications, funds, holdings)
    names = taxonomy_names(classifications)
    with filters:
        with st.container():
            metadata = {}
            for dimension in dimensions:
                choices = sorted(holdings[dimension].unique())
                metadata[dimension] = st.multiselect(
                    dimension.replace("_", " ").title(), choices, default=choices,
                    format_func=lambda value: value or "Unspecified", key=f"filter_meta_{dimension}",
                )
            ids = list(holdings["id"].unique())
            labels = {row.id: (f"{instrument_name(row)} ({row.ticker or row.id.upper()})" if show_tickers else instrument_name(row)) for row in holdings.itertuples()}
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
    with filters:
        st.caption('Source filters apply before ETF expansion. Search filters the resulting assets without changing their percentage denominator.')
        def clear_filters():
            for key in list(st.session_state):
                if key.startswith('filter_'):
                    del st.session_state[key]
            st.session_state['exposure_scope'] = ''
            st.session_state['exposure_search'] = ''
            st.session_state[filter_key] = ids
            st.session_state['filter_holdings_selection'] = ids
            for dimension in dimensions:
                st.session_state[f'filter_meta_{dimension}'] = sorted(holdings[dimension].unique())
            for name in names:
                st.session_state[f'filter_taxonomy_{name}'] = []
        st.button('Clear filters', on_click=clear_filters)
    if price_service is None:
        try:
            provider = StaticProvider(data_dir / "demo_prices.json") if demo else YahooProvider(data_dir / ".cache" / "yahoo")
            price_service = PriceService(provider, None if demo else data_dir / ".cache" / "prices.json")
        except (OSError, ValueError) as exc:
            st.error(f"Cannot load demo prices: {exc}")
            return
    valued = prepare_portfolio(holdings, price_service, refresh=refresh) if source_valued is None else source_valued.copy()
    for column in ('analysis_asset_id', 'analysis_asset_name'):
        if column in holdings:
            valued[column] = valued.position_id.map(holdings.set_index('position_id')[column])
    valued = named_holdings(valued)
    if price_service.cache_warning:
        st.warning(price_service.cache_warning)
    selected = filter_holdings(
        valued, classifications, metadata=metadata, asset_ids=selected_ids, taxonomy_branches=taxonomy_filters,
    )
    if scope:
        selected = bucket_positions(selected, allocation, scope) if allocation else selected.loc[selected.portfolio.eq(scope)].copy()
    selected["portfolio_weight"] = portfolio_weights(selected["current_value_eur"])
    total = valued["current_value_eur"].sum()
    selected_total = selected["current_value_eur"].sum()
    missing = int(selected["current_value_eur"].isna().sum())
    all_missing = int(valued["current_value_eur"].isna().sum())
    with summary_area:
        first, second = st.columns([2, 1])
        with first:
            value_metric(selected_total, summarize_performance(selected), missing=missing,
                         percent=performance_percent, key='exposure_value', on_toggle_gain=on_toggle_gain)
        second.metric('Portfolio share', f'{100 * selected_total / total:.1f}%' if not missing and not all_missing and total else '—')
    with refresh_panel:
        held_funds = [fund for fund in expanded_funds if any(row.get('shares', 0) > 0 and matching_fund(row, [fund]) for row in selected.to_dict('records'))]
        render_refresh_status(data_dir, held_funds, etf_revision, demo=demo)
        if lookthrough:
            intact = sum(row.get('shares', 0) > 0 and row.get('instrument_type') == 'etf'
                         and matching_fund(row, expanded_funds) is None for row in selected.to_dict('records'))
            if intact:
                st.caption(f'{intact} ETF(s) remain whole instruments · Breakdown unavailable or disabled in Individual ETFs')
    if missing:
        results_area.warning(f'{missing} source position(s) missing prices. Their exposure is unavailable; full portfolio percentages are blank.')
    if selected.empty:
        with results_area:
            st.info('No holdings match the selected filters.')
        return valued
    exposures = normalize_exposures(selected)
    if lookthrough:
        try:
            exposures = expand_etfs(exposures, expanded_funds, holdings)
        except DataError as exc:
            st.error(str(exc))
            return valued
    exposures["asset_name"] = exposures["asset_name"].map(display_name)
    residual = exposures.source_type.eq('etf_other')
    source_names = {row.id: instrument_name(row) for row in holdings.itertuples()}
    exposures.loc[residual, 'asset_name'] = exposures.loc[residual, 'source_instrument'].map(
        lambda asset: f'{source_names.get(asset, "ETF")} / Other')
    effective_exposures = exposures
    saved_classifications = classifications
    mode = results_area.segmented_control('Exposure view', ['Assets', 'Themes & sectors', 'Geography'], default='Assets', key='exposure_view')
    if mode == 'Themes & sectors':
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
    match_note = '* Estimated company match · Review or undo in Data & settings → Company merges' if lookthrough and any(group.enabled and group.basis == 'Estimated name match' for group in plan.groups) else ''
    with results_area:
        if mode == 'Geography':
            render_geography(complete_exposures(effective_exposures, selected), geography, complete=missing == 0, query=query)
            if match_note:
                st.caption(match_note)
        elif mode != 'Themes & sectors':
            render_assets(complete_exposures(effective_exposures, selected), selected, holdings, funds, saved_classifications,
                          query=query, show_tickers=show_tickers, show_chart=show_chart, complete=missing == 0,
                          breakdown=lookthrough, context=str(data_dir.resolve()), footer=match_note, geography=geography)
        else:
            render_theme_view(exposures, selected, holdings, classifications, names, dimensions, targets, performance,
                              total, all_missing, selected_total, show_tickers, performance_percent, query)
            if match_note:
                st.caption(match_note)
    with settings_panel:
        if display_group is not None:
            render_group_members(selected, display_group)
        source_panel = st.expander('Source positions & price details', key='exposure_sources_open', on_change='rerun')
        if source_panel.open:
            with source_panel:
                render_source_positions(selected, valued, names, saved_classifications, show_tickers, performance_percent, dimensions)
        stock_panel = st.expander('Stock-only company analysis', key='exposure_stock_open', on_change='rerun')
        if stock_panel.open:
            with stock_panel:
                render_stock_exposure(selected, expanded_funds, data_dir)
    return valued


def render_theme_view(exposures, selected, holdings, classifications, names, dimensions, targets, performance,
                      total, all_missing, selected_total, show_tickers, performance_percent, query):
    options = [('selected_labels', 'Selected labels'),
               *[(f'taxonomy:{name}', name.replace('_', ' ').title()) for name in names],
               *[(f'metadata:{name}', name.replace('_', ' ').title()) for name in dimensions],
               ('holding', 'Investment')]
    view_col, root_col, options_col = st.columns([2, 3, 1.4], vertical_alignment='bottom')
    view = view_col.selectbox('Group by', [key for key, _ in options], format_func=dict(options).get, key='exposure_group')
    root, depth, include_holdings = (), None, False
    show_paths = False
    chart_settings = options_col.popover('Chart options') if view != 'selected_labels' else None
    if view.startswith('taxonomy:'):
        taxonomy = view.removeprefix('taxonomy:')
        if taxonomy == 'ai':
            st.caption('AI themes describe business roles, not the proportion of company revenue from AI.')
        roots = [(), *branches(classifications, exposures.asset_id.tolist(), taxonomy)]
        root_key = f'exposure_root_{taxonomy}'
        if st.session_state.get(root_key, ()) not in roots:
            st.session_state[root_key] = ()
        navigation, back = root_col.columns([5, 1], vertical_alignment='bottom')
        root = navigation.selectbox('Hierarchy root', roots, format_func=lambda path: ' › '.join(path) if path else 'Entire taxonomy', key=root_key)
        if root:
            back.button('Back', on_click=lambda: st.session_state.update({root_key: root[:-1]}))
        with chart_settings:
            max_depth = max((len(path) - len(root) for path in roots if path[:len(root)] == root), default=0)
            depth = st.selectbox('View depth', [None, *range(1, max_depth + 1)], format_func=lambda value: 'Full tree' if value is None else f'{value} level(s) below root', key=f'depth_{taxonomy}_{root}')
            include_holdings = st.checkbox('Show holdings beneath labels', key='exposure_control_holdings')
            show_paths = st.checkbox('Show classification paths', key='exposure_control_paths', help='Show the taxonomy breadcrumb for each category.')
    if view != 'selected_labels':
        with chart_settings:
            chart_type = st.selectbox('Chart', ['Sunburst', 'Treemap', 'Bar', 'Pie'], key='exposure_control_chart')
    if query:
        st.caption('Search applies in Assets. Theme percentages cover the selected source scope.')
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
        with st.container(key='exposure_theme_results'):
            chart_area, table_area = st.columns([1, 1.3], gap='large', vertical_alignment='center')
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
                    figure.update_layout(height=400, uniformtext=None, margin=dict(t=12, b=12, l=12, r=12))
                    chart_key = 'exposure_theme_chart_' + sha256(repr((view, root, depth, include_holdings)).encode()).hexdigest()[:16]
                    with chart_area:
                        st.plotly_chart(figure, width='stretch', key=chart_key, config={'displayModeBar': False})
                        if chart_type in {'Sunburst', 'Treemap'} and view.startswith('taxonomy:'):
                            categories = {row.node_id: tuple(row.path) for row in nodes.itertuples() if row.kind == 'category'}
                            categories[nodes.iloc[0].node_id] = root[:-1] if root else ()
                            sync_chart_category(chart_key, categories, root_key, event_name=f'plotly_{chart_type.lower()}click')
                else:
                    st.info("This selection has no current allocation. Its targets are shown below.")
                if view.startswith("taxonomy:"):
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
                table_area.dataframe(allocation, column_order=['Category', 'EUR value', 'Allocation %', *(['Labels'] if 'Labels' in allocation else []), *(['Classification path'] if show_paths else [])], hide_index=True, width="stretch", height="content", column_config={
                    "Category": "Investment" if view == "holding" else "Category",
                    "Classification path": st.column_config.TextColumn(help="Full breadcrumb within the selected taxonomy."),
                    "EUR value": st.column_config.NumberColumn(format="€ %.2f"),
                    "Allocation %": st.column_config.NumberColumn(format="%.2f %%"),
                } | label_config | target_column_config() | performance_column_config(percent=performance_percent))


def render_source_positions(selected, valued, names, classifications, show_tickers, performance_percent, dimensions):
    displayed_classifications = ['labels'] if 'labels' in names else names
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
        table["Performance"] = table.return_pct.where(table.unrealized_gain_eur.notna()) if performance_percent else table.unrealized_gain_eur
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
    if st.checkbox("Show price details", key="exposure_show_price_details", help="Quote timestamps, FX status and valuation notes"):
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
                                    "AI theme" if name == "ai" else name.replace("_", " ").title()) for name in names} | performance_column_config(percent=performance_percent))
    st.caption("Latest available daily close · Prices may be delayed")
