"""Overview with a single authoritative category scope."""
from portfolio_app.chart_hover import chart_hover

from portfolio_app.currency_display import money, currency_symbol
from portfolio_app.ui_help import column_help
from hashlib import sha256
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from portfolio_app.charts import chart_config

from portfolio_app.settings import GAIN_COLOR, LOSS_COLOR
from portfolio_app.charts import hatch_foregrounds, hierarchy_chart, sort_allocation_nodes, strategic_colors, style_figure
from portfolio_app.chart_navigation import sync_chart_category
from portfolio_app.position_list import render_overview_positions
from portfolio_app.futures_position_ui import render_future_position
from portfolio_app.list_ui import ListColumn, frame_rows, render_list
from portfolio_app.presentation import value_metric
from portfolio_app.performance import position_performance, summarize_performance
from portfolio_app.strategic import bucket_paths, category_labels, bucket_positions, strategic_summary, strategic_tree, strategic_performance


def render_strategic_overview(valued, config, *, open_position=None, percent=False, on_toggle_gain=None, edit_position=None, position_context="overview", analytics=None, exposure_result=None, future=None, data_dir=None, leverage_enabled=True, open_exposure_targets=None, open_capital_targets=None, current_valued=None, automatic_history=False):
    if 'unrealized_gain_reporting' not in valued:
        valued = position_performance(valued)
    from portfolio_app.futures import funded_strategy_positions
    valued = funded_strategy_positions(valued, future, config)
    has_strategy = (future is not None and future.reference_capital_eur is not None) or bool(valued.attrs.get('investment_members'))
    paths = bucket_paths(config)
    names = {b.id: b.name for b in config.buckets} | {'unassigned': 'Unassigned'}
    labels = category_labels(config) | {'': 'Portfolio', 'unassigned': 'Unassigned'}
    # Unassigned can retain closed-history gains even with no current value.
    options = ['', *[b.id for b in config.buckets], 'unassigned']
    key = 'strategic_category'
    if st.session_state.get(key, '') not in options:
        st.session_state[key] = ''
    bucket = st.session_state.get(key, '')
    crumbs = ['', *paths[bucket]]
    if bucket:
        with st.container(horizontal=True, vertical_alignment='center', gap='small'):
            for index, node in enumerate(crumbs):
                if index:
                    st.caption('›')
                st.button(names.get(node, 'Portfolio'), help='Navigate to this category and show its holdings and allocation.', key=f'strategic_crumb_{node}', type='tertiary',
                          disabled=node == bucket, on_click=lambda node=node: st.session_state.update({key: node}))
    with st.container(key='tour_overview_scope'):
        navigation, back = st.columns([5, 1], vertical_alignment='bottom')
        bucket = navigation.selectbox('Category', options, help='Choose the category to inspect; figures use that category’s current holdings.', key=key,
            format_func=labels.get)
        back.button('Back', help='Return to the preceding level of this view.', disabled=not bucket, width='stretch',
                    on_click=lambda: st.session_state.update({key: paths[bucket][-2] if len(paths[bucket]) > 1 else ''}))
        scope = names.get(bucket, 'Portfolio')
        selected = bucket_positions(valued, config, bucket)
        from portfolio_app.futures_valuation import summarize_freshness
        freshness = summarize_freshness(selected)
        subtotal = float(selected.current_value_reporting.sum())
        missing = int(selected.current_value_reporting.isna().sum())
        whole = float(valued.current_value_reporting.sum())
        performance = summarize_performance(selected)
        bucket_ids = ({''} if bucket == 'unassigned' else config.leaves(bucket)) if bucket else None

    scoped_future = future
    if future is not None and bucket:
        assigned = future.bucket_id if future.bucket_id in config.leaves() else ''
        if assigned not in bucket_ids:
            scoped_future = None
    history = None
    if data_dir is not None:
        from portfolio_app.history_ui import prepare_portfolio_history
        from portfolio_app.positions import read_snapshot
        from portfolio_app.holdings import DataError
        try:
            history = prepare_portfolio_history(data_dir,read_snapshot(data_dir / 'holdings.csv').holdings,
                config=config,bucket=bucket,displayed=valued, current_valued=current_valued if current_valued is not None else valued,
                include_points=st.session_state.get('strategic_view', 'Performance') == 'Performance')
        except (DataError,ValueError,OSError) as exc:
            st.caption(str(exc))
    from portfolio_app.history_scope import can_start_history_chart
    has_history = history is not None and (history['view'].useful or
        can_start_history_chart(history['journal'], history['scope']))
    default_view = 'Performance' if has_history else 'Allocation'
    measure = 'Capital allocation'
    with st.container(horizontal=True, vertical_alignment='center', gap='medium', key='overview_view_controls'):
        mode = st.segmented_control('Overview view', ['Allocation', 'Performance', *(['Analytics'] if analytics else [])], help='Switch between allocation, performance and available portfolio analytics.', default=st.session_state.get('strategic_view', default_view), key='strategic_view', selection_mode='single', required=True, label_visibility='collapsed')
    if mode != 'Performance' or not has_history:
        columns = st.columns([2, 1, 1] if exposure_result is not None and leverage_enabled else [2, 1])
        first, share = columns[:2]
        second = columns[2] if len(columns) == 3 else None
        share.metric('Portfolio share', f'{100 * subtotal / whole:.1f}%' if not missing and valued.current_value_reporting.notna().all() and whole else '—',
                     help='Selected capital as a share of all owned capital, including a funded futures strategy. Excludes debt and any legacy unfunded settlement adjustment.')
        with first:
            value_metric(exposure_result.net_equity if exposure_result is not None and not bucket else subtotal,
                         performance, missing=missing, percent=percent, key='overview_value',
                         on_toggle_gain=on_toggle_gain,
                         label=('Category value' if bucket else 'Portfolio value') if exposure_result is not None else None,
                         funded_gain=exposure_result is not None, strategy_gain=has_strategy, freshness=freshness, freshness_rows=selected,
                         known=history.get('current_gain') if history else None,
                         twr=history['view'].all_result.time_weighted if history else None)
        if second is not None:
            with second:
                from portfolio_app.portfolio_exposure_ui import render_equity_exposure_metric
                render_equity_exposure_metric(exposure_result, market=True, bucket_ids=bucket_ids, freshness=freshness, freshness_rows=selected)

    if mode == 'Analytics':
        analytics(selected.loc[selected.position_id.ne('future:overlay')], scope, scoped_future, bool(bucket))
        return
    if mode == 'Performance':
        if has_history:
            from portfolio_app.history_ui import render_portfolio_history
            render_portfolio_history(data_dir,history,label='Category value' if bucket else 'Portfolio value',
                                     percent=percent,on_toggle_gain=on_toggle_gain,automatic=automatic_history)
        with st.container(key='tour_performance'):
            table = strategic_performance(valued, config, bucket)
            if history is not None:
                from portfolio_app.gain_comparison import known_category_performance
                from portfolio_app.currency_display import reporting_currency
                table = known_category_performance(valued, read_snapshot(data_dir / 'holdings.csv').holdings,
                    config, history['journal'], history['observations'],
                    current_valued if current_valued is not None else valued, bucket, target=reporting_currency(),
                    calculation=history['calculation'], current_values=history['current_values'])
            st.markdown('**Category comparison**', help='Total known gain includes retained closed holdings and recorded income. Return is full recorded-history time-weighted return (TWR), not gain divided by remaining cost. Current category membership does not reconstruct past assignments; unsupported account splits remain unavailable.')
            measure = st.segmented_control('Chart measure', ['Return (%)', 'Gain'], help='Cumulative TWR uses daily valuations and recorded cash flows. Gain shows supported monetary subtotals, with coverage.',
                default='Gain', key='strategic_performance_measure') or 'Gain'
            chart_percent = measure == 'Return (%)'
            available = table.dropna(subset=[measure]).sort_values(measure)
            if not available.empty:
                hover = [[money(row["Cost"]), money(row["Gain"], signed=True),
                          f'{row["Return (%)"]:+,.2f}%' if pd.notna(row['Return (%)']) else 'Unavailable',
                          row['Coverage'], row['Status']] for _, row in available.iterrows()]
                figure = style_figure(go.Figure(go.Bar(x=available[measure], y=available.Category, orientation='h',
                    marker_color=[LOSS_COLOR if row[measure] < 0 else GAIN_COLOR for _, row in available.iterrows()],
                    text=[f'{value:+,.2f}%' if chart_percent else money(value, signed=True) for value in available[measure]], textposition='auto',
                    customdata=hover, hovertemplate='%{y}<br>TWR: %{customdata[2]}<br>Gain: %{customdata[1]}<br>Capital basis: %{customdata[0]}<br>Coverage: %{customdata[3]} · %{customdata[4]}<extra></extra>')))
                figure.update_layout(height=max(260, 36 * len(available)), xaxis_title=measure, margin=dict(l=12, r=12, t=12, b=30))
                st.plotly_chart(chart_hover(figure), width='stretch', config=chart_config(), key='category_performance_chart')
                from portfolio_app.freshness_ui import render_comparison_freshness
                render_comparison_freshness(valued, table.attrs.get('valuation_members', {}),
                    available.Category.tolist(), chart_key='category_performance_chart')
            elif chart_percent and table['Gain'].notna().any():
                st.caption('TWR unavailable · Choose Gain for supported monetary results.')
            elif performance.cash_only:
                st.info('N/A — cash balance. Purchase-based return does not apply without a recorded conversion basis; interest is separate.')
            else:
                st.info('Add convertible buy-ins to see performance for this category.')
        return
    else:
        with st.container(key='overview_allocation'):
            chart_column, table_column = st.columns([1, 1.3], gap='large', vertical_alignment='top')
            with table_column:
                if exposure_result is not None and leverage_enabled:
                    # Recover sessions left with no selection by the former toggle behavior.
                    if st.session_state.get('strategic_allocation_measure', 'Market exposure') is None:
                        st.session_state['strategic_allocation_measure'] = 'Market exposure'
                    measure = st.segmented_control('Allocation measure', ['Market exposure', 'Capital allocation'],
                        default='Market exposure', required=True, key='strategic_allocation_measure', label_visibility='collapsed',
                        help='Exposure compares market exposure shares with target percentages. Capital compares invested value with target percentages.')
            from portfolio_app.exposure_presentation import exposure_positions, companion_nodes
            market = measure == 'Market exposure'
            plotted = exposure_positions(valued, exposure_result, future) if market else valued
            plotted_selection = bucket_positions(plotted, config, bucket)
            partial = market and plotted_selection.current_value_reporting.isna().any()
            chart_positions = exposure_positions(valued, exposure_result, future, known_only=True) if partial else plotted
            if partial:
                from portfolio_app.portfolio_exposure_ui import render_exposure_coverage
                scoped_rows = exposure_result.exposures.loc[exposure_result.exposures.source_position_id.isin(plotted_selection.position_id)]
                render_exposure_coverage(scoped_rows, plotted)
                st.caption('Chart and Known exposure show only resolved contributions. Total exposure stays unavailable for affected rows.')
            with chart_column:
                tree = companion_nodes(sort_allocation_nodes(strategic_tree(chart_positions, config, bucket)), future)
                if bucket_positions(chart_positions, config, bucket).current_value_reporting.lt(0).any():
                    from portfolio_app.portfolio_exposure_ui import signed_chart
                    amount = 'Exposure' if market else 'Value'
                    table = strategic_summary(chart_positions, config, bucket).rename(columns={'Value': amount})
                    signed_chart(table, 'Category', amount, 'strategic_exposure_chart' if market else 'signed_capital_chart')
                elif not tree.empty and tree.value.max() > 0:
                    figure = hierarchy_chart(tree, 'Sunburst', preserve_order=True)
                    reference = strategic_tree(valued, config)
                    colors = dict(zip(reference.node_id, strategic_colors(reference, config)))
                    position_colors = {json.loads(row.node_id)[2]: colors[row.node_id] for row in reference.itertuples() if row.kind == 'holding'}
                    linked_color = position_colors.get(future.proxy_position_id) if future else None
                    shades, patterns = [], []
                    for row in tree.itertuples():
                        derivative = row.kind == 'holding' and json.loads(row.node_id)[2] == 'future:overlay'
                        shades.append(linked_color if derivative and linked_color else colors.get(row.node_id, '#7b8493'))
                        patterns.append('/' if derivative else '')
                    figure.update_traces(maxdepth=3, insidetextorientation='auto', marker_colors=shades,
                        leaf=dict(opacity=1),
                        marker_pattern=dict(shape=patterns, fillmode='overlay', bgcolor=shades,
                                            fgcolor=hatch_foregrounds(shades), fgopacity=1, size=8, solidity=.12),
                        hovertemplate='%{label}<br>' + currency_symbol() + '%{value:,.2f}<br>%{customdata[0]:.2%} of ' + ('selected market exposure' if market else scope.replace('<', '&lt;')) + '<extra></extra>')
                    figure.update_layout(height=420, uniformtext=None, margin=dict(t=10, b=10, l=10, r=10))
                    if partial:
                        figure.update_traces(hovertemplate='%{label}<br>Known exposure: ' + currency_symbol() + '%{value:,.2f}<extra></extra>')
                    signature = sha256(repr((config, list(plotted.position_id), bucket, market)).encode()).hexdigest()[:16]
                    chart_key = f'strategic_chart_{signature}'
                    st.plotly_chart(chart_hover(figure), width='stretch', height=420, theme='streamlit', key=chart_key, config=chart_config())
                    categories = {row.node_id: row.path[-1] if row.path else '' for row in tree.itertuples() if row.kind == 'category'}
                    parent = paths[bucket][-2] if len(paths[bucket]) > 1 else ''
                    categories[tree.loc[tree.parent_id.eq(''), 'node_id'].iloc[0]] = parent
                    categories[''] = parent
                    positions = {row.node_id: json.loads(row.node_id)[2] for row in tree.itertuples() if row.kind == 'holding'}
                    sync_chart_category(chart_key, categories, key, positions=positions, open_position=lambda pid: render_future_position(future, data_dir) if pid == 'future:overlay' else open_position(pid) if open_position else None)
                else:
                    st.info('No positive exposure in this category.' if market else 'No priced holdings in this category.' if missing else 'No current holdings in this category.')
            with table_column, st.container(key='overview_allocation_summary'):
                title = 'Market exposure' if market else 'Allocation'
                with st.container(horizontal=True, wrap=False, vertical_alignment='center', key='overview_allocation_heading'):
                    st.markdown(f'**{title}**', width='stretch', help=(
                        'Market exposure includes signed futures notional and leveraged fund exposure.'
                        if market else 'Capital allocated to each category, including futures investment equity. Uses capital allocation targets.'))
                    target_controls = st.container(width='content')
                if not market and open_capital_targets is not None:
                    with target_controls:
                        st.button('Edit', icon=':material/edit:', key='overview_capital_targets',
                                  on_click=open_capital_targets, type='tertiary', help='Edit capital allocation targets.')
                table = strategic_summary(plotted, config, bucket,
                    known_exposure=chart_positions.set_index('position_id').current_value_reporting if partial else None)
                if partial:
                    table['Status'] = table.Status.replace('Missing price', 'Partial exposure: see unresolved assets above.')
                target_help = {}
                if market:
                    table = table.rename(columns={'Value': 'Exposure', 'Current (%)': '% of exposure'})
                    from portfolio_app.exposure_target_presentation import TARGET_COLUMN_HELP, render_market_target_summary
                    target_table = render_market_target_summary(exposure_result, config, data_dir, bucket,
                                                                open_targets=open_exposure_targets, controls=target_controls,
                                                                composition_table=table, open_composition=open_capital_targets)
                    if target_table is not None:
                        table = target_table
                        target_help = {**TARGET_COLUMN_HELP, 'Target (%)': 'Saved target share within this scope. A percentage comparison does not require a total exposure multiple.'}
                    else:
                        table[['Target (%)', 'Gap (pp)']] = float('nan')
                for col in table:
                    if col not in {'Category', 'Status'}:
                        table[col] = pd.to_numeric(table[col], errors='coerce')
                # Keep row qualifications on hover instead of a sparse Status column.
                table['Row details'] = table['Status'].replace({
                    'Unresolved constituents': 'Market exposure is known; some underlying constituents are unresolved.',
                    'Unassigned': 'No strategic category assigned.',
                    'Missing price': 'Missing valuation; percentages are unavailable.',
                    'Empty': 'No current holdings.'})
                columns = [column for column in table if column not in {'Status', 'Row details'} |
                           ({'Target exposure', 'Gap'} if market else set())]
                render_list(frame_rows(table), [ListColumn(column, f'% of {scope}' if column == 'Current (%)' else column,
                            tooltip='Row details' if column == 'Category' else '',
                            help=target_help.get(column, column_help(column, scope=scope)), numeric=column not in {'Category', 'Status'}, signed=column in {'Gap (pp)', 'Gap'}, color_signed=False) for column in columns],
                            key='strategic_allocation_table', context=f'{position_context}_{bucket}_{measure}',
                            title=title, default_sort='% of exposure' if market else 'Value')
    st.subheader('Positions')
    render_overview_positions(selected, config, context=f'overview_{position_context}_{bucket}', scope=scope,
                              open_position=open_position, edit_position=edit_position, future=scoped_future,
                              open_future=lambda: render_future_position(scoped_future, data_dir))

    if exposure_result is not None and not bucket:
        from portfolio_app.portfolio_exposure_ui import render_capital_reconciliation
        with st.expander('Portfolio value reconciliation'):
            render_capital_reconciliation(exposure_result, actual=bool(valued.attrs.get('actual_accounts')))
