"""One searchable exposure table, with on-demand source and fund details."""

from portfolio_app.currency_display import currency_symbol
from hashlib import sha256
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio_app.charts import style_figure
from portfolio_app.etf import matching_fund
from portfolio_app.etf_ui import render_fund_details
from portfolio_app.exposure_tables import asset_exposure_table, exposure_sources, source_contributions
from portfolio_app.label_presentation import taxonomy_colors
from portfolio_app.list_ui import BOUNDED_LIST_HEIGHT, ListColumn, frame_rows, render_list
from portfolio_app.taxonomy import describe, taxonomy_names


def source_previews(exposures, selected, table):
    """Ship only compact summaries; expansion needs no server round trip."""
    contributions = source_contributions(exposures.loc[exposures.asset_id.isin(table.asset_id)], selected)
    counts = contributions.asset_id.value_counts().to_dict()
    previews = {}
    # One sort and a linear pass also keep initial rendering cheap for large ETFs.
    for row in contributions.sort_values('Value', ascending=False, na_position='last').to_dict('records'):
        asset = row['asset_id']
        if asset not in previews:
            previews[asset] = dict(items=[], more=max(0, counts[asset] - 4))
        items = previews[asset]['items']
        if len(items) == 4:
            continue
        amount = row['Value']
        share = row['% of asset exposure']
        label = ('Direct · ' if row['Exposure'] == 'Direct' else '') + row['Source']
        if row['Account']:
            label += f' · {row["Account"]}'
        items.append(dict(label=label,
                          amount='Unavailable' if pd.isna(amount) else (f'€{amount:,.2f}').replace('€', currency_symbol()),
                          share='—' if pd.isna(share) else f'{share:.2f}%'))
    return previews


def render_assets(exposures, selected, holdings, funds, classifications, *, query='', show_tickers=False, show_chart=False, complete=True, breakdown=False, context='exposure', footer='', geography=None):
    default = 'labels' if any('labels' in item for item in classifications.values()) else 'sector'
    titles = {'labels': 'Themes', 'sector': 'Sector', 'geography': 'Geography'}
    taxonomy = st.selectbox('Asset classifications', list(titles), index=list(titles).index(default),
                            format_func=titles.get, key='exposure_asset_classifications')
    if geography is not None:
        classifications = {asset: dict(entry) for asset, entry in classifications.items()}
        for asset, entry in geography.classifications(exposures.asset_id).items():
            classifications.setdefault(asset, {}).update(entry)
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
    columns = [ListColumn('Asset', 'Asset', width=260)]
    if show_tickers:
        columns.append(ListColumn('Ticker', 'Ticker', width=110))
    columns += [ListColumn('Total', 'Total', numeric=True, width=140),
                ListColumn('Allocation %', '% of selected portfolio', numeric=True, width=190),
                ListColumn('Sources', 'Sources', width=190),
                ListColumn('Labels', titles[taxonomy], badges=taxonomy_colors(classifications, taxonomy))]
    render_list(frame_rows(table, id_column='asset_id'), columns, key=key, context=key, title='Exposure assets',
                on_open=select, max_height=BOUNDED_LIST_HEIGHT if breakdown else None, default_sort='Total',
                preview_column='Sources', preview_value_column='Total', preview_share_column='Allocation %', previews=source_previews(exposures, selected, table))
    if footer:
        st.caption(footer)
    if show_chart:
        largest = table.dropna(subset=['Total']).head(12).iloc[::-1]
        figure = style_figure(go.Figure(go.Bar(x=largest['Total'], y=largest.Asset, orientation='h')))
        figure.update_layout(height=max(260, 30 * len(largest)), xaxis_title='Exposure', margin=dict(t=12, b=20, l=12, r=12))
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
        st.caption('Unknown values remain unavailable; they are not treated as zero.') if pd.isna(row['Total']) else st.metric('Total exposure', (f'€{row["Total"]:,.2f}').replace('€', currency_symbol()))
        for name in taxonomy_names(classifications):
            st.write(f'{titles.get(name, name.replace("_", " ").title())}: {describe(classifications, asset, name)}')
        if geography is not None:
            st.caption('Geography source: ' + geography.sources.get(asset, 'No local metadata'))
            if reason := geography.reasons.get(asset):
                st.caption(reason)
        sources = exposure_sources(exposures, selected, asset)
        st.caption('Asset weight is the share of each contributing position invested in this asset. The final percentage is that source’s share of your total exposure to this asset.')
        render_list(frame_rows(sources), [ListColumn('Source', 'Source'), ListColumn('Account', 'Account'),
                    ListColumn('Exposure', 'Source type'), ListColumn('Position value', 'Position', numeric=True),
                    ListColumn('Asset weight (%)', 'Asset weight (%)', numeric=True),
                    ListColumn('Value', 'Contribution', numeric=True),
                    ListColumn('% of asset exposure', '% of asset exposure', numeric=True)],
                    key=f'{key}_sources', context=f'{key}_{asset}_sources', title='Exposure sources', default_sort='Value')
        source_ids = exposures.loc[exposures.asset_id.eq(asset), 'source_position_id']
        source_positions = selected.loc[selected.position_id.isin(source_ids)]
        relevant = {f.isin for position in source_positions.to_dict('records') if (f := matching_fund(position, funds)) is not None}
        render_fund_details([f for f in funds if f.isin in relevant], source_positions, holdings=holdings,
                            classifications=classifications, show_tickers=show_tickers)
        if st.button('Close exposure details'):
            dismiss()
            st.rerun()
    details()
