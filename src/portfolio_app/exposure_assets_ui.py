"""One searchable exposure table, with on-demand source and fund details."""
from hashlib import sha256
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio_app.charts import style_figure
from portfolio_app.etf import matching_fund
from portfolio_app.etf_ui import render_fund_details
from portfolio_app.exposure_tables import asset_exposure_table, exposure_sources
from portfolio_app.label_presentation import taxonomy_colors
from portfolio_app.list_ui import BOUNDED_LIST_HEIGHT, ListColumn, frame_rows, render_list


def render_assets(exposures, selected, holdings, funds, classifications, *, query='', show_tickers=False, show_chart=False, complete=True, breakdown=False, context='exposure'):
    taxonomy = 'labels' if any('labels' in item for item in classifications.values()) else 'sector'
    table = asset_exposure_table(exposures, classifications=classifications, taxonomy=taxonomy, complete=complete)
    if query.strip():
        terms = table[['Asset', 'Ticker']].fillna('').agg(' '.join, axis=1).str.casefold()
        table = table.loc[terms.str.contains(query.strip().casefold(), regex=False)].reset_index(drop=True)
        st.caption(f'{len(table)} matching assets · Search keeps percentages relative to the selected portfolio')
    if table.empty:
        st.info('No assets match this search.')
        return
    signature = sha256(repr((context, list(selected.position_id), breakdown)).encode()).hexdigest()[:16]
    key = f'exposure_assets_{signature}'
    def select(event):
        st.session_state['exposure_asset_detail'] = event['id']
    columns = [ListColumn('Asset', 'Asset')]
    if show_tickers:
        columns.append(ListColumn('Ticker', 'Ticker'))
    columns += [ListColumn('Total (EUR)', 'Total (EUR)', numeric=True),
                ListColumn('Allocation %', '% of selected portfolio', numeric=True),
                ListColumn('Sources', 'Sources'),
                ListColumn('Labels', 'Labels', badges=taxonomy_colors(classifications, taxonomy))]
    render_list(frame_rows(table, id_column='asset_id'), columns, key=key, context=key, title='Exposure assets',
                on_open=select, max_height=BOUNDED_LIST_HEIGHT if breakdown else None, default_sort='Total (EUR)')
    st.caption('Select an asset to see how much comes from each direct position and ETF.')
    if show_chart:
        largest = table.dropna(subset=['Total (EUR)']).head(12).iloc[::-1]
        figure = style_figure(go.Figure(go.Bar(x=largest['Total (EUR)'], y=largest.Asset, orientation='h')))
        figure.update_layout(height=max(260, 30 * len(largest)), xaxis_title='Exposure (EUR)', margin=dict(t=12, b=20, l=12, r=12))
        st.plotly_chart(figure, width='stretch', config={'displayModeBar': False})
    asset = st.session_state.get('exposure_asset_detail')
    if asset not in set(table.asset_id):
        st.session_state.pop('exposure_asset_detail', None)
        return
    def dismiss():
        st.session_state.pop('exposure_asset_detail', None)
    @st.dialog('Exposure details', width='large', on_dismiss=dismiss)
    def details():
        row = table.loc[table.asset_id.eq(asset)].iloc[0]
        st.subheader(row.Asset)
        st.caption('Unknown values remain unavailable; they are not treated as zero.') if pd.isna(row['Total (EUR)']) else st.metric('Total exposure', f'€{row["Total (EUR)"]:,.2f}')
        sources = exposure_sources(exposures, selected, asset)
        st.caption('Percentages are relative to this asset’s total exposure. Each account position is shown separately.')
        render_list(frame_rows(sources), [ListColumn('Source', 'Source'), ListColumn('Account', 'Account'),
                    ListColumn('Exposure', 'Source type'), ListColumn('Value (EUR)', 'Contribution (EUR)', numeric=True),
                    ListColumn('% of asset exposure', '% of asset exposure', numeric=True)],
                    key=f'{key}_sources', context=f'{key}_{asset}_sources', title='Exposure sources', default_sort='Value (EUR)')
        source_ids = exposures.loc[exposures.asset_id.eq(asset), 'source_position_id']
        source_positions = selected.loc[selected.position_id.isin(source_ids)]
        relevant = {f.isin for position in source_positions.to_dict('records') if (f := matching_fund(position, funds)) is not None}
        render_fund_details([f for f in funds if f.isin in relevant], source_positions, holdings=holdings,
                            classifications=classifications, show_tickers=show_tickers)
        if st.button('Close exposure details'):
            dismiss()
            st.rerun()
    details()
