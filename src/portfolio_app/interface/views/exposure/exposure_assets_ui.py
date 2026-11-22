"""One searchable exposure table, with on-demand source and fund details."""
from portfolio_app.interface.shared.chart_hover import chart_hover

from portfolio_app.interface.shared.currency_display import currency_symbol, money
from portfolio_app.interface.shared.input_cache import cached_input
from portfolio_app.interface.shared.ui_help import column_help
from hashlib import sha256
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from portfolio_app.interface.shared.charts import chart_config

from portfolio_app.interface.shared.charts import style_figure
from portfolio_app.funds.etf import matching_fund
from portfolio_app.interface.views.funds.etf_ui import render_fund_details
from portfolio_app.exposure.exposure_tables import asset_exposure_table, asset_source_amounts, exposure_sources
from portfolio_app.exposure.label_presentation import taxonomy_colors
from portfolio_app.interface.shared.list_ui import BOUNDED_LIST_HEIGHT, ListColumn, frame_rows, render_list
from portfolio_app.exposure.taxonomy import describe, taxonomy_names


def preview_facts(exposures, selected):
    """Numeric facts for local disclosure. Share each distinct source label across assets."""
    values = asset_source_amounts(exposures, selected).sort_values('Value', ascending=False, na_position='last')
    counts = values.asset_id.value_counts().to_dict()
    top = values.groupby('asset_id', sort=False).head(4)
    labels = top['Source'].fillna('Unavailable')
    prefix = top.Exposure.isin(('Direct', 'Future', 'Futures proxy'))
    labels.loc[prefix] = top.loc[prefix, 'Exposure'] + ' · ' + labels.loc[prefix]
    accounts = top.Account.fillna('').ne('')
    labels.loc[accounts] += ' · ' + top.loc[accounts, 'Account']
    codes, names = pd.factorize(labels)
    by_asset = {}
    for asset, code, amount, share in zip(top.asset_id, codes.tolist(), top.Value, top['% of asset exposure']):
        # Round once with the same precision as Python's monetary display. Formatting
        # and preview markup are deferred until the user expands a row.
        if asset not in by_asset:
            by_asset[asset] = [max(0, int(counts[asset]) - 4)]
        by_asset[asset].append([code, round(amount, 2) if pd.notna(amount) else None,
                               round(share, 2) if pd.notna(share) else None])
    return {'labels': names.tolist(), 'assets': by_asset}


