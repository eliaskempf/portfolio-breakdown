"""Optional company view, separate from strategic allocation and tradable holdings."""

from portfolio_app.currency_display import money
import streamlit as st
from portfolio_app.holdings import DataError
from portfolio_app.stock_exposure import load_company_identities, stock_exposure


def render_stock_exposure(valued, funds, data_dir, *, query='', allocation=None):
    from portfolio_app.strategic import category_labels
    labels = category_labels(allocation) if allocation else {}
    buckets = sorted(set(valued.get('bucket_id', [])) - {''})
    excluded = st.multiselect('Exclude source buckets from stock exposure', buckets,
        format_func=lambda value: labels.get(value, value),
        help='Exclude source positions before fund look-through. Companies inside included funds remain included.', key='exposure_stock_excluded')
    st.caption('Company ownership · funded holdings', help='Direct shares and supported equity holdings inside funds. '
               'Company percentages use the selected stock universe; portfolio percentages use the source scope before these additional exclusions. '
               'This ownership view does not treat futures notional as funded capital.')
    try:
        result = stock_exposure(valued, funds, excluded_buckets=excluded,
                                identities=load_company_identities(data_dir / 'company-identities.yaml'))
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
        with st.expander('Unresolved exposure and fund residuals'):
            st.dataframe(result.unresolved, hide_index=True, width='stretch')
    with st.expander('Company exposure by source'):
        st.dataframe(result.sources, hide_index=True, width='stretch')
    with st.expander('Company coverage & identity'):
        st.write('Instrument types and ETF snapshot composition determine coverage. Partial snapshots keep their actual weights. Different share classes and ADRs remain distinct unless explicitly mapped to a company.')
