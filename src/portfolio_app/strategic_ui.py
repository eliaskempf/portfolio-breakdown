"""Overview with a single authoritative category scope."""
from hashlib import sha256
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio_app.charts import hierarchy_chart, sort_allocation_nodes, strategic_colors, style_figure
from portfolio_app.chart_navigation import sync_chart_category
from portfolio_app.position_list import render_overview_positions
from portfolio_app.list_ui import ListColumn, frame_rows, render_list
from portfolio_app.metric_interactions import toggle_gain_unit
from portfolio_app.performance import position_performance, summarize_performance
from portfolio_app.strategic import bucket_paths, category_labels, bucket_positions, strategic_summary, strategic_tree, strategic_performance


def render_strategic_overview(valued, config, *, open_position=None, percent=False, on_toggle_gain=None, edit_position=None, position_context="overview"):
    if 'unrealized_gain_eur' not in valued:
        valued = position_performance(valued)
    paths = bucket_paths(config)
    names = {b.id: b.name for b in config.buckets} | {'unassigned': 'Unassigned'}
    labels = category_labels(config) | {'': 'Portfolio', 'unassigned': 'Unassigned'}
    options = ['', *[b.id for b in config.buckets]]
    if valued.bucket_id.eq('').any():
        options.append('unassigned')
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
                st.button(names.get(node, 'Portfolio'), key=f'strategic_crumb_{node}', type='tertiary',
                          disabled=node == bucket, on_click=lambda node=node: st.session_state.update({key: node}))
    navigation, back = st.columns([5, 1], vertical_alignment='bottom')
    bucket = navigation.selectbox('Category', options, key=key,
        format_func=labels.get)
    back.button('Back', disabled=not bucket, width='stretch',
                on_click=lambda: st.session_state.update({key: paths[bucket][-2] if len(paths[bucket]) > 1 else ''}))
    scope = names.get(bucket, 'Portfolio')
    selected = bucket_positions(valued, config, bucket)
    subtotal = float(selected.current_value_eur.sum())
    missing = int(selected.current_value_eur.isna().sum())
    whole = float(valued.current_value_eur.sum())
    performance = summarize_performance(selected)
    first, second = st.columns([2, 1])
    gain = performance.return_pct if percent else performance.gain_eur
    gain_text = 'Unavailable' if gain is None else f'{gain:+,.2f}%' if percent else f'{"-" if gain < 0 else "+"}€{abs(gain):,.2f}'
    with first.container(key='overview_value'):
        st.metric('Priced value' if missing else 'Current value', f'€{subtotal:,.2f}',
                  delta=gain_text, delta_color='normal' if gain else 'off', delta_arrow='off',
                  delta_description='Return on cost' if percent else 'Unrealized gain / loss',
                  help='Performance uses positions with recorded EUR buy-ins and available prices; coverage is shown below.')
    if on_toggle_gain:
        toggle_gain_unit(percent=percent, on_toggle=on_toggle_gain)
    second.metric('Portfolio share', f'{100 * subtotal / whole:.1f}%' if not missing and valued.current_value_eur.notna().all() and whole else '—')
    st.caption(f'{scope} · {len(selected)} positions · Performance coverage: {performance.covered_count} of {performance.held_count} held positions · EUR buy-ins · Excludes dividends and realized gains')
    if missing:
        st.warning(f'{missing} position(s) missing prices. Chart areas use priced value; full allocation percentages are unavailable.')
    mode = st.segmented_control('Overview view', ['Allocation', 'Performance'], default='Allocation', key='strategic_view', selection_mode='single', label_visibility='collapsed')
    if mode == 'Performance':
        table = strategic_performance(valued, config, bucket)
        measure = st.segmented_control('Chart measure', ['Return (%)', 'Gain (EUR)'],
            default='Return (%)', key='strategic_performance_measure') or 'Return (%)'
        chart_percent = measure == 'Return (%)'
        st.caption('Return compares unrealized gain with recorded buy-in cost. Euro gain shows the amount gained or lost. Both are shown in the tables and on hover.')
        available = table.dropna(subset=[measure]).sort_values(measure)
        if not available.empty:
            hover = [[f'€{row["Cost (EUR)"]:,.2f}', f'{row["Gain (EUR)"]:+,.2f} EUR',
                      f'{row["Return (%)"]:+,.2f}%' if pd.notna(row['Return (%)']) else 'Unavailable',
                      row['Coverage'], row['Status']] for _, row in available.iterrows()]
            figure = style_figure(go.Figure(go.Bar(x=available[measure], y=available.Category, orientation='h',
                marker_color=['#b84655' if value < 0 else '#27836c' for value in available[measure]],
                text=[f'{value:+,.2f}' + ('%' if chart_percent else ' €') for value in available[measure]], textposition='auto',
                customdata=hover, hovertemplate='%{y}<br>Return: %{customdata[2]}<br>Gain: %{customdata[1]}<br>Buy-in cost: %{customdata[0]}<br>Coverage: %{customdata[3]} · %{customdata[4]}<extra></extra>')))
            figure.update_layout(height=max(260, 36 * len(available)), xaxis_title=measure, margin=dict(l=12, r=12, t=12, b=30))
            st.plotly_chart(figure, width='stretch', config={'displayModeBar': False})
        elif chart_percent and table['Gain (EUR)'].notna().any():
            st.info('Percentage return is unavailable for zero buy-in cost. Choose Gain (EUR) to see the euro amounts.')
        else:
            st.info('Add EUR buy-ins to see performance for this category.')
        render_list(frame_rows(table), [ListColumn(column, column,
                    numeric=column in {'Cost (EUR)', 'Gain (EUR)', 'Return (%)'},
                    signed=column in {'Gain (EUR)', 'Return (%)'}) for column in table],
                    key='strategic_performance_table', context=f'{position_context}_{bucket}_performance',
                    title='Category performance', default_sort=measure)
    else:
        with st.container(key='overview_allocation'):
            chart_column, table_column = st.columns([1, 1.3], gap='large', vertical_alignment='center')
            with chart_column:
                tree = sort_allocation_nodes(strategic_tree(valued, config, bucket))
                if not tree.empty and subtotal > 0:
                    figure = hierarchy_chart(tree, 'Sunburst')
                    reference = strategic_tree(valued, config) if bucket else tree
                    colors = dict(zip(reference.node_id, strategic_colors(reference, config)))
                    figure.update_traces(maxdepth=3, insidetextorientation='auto', marker_colors=[colors[node] for node in tree.node_id],
                        hovertemplate='%{label}<br>€%{value:,.2f}<br>%{customdata[0]:.2%} of ' + ('priced value' if missing else scope.replace('<', '&lt;')) + '<extra></extra>')
                    figure.update_layout(height=420, uniformtext=None, margin=dict(t=10, b=10, l=10, r=10))
                    signature = sha256(repr((config, list(valued.position_id), bucket)).encode()).hexdigest()[:16]
                    chart_key = f'strategic_chart_{signature}'
                    st.plotly_chart(figure, width='stretch', height=420, theme='streamlit', key=chart_key, config={'displayModeBar': False})
                    categories = {row.node_id: row.path[-1] if row.path else '' for row in tree.itertuples() if row.kind == 'category'}
                    parent = paths[bucket][-2] if len(paths[bucket]) > 1 else ''
                    categories[tree.loc[tree.parent_id.eq(''), 'node_id'].iloc[0]] = parent
                    categories[''] = parent
                    positions = {row.node_id: json.loads(row.node_id)[2] for row in tree.itertuples() if row.kind == 'holding'}
                    sync_chart_category(chart_key, categories, key, positions=positions, open_position=open_position)
                else:
                    st.info('No priced holdings in this category.' if missing else 'No current holdings in this category.')
            with table_column, st.container(key='overview_allocation_summary'):
                st.markdown('**Allocation**')
                st.caption(f'Current and target percentages are relative to {scope}.')
                table = strategic_summary(valued, config, bucket)
                for col in ['Value (EUR)', 'Current (%)', 'Target (%)', 'Gap (pp)']:
                    table[col] = pd.to_numeric(table[col], errors='coerce')
                if table['Status'].eq('').all():
                    table = table.drop(columns='Status')
                render_list(frame_rows(table), [ListColumn(column, f'% of {scope}' if column == 'Current (%)' else column,
                            numeric=column in {'Value (EUR)', 'Current (%)', 'Target (%)', 'Gap (pp)'},
                            signed=column == 'Gap (pp)') for column in table],
                            key='strategic_allocation_table', context=f'{position_context}_{bucket}_allocation',
                            title='Allocation', default_sort='Value (EUR)')
    st.subheader('Positions')
    st.caption('Select a position for details and price history. Use the pencil to edit.')
    render_overview_positions(selected, config, context=f'overview_{position_context}_{bucket}', scope=scope,
                              open_position=open_position, edit_position=edit_position)
