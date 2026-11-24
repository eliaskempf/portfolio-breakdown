"""Optional company view, separate from strategic allocation and tradable holdings."""

import streamlit as st

from portfolio_app.exposure.exposure_workspace import analytical_signature
from portfolio_app.exposure.stock_exposure import load_company_identities, stock_exposure
from portfolio_app.interface.shared.currency_display import money
from portfolio_app.interface.shared.detail_panels import render_detail_panel
from portfolio_app.interface.shared.input_cache import cached_input
from portfolio_app.portfolio.holdings import DataError


def render_stock_exposure(valued, funds, data_dir, *, query='', allocation=None, current_view=None,
                          contribution_key=None, identities=None):
    from portfolio_app.portfolio.strategic import category_labels
    labels = category_labels(allocation) if allocation else {}
    buckets = sorted(set(valued.get('bucket_id', [])) - {''})
    excluded = st.multiselect('Exclude source categories', buckets,
        format_func=lambda value: labels.get(value, value),
        help='Exclude source positions before fund look-through. Companies inside included funds remain included.', key='exposure_stock_excluded')
    st.caption('Company ownership · funded holdings', help='Direct shares and supported equity holdings inside funds. '
               'Company percentages use the selected stock universe; portfolio percentages use the source scope before these additional exclusions. '
               'This ownership view does not treat futures notional as funded capital.')
    try:
        if identities is None:
            identities = load_company_identities(data_dir / 'company-identities.yaml')
        if contribution_key is None:
            contribution_key = analytical_signature(valued, funds, str(data_dir.resolve()), identities)
        result = cached_input('stock_exposure', (contribution_key, frozenset(excluded)),
            lambda: stock_exposure(valued, funds, excluded_buckets=excluded, identities=identities), immutable=True)
    except DataError as exc:
        st.error(str(exc))
        return
    companies = result.companies
    if query.strip():
        companies = companies.loc[companies.astype(str).apply(lambda col: col.str.contains(query.strip(), case=False, regex=False)).any(axis=1)]
    st.dataframe(companies, hide_index=True, width='stretch', column_config={'Company ID': None, 'Whole-portfolio %': '% of selected portfolio'})
    if result.stock_value is None:
        st.caption('Partial company coverage · stock-universe percentages unavailable', help='Some values or composition are unresolved. Unknown exposure is not zero.')
    else:
        st.caption(f'Selected stock universe: {money(result.stock_value)}, including unresolved equity residuals. Portfolio percentages use the selected source scope before these additional exclusions.')
    if not result.unresolved.empty:
        render_detail_panel('Unresolved exposure and fund residuals', 'company_unresolved',
                            lambda: st.dataframe(result.unresolved, hide_index=True, width='stretch'), current=current_view)
    render_detail_panel('Company exposure by source', 'company_sources',
                        lambda: st.dataframe(result.sources, hide_index=True, width='stretch'), current=current_view)
    with st.expander('Company coverage & identity'):
        st.write('Instrument types and ETF snapshot composition determine coverage. Partial snapshots keep their actual weights. Different share classes and ADRs remain distinct unless explicitly mapped to a company.')
