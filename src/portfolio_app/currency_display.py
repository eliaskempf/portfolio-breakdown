"""Session-local presentation units; calculations receive currency explicitly."""
import streamlit as st


def reporting_currency():
    return st.session_state.get('reporting_currency', 'EUR')


def currency_symbol():
    return {'EUR': '€', 'USD': '$', 'GBP': '£'}[reporting_currency()]


def money_label(label):
    return f'{label} ({reporting_currency()})'
