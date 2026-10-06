"""Optional company view, separate from strategic allocation and tradable holdings."""

from portfolio_app.currency_display import currency_symbol
import streamlit as st
from portfolio_app.holdings import DataError
from portfolio_app.stock_exposure import load_company_identities, stock_exposure


def render_stock_exposure(valued, funds, data_dir):
    if not st.checkbox('Show stock-only company exposure', help='Show direct shares and supported equity holdings inside funds, excluding non-equity exposure.', key='exposure_stock_enabled'):
        return
    buckets = sorted(set(valued.get('bucket_id', [])) - {''})
    excluded = st.multiselect('Exclude source buckets from stock exposure', buckets, help='Omit positions owned by these categories before calculating company exposure.', key='exposure_stock_excluded')
    st.caption('Exclusions apply to source positions before looking through funds. Companies held through included funds remain visible regardless of their theme labels.')
    try:
        result = stock_exposure(valued, funds, excluded_buckets=excluded,
                                identities=load_company_identities(data_dir / 'company-identities.yaml'))
    except DataError as exc:
        st.error(str(exc))
        return
    st.dataframe(result.companies, hide_index=True, width='stretch', column_config={'Company ID': None, 'Whole-portfolio %': '% of selected portfolio'})
    if result.stock_value is None:
        st.info('The selected stock-universe total is unknown because some values or composition are unresolved. Its percentages are blank; unknown exposure is not zero.')
    else:
        st.caption((f'Selected stock universe: €{result.stock_value:,.2f}, including unresolved equity residuals. Portfolio percentages use the selected source scope before these additional exclusions.').replace('€', currency_symbol()))
    if not result.unresolved.empty:
        st.subheader('Unresolved exposure and fund residuals')
        st.dataframe(result.unresolved, hide_index=True, width='stretch')
    with st.expander('Company exposure by source'):
        st.dataframe(result.sources, hide_index=True, width='stretch')
    st.caption('Instrument types and ETF snapshot composition determine coverage. Partial snapshots keep their actual weights. Different share classes and ADRs remain distinct unless explicitly mapped to a company.')
