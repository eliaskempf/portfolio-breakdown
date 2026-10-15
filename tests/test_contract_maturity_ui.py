"""Source maturity selection uses invented contract lists; no live network."""
from datetime import date

from streamlit.testing.v1 import AppTest

from portfolio_app import contract_maturity_ui as ui
from portfolio_app.derivative_market import make_contract
from portfolio_app.holdings import DataError
from test_ui import by_label


def launch(*, product='FMWN', after=None):
    return AppTest.from_string(f'''
import streamlit as st
from portfolio_app.contract_maturity_ui import maturity_input
value = maturity_input({product!r}, key='maturity_', after={after!r})
st.session_state['chosen'] = value
''').run()


def synthetic_contracts(product):
    return tuple(make_contract(product, expiry) for expiry in ('2027-03-19', '2027-06-18', '2027-09-17'))


def test_roll_options_keep_only_source_dates_later_than_current_contract():
    contracts = synthetic_contracts('FMWN')
    assert ui.maturity_options(contracts, after='2027-03-19', today=date(2026, 1, 1)) == [date(2027, 6, 18), date(2027, 9, 17)]
    assert ui.maturity_options(contracts, today=date(2027, 6, 19)) == [date(2027, 9, 17)]


def test_listed_maturity_needs_explicit_choice_and_returns_exact_source_date(monkeypatch):
    monkeypatch.setattr(ui, 'listed_contracts', synthetic_contracts)
    app = launch()
    assert not app.exception
    picker = by_label(app.selectbox, 'Exact contract maturity')
    assert picker.options == ['2027-03-19', '2027-06-18', '2027-09-17', 'Historical / manual maturity…']
    assert picker.value is None and app.session_state['chosen'] is None
    assert not app.date_input
    picker.select('2027-06-18').run()
    assert app.session_state['chosen'] == date(2027, 6, 18)
    assert not app.date_input


def test_roll_ui_excludes_existing_and_earlier_listed_expiries(monkeypatch):
    monkeypatch.setattr(ui, 'listed_contracts', synthetic_contracts)
    app = launch(after='2027-06-18')
    picker = by_label(app.selectbox, 'Exact contract maturity')
    assert picker.options == ['2027-09-17', 'Historical / manual maturity…']
    assert app.session_state['chosen'] is None


def test_source_failure_offers_manual_date_without_invented_default(monkeypatch):
    def unavailable(_):
        raise DataError('Invented source outage')
    monkeypatch.setattr(ui, 'listed_contracts', unavailable)
    app = launch()
    assert not app.exception
    picker = by_label(app.selectbox, 'Exact contract maturity')
    assert picker.options == ['Historical / manual maturity…']
    assert 'unavailable' in picker.proto.help
    assert not app.date_input
    picker.select('manual').run()
    manual = by_label(app.date_input, 'Manual maturity')
    assert manual.value is None
    manual.set_value(date(2025, 12, 19)).run()
    assert app.session_state['chosen'] == date(2025, 12, 19)


def test_custom_contract_remains_offline_and_manual(monkeypatch):
    def forbidden(_):
        raise AssertionError('Unknown products must not trigger discovery')
    monkeypatch.setattr(ui, 'listed_contracts', forbidden)
    app = launch(product='SYNTH')
    assert not app.exception
    assert not app.selectbox
    assert by_label(app.date_input, 'Exact contract maturity').value is None