def render_assets(exposures, selected, holdings, funds, classifications, *, query='', show_tickers=False, show_chart=False, complete=True, breakdown=False, context='exposure', footer='', geography=None, measure=None, equity=None, contribution_key):
    default = 'labels' if any('labels' in item for item in classifications.values()) else 'sector'
    titles = {'labels': 'Themes', 'sector': 'Sector', 'geography': 'Geography'}
    taxonomy = st.selectbox('Asset classifications', list(titles), help='Inspect the classifications used to aggregate this asset’s exposure.', index=list(titles).index(default),
                            format_func=titles.get, key='exposure_asset_classifications')
    def geographic_labels():
        labels = {asset: dict(entry) for asset, entry in classifications.items()}
        for asset, entry in geography.classifications(exposures.asset_id).items():
            labels.setdefault(asset, {}).update(entry)
        return labels

    def prepare_table():
        labels = geographic_labels() if geography is not None and taxonomy == 'geography' else classifications
        table = asset_exposure_table(exposures, classifications=labels, taxonomy=taxonomy, complete=complete, equity=equity)
        return table, taxonomy_colors(labels, taxonomy)

    table, colors = cached_input('exposure_assets_table', (contribution_key, taxonomy, complete, equity), prepare_table)
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
    columns = [ListColumn('Asset', 'Asset', help=column_help('Asset'), width=260)]
    if show_tickers:
        columns.append(ListColumn('Ticker', 'Ticker', help=column_help('Ticker'), width=110))
    columns += [ListColumn('Total', 'Total', help=column_help('Total'), numeric=True, width=140),
                ListColumn('Allocation %', '% of selected exposure' if measure == 'Market exposure' else '% of selected portfolio', help=column_help('Allocation %'), numeric=True, width=190),
                ListColumn('Sources', 'Sources', help=column_help('Sources'), width=190),
                ListColumn('Labels', titles[taxonomy], help=column_help('Labels'), badges=colors)]
    if measure == 'Market exposure':
        if 'Equity %' not in table:
            table['Equity %'] = float('nan')
        columns.insert(4 if show_tickers else 3, ListColumn('Equity %', '% of portfolio equity', numeric=True, width=170,
            help='Signed exposure / whole-portfolio net equity. Unavailable when equity or exposure is incomplete.'))
    if 'Known exposure' in table:
        columns.insert(3 if show_tickers else 2, ListColumn('Known exposure', 'Known exposure', numeric=True, width=150,
            help='Sum of resolved contributions only. An unavailable total can still have known contributions; the remainder is not zero.'))
    list_key = (contribution_key, taxonomy, complete, equity, query.strip().casefold())
    rows = cached_input('exposure_asset_rows', list_key, lambda: frame_rows(table, id_column='asset_id'), immutable=True)
    revision = sha256(repr(list_key).encode()).hexdigest()
    facts = cached_input('exposure_preview_facts', contribution_key,
                        lambda: preview_facts(exposures, selected), immutable=True)
    if query.strip():
        facts = dict(facts, assets={asset: facts['assets'][asset] for asset in table.asset_id})
    render_list(rows, columns, revision=revision, key=key, context=key, title='Exposure assets',
                on_open=select, max_height=BOUNDED_LIST_HEIGHT if breakdown else None, default_sort='Total',
                page_size=100 if len(table) > 200 else None, preview_column='Sources', preview_value_column='Total', preview_share_column='Allocation %', previews=dict(facts, symbol=currency_symbol()))
    if footer:
        st.caption(footer)
    if show_chart:
        chart_amount = 'Known exposure' if 'Known exposure' in table else 'Total'
        known = table.dropna(subset=[chart_amount])
        largest = known.loc[known[chart_amount].abs().nlargest(30).index].iloc[::-1]
        if chart_amount == 'Known exposure':
            st.caption('Chart shows known exposure only; unresolved contributions are excluded.')
        if len(known) > 30:
            st.caption('Chart: 30 largest absolute contributions. The table retains every asset and percentages use the full selection.')
        figure = style_figure(go.Figure(go.Bar(x=largest[chart_amount], y=largest.Asset, orientation='h')))
        figure.update_layout(height=max(260, 30 * len(largest)), xaxis_title='Exposure', margin=dict(t=12, b=20, l=12, r=12))
        st.plotly_chart(chart_hover(figure), width='stretch', config=chart_config())
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
        st.caption('Unknown values remain unavailable; they are not treated as zero.') if pd.isna(row['Total']) else st.metric('Total exposure', money(row["Total"]), help='Combined direct and indirect exposure to this asset in the portfolio currency.')
        labels = geographic_labels() if geography is not None else classifications
        for name in taxonomy_names(labels):
            st.write(f'{titles.get(name, name.replace("_", " ").title())}: {describe(labels, asset, name)}')
        if geography is not None:
            st.caption('Geography source: ' + geography.sources.get(asset, 'No local metadata'))
            if reason := geography.reasons.get(asset):
                st.caption(reason)
        sources = exposure_sources(exposures, selected, asset, measure=measure)
        st.caption('Asset weight uses each source’s selected measure, including signed notional for futures in market exposure. The final percentage is that source’s share of this asset’s total.')
        render_list(frame_rows(sources), [ListColumn('Source', 'Source', help=column_help('Source')), ListColumn('Account', 'Account', help=column_help('Account')),
                    ListColumn('Exposure', 'Source type', help=column_help('Exposure')), ListColumn('Position value', 'Source exposure' if measure == 'Market exposure' else 'Position', help=column_help('Position value'), numeric=True),
                    ListColumn('Asset weight (%)', 'Asset weight (%)', help=column_help('Asset weight (%)'), numeric=True),
                    ListColumn('Value', 'Contribution', help=column_help('Value'), numeric=True),
                    ListColumn('% of asset exposure', '% of asset exposure', help=column_help('% of asset exposure'), numeric=True)],
                    key=f'{key}_sources', context=f'{key}_{asset}_sources', title='Exposure sources', default_sort='Value')
        if 'method' in exposures:
            provenance = exposures.loc[exposures.asset_id.eq(asset) & exposures.source_type.eq('future'), ['method', 'as_of', 'status']].drop_duplicates()
            for row in provenance.fillna('').itertuples():
                st.caption(' · '.join(str(x) for x in (row.method, row.as_of, row.status) if x))
        source_ids = exposures.loc[exposures.asset_id.eq(asset), 'source_position_id']
        source_positions = selected.loc[selected.position_id.isin(source_ids)]
        relevant = {f.isin for position in source_positions.to_dict('records') if (f := matching_fund(position, funds)) is not None}
        render_fund_details([f for f in funds if f.isin in relevant], source_positions, holdings=holdings,
                            classifications=labels, show_tickers=show_tickers)
        if st.button('Close exposure details', help='Return to the exposure overview.'):
            dismiss()
            st.rerun()
    details()
