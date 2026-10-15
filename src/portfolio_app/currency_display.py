"""Session-local presentation units; calculations receive currency explicitly."""
import streamlit as st
from portfolio_app.money import currency_symbol as explicit_symbol, format_money


def reporting_currency():
    return st.session_state.get('reporting_currency', 'EUR')


def currency_symbol(currency=None):
    return explicit_symbol(currency or reporting_currency())


def money_label(label):
    return f'{label} ({reporting_currency()})'


def money(value, *, currency=None, signed=False, missing='—'):
    """Format using session units unless a fixed-currency model supplies its own."""
    return format_money(value, currency or reporting_currency(), signed=signed, missing=missing)


def money_format(*, signed=False):
    """Native grid format; keep values numeric for sorting and editing."""
    return currency_symbol() + (' %+.2f' if signed else ' %.2f')
