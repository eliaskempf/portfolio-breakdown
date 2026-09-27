"""One searchable exposure table, with on-demand source and fund details."""
from hashlib import sha256
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio_app.charts import style_figure
from portfolio_app.etf import matching_fund
from portfolio_app.etf_ui import render_fund_details
from portfolio_app.exposure_tables import asset_exposure_table, exposure_sources
from portfolio_app.label_presentation import badge_column, taxonomy_colors


def render_assets(exposures, selected, holdings, funds, classifications, *, query='', show_tickers=False, show_chart=False, complete=True):
    taxonomy = 'labels' if any('labels' in item for item in classifications.values()) else 'sector'
    table = asset_exposure_table(exposures, classifications=classifications, taxonomy=taxonomy, complete=complete)
    if query.strip():
        terms = table[['Asset', 'Ticker']].fillna('').agg(' '.join, axis=1).str.casefold()
        table = table.loc[terms.str.contains(query.strip().casefold(), regex=False)].reset_index(drop=True)
        st.caption(f'{len(table)} matching assets · Search keeps percentages relative to the selected portfolio')
    if table.empty:
        st.info('No assets match this search.')
        return
    signature = sha256(repr((table.asset_id.tolist(), list(selected.position_id), list(exposures.source_type))).encode()).hexdigest()[:16]
    key = f'exposure_assets_{signature}'
    def select():
        rows = st.session_state.get(key, {}).get('selection', {}).get('rows', [])
        if rows and rows[0] < len(table):
            st.session_state['exposure_asset_detail'] = table.iloc[rows[0]].asset_id
    st.dataframe(table, hide_index=True, width='stretch', height=min(620, 36 + 35 * len(table)),
                 on_select=select, selection_mode='single-row', key=key, column_order=[
                     'Asset', 'Ticker', 'Total (EUR)', 'Allocation %', 'Direct (EUR)', 'ETF-derived (EUR)', 'Labels'],
                 column_config={'asset_id': None, 'Ticker': 'Ticker' if show_tickers else None,
                 'Allocation %': st.column_config.NumberColumn('% of selected portfolio', format='%.2f %%'),
                 'Labels': badge_column('Labels', taxonomy_colors(classifications, taxonomy)),
                 **{column: st.column_config.NumberColumn(format='€ %.2f') for column in ['Direct (EUR)', 'ETF-derived (EUR)', 'Total (EUR)']}})
    st.caption('Select an asset to see its direct positions and contributing ETFs.')
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
        st.dataframe(sources, hide_index=True, width='stretch', column_config={
            'Value (EUR)': st.column_config.NumberColumn(format='€ %.2f')})
        source_ids = exposures.loc[exposures.asset_id.eq(asset), 'source_position_id']
        source_positions = selected.loc[selected.position_id.isin(source_ids)]
        relevant = {f.isin for position in source_positions.to_dict('records') if (f := matching_fund(position, funds)) is not None}
        render_fund_details([f for f in funds if f.isin in relevant], source_positions, holdings=holdings,
                            classifications=classifications, show_tickers=show_tickers)
        if st.button('Close exposure details'):
            dismiss()
            st.rerun()
    details()
