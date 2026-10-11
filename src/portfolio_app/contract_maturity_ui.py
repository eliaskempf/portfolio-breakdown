"""Source-listed exact maturities, with an explicit manual/history escape hatch."""
from datetime import date

import streamlit as st

from portfolio_app.derivative_market import CATALOG, discover_contracts
from portfolio_app.holdings import DataError


@st.cache_data(ttl=15 * 60, show_spinner=False)
def _listed_contract_result(product):
    """Cache public metadata and temporary failures, not holdings or executions."""
    try:
        return discover_contracts(product), ''
    except (DataError, OSError) as exc:
        return (), str(exc)


def listed_contracts(product):
    contracts, failure = _listed_contract_result(product)
    if failure:
        raise DataError(failure)
    return contracts


def maturity_options(contracts, *, after=None, today=None):
    """Do not invent a calendar or offer an earlier expiry for a roll."""
    today = today or date.today()
    return sorted({date.fromisoformat(contract.expiry) for contract in contracts
                   if date.fromisoformat(contract.expiry) >= today
                   and (after is None or contract.expiry > after)})


def maturity_input(product, *, key, label='Exact contract maturity', after=None):
    """Return an explicitly selected date; unknown/custom contracts stay manual."""
    if product not in CATALOG:
        return st.date_input(label, value=None, key=key+'manual',
            help='Enter the exact maturity from your contract or trade confirmation.')
    failure = ''
    try:
        dates = maturity_options(listed_contracts(product), after=after)
    except (DataError, OSError) as exc:
        dates, failure = [], str(exc)
    help_text = ('Maturities published by Eurex. A listing does not guarantee liquidity or an execution price. '
                 'Choose historical / manual entry for an expired contract or an unavailable listing.')
    if failure:
        help_text = 'Listed maturities unavailable. ' + failure + ' Enter the verified maturity manually.'
    elif not dates:
        help_text = 'No eligible listed maturities found. Enter a verified historical or manual maturity.'
    options = [day.isoformat() for day in dates] + ['manual']
    # Product and roll origin are part of the key so a previous contract's draft
    # cannot silently select a maturity for a different product or roll.
    selected = st.selectbox(label, options, index=None,
        format_func=lambda value: 'Historical / manual maturity…' if value == 'manual' else value,
        placeholder='Choose a listed maturity' if dates else 'Listed maturities unavailable — choose manual entry',
        key=key+product+'_'+str(after or '')+'_listed', help=help_text)
    if selected == 'manual':
        return st.date_input('Manual maturity', value=None, key=key+product+'_manual',
            help='Use the exact maturity from your contract or trade confirmation; this date is not source-verified.')
    return date.fromisoformat(selected) if selected else None
