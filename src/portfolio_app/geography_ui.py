"""Region/country exploration over the same normalized portfolio exposures."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio_app.charts import style_figure
from portfolio_app.geography import (
    COUNTRY_UNSPECIFIED, SPECIAL, UNKNOWN, Geography, geography_allocations, geography_table,
)


def render_geography(exposures: pd.DataFrame, geography: Geography, *, complete: bool, query: str = ''):
    st.caption('Company country from local classifications and ETF metadata; this does not describe revenue by region. '
               'Europe includes the UK. Gold, crypto, cash and money market are shown separately.')
    allocations = geography_allocations(exposures, geography)
    total = exposures.value.sum()
    unknown = allocations.loc[allocations.path.map(lambda p: p[0] == UNKNOWN), 'value'].sum()
    regional_only = allocations.loc[allocations.path.map(lambda p: p[-1] == COUNTRY_UNSPECIFIED), 'value'].sum()
    special = allocations.loc[allocations.path.map(lambda p: p[0] in SPECIAL), 'value'].sum()
    country_value = total - unknown - regional_only - special
    if total > 0:
        st.caption(f'Country coverage: {country_value / total:.1%} of {"selected" if complete else "priced"} value · '
                   f'Non-geographic: €{special:,.2f} · Region only: €{regional_only:,.2f} · Unknown geography: €{unknown:,.2f}')
    if not complete:
        st.caption('Amounts include known values only; full-portfolio percentages are unavailable while prices are missing.')
    if query.strip():
        terms = exposures[['asset_name', 'ticker']].fillna('').agg(' '.join, axis=1).str.casefold()
        matching = set(exposures.loc[terms.str.contains(query.strip().casefold(), regex=False), 'asset_id'])
        allocations = allocations.loc[allocations.asset_id.isin(matching)]
        st.caption('Search keeps percentages relative to the selected portfolio.')
    if allocations.empty:
        st.info('No assets match this search.')
        return
    level_col, detail_col = st.columns([1, 2])
    level = level_col.segmented_control('Geography granularity', ['Regions', 'Countries'], default='Regions',
                                       key='geography_level') or 'Regions'
    paths = sorted({p[:i] for p in allocations.path for i in range(1, len(p) + 1)})
    if st.session_state.get('geography_detail', ()) not in [(), *paths]:
        st.session_state['geography_detail'] = ()
    root = detail_col.selectbox('Geography detail', [(), *paths],
                               format_func=lambda p: 'Entire selection' if not p else ' › '.join(p),
                               key='geography_detail')
    if root:
        st.button('Back to geography overview', on_click=lambda: st.session_state.update(geography_detail=()))
    rows = allocations.loc[allocations.path.map(lambda p: p[:len(root)] == root)]
    assets = bool(root) and (len(root) == 2 or root[0] in SPECIAL | {UNKNOWN})
    if assets:
        grouped = rows.groupby(['asset_id', 'asset_name'], sort=False).value
        table = grouped.sum(min_count=1).rename('EUR value').to_frame()
        table['Missing valuations'] = grouped.size() - grouped.count()
        table = table.reset_index().rename(columns={'asset_name': 'Asset'})
        table['% of selected portfolio'] = 100 * table['EUR value'] / total if complete and total > 0 else float('nan')
        table['Source'] = table.asset_id.map(geography.sources).fillna('No local metadata')
        table['Classification note'] = table.asset_id.map(geography.reasons).fillna('')
        residuals = set(exposures.loc[exposures.source_type.eq('etf_other'), 'asset_id'])
        table.loc[table.asset_id.isin(residuals), 'Classification note'] = 'Unresolved ETF residual'
        table = table.drop(columns='asset_id').sort_values(['EUR value', 'Asset'], ascending=[False, True], na_position='last')
        category = 'Asset'
    else:
        table = geography_table(allocations, level='Countries' if root else level, root=root,
                                denominator=total, complete=complete)
        category = 'Category'
    chart_area, table_area = st.columns([1, 1.3], gap='large')
    plotted = table.loc[table['EUR value'].notna() & table['EUR value'].gt(0)].iloc[::-1]
    if not plotted.empty:
        figure = style_figure(go.Figure(go.Bar(x=plotted['EUR value'], y=plotted[category], orientation='h',
                                              hovertemplate='%{y}<br>€%{x:,.2f}<extra></extra>')))
        figure.update_layout(height=min(900, max(280, len(plotted) * 28)), xaxis_title='Exposure (EUR)',
                             margin=dict(t=12, b=20, l=12, r=12))
        chart_area.plotly_chart(figure, width='stretch', config={'displayModeBar': False})
    else:
        chart_area.info('No positive priced exposure in this selection.')
    if not table['Missing valuations'].any():
        table = table.drop(columns='Missing valuations')
    table_area.dataframe(table, hide_index=True, width='stretch', column_config={
        'EUR value': st.column_config.NumberColumn('EUR value' if complete else 'Known EUR value', format='€ %.2f'),
        '% of selected portfolio': st.column_config.NumberColumn(format='%.2f %%'),
    })
