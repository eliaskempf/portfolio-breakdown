"""Only explicitly invented positions and purchase inputs in temporary workspaces."""
from decimal import Decimal
import json

import pandas as pd
import pytest

from portfolio_app.holdings import DataError, load_holdings
from portfolio_app.position_entry import compact_decimal, decimal_input, linked_costs
from test_instrument_ui import launch_editor
from test_ui import by_label, position_action


@pytest.mark.parametrize('text,expected', [('12,5', '12.5'), ('.125', '0.125'), ('0', '0'), ('0.000000000123456789', '0.000000000123456789')])
def test_decimal_precision_and_display(text, expected):
    amount = decimal_input(text, 'Quantity')
    assert amount == Decimal(expected)
    assert compact_decimal(amount) == expected


@pytest.mark.parametrize('text', ['', '-1', 'NaN', 'inf', '1,000.5', '1e3', '1000000000000000001'])
def test_invalid_decimal_rejected(text):
    with pytest.raises(DataError):
        decimal_input(text, 'Quantity')


def test_optional_blank_and_cost_authority():
    assert decimal_input('', 'Cost', optional=True) is None
    assert linked_costs(Decimal(3), Decimal(10), 'average') == (Decimal(10), Decimal(30))
    assert linked_costs(Decimal(4), Decimal(30), 'total') == (Decimal('7.5'), Decimal(30))
    assert linked_costs(Decimal(0), Decimal(10), 'average') == (Decimal(10), Decimal(0))
    assert linked_costs(None, Decimal(30), 'total') == (None, Decimal(30))
    with pytest.raises(DataError, match='greater than zero'):
        linked_costs(Decimal(0), Decimal(30), 'total')


def test_linked_costs_switch_authority_clear_and_validation(tmp_path):
    app = launch_editor(tmp_path / 'holdings.csv', manual=True)
    field = lambda label: by_label(app.text_input, label)
    assert field('Quantity held (total)').value == ''
    field('Quantity held (total)').set_value('2,5').run()
    field('Average buy-in per unit (optional)').set_value('12.34').run()
    assert field('Total buy-in (optional)').value == '30.85'
    field('Quantity held (total)').set_value('5').run()
    assert field('Total buy-in (optional)').value == '61.7'
    field('Total buy-in (optional)').set_value('100').run()
    assert field('Average buy-in per unit (optional)').value == '20'
    field('Quantity held (total)').set_value('4').run()
    assert field('Average buy-in per unit (optional)').value == '25'
    field('Total buy-in (optional)').set_value('bad').run()
    assert any('nonnegative decimal' in error.value for error in app.error)
    by_label(app.button, 'Clear buy-in').click().run()
    assert field('Average buy-in per unit (optional)').value == ''
    assert field('Total buy-in (optional)').value == ''
    assert not app.exception


def test_unchanged_edit_retains_exact_csv_decimal_and_unlabelled_cost(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,shares,acquisition_price\na,Invented precision,1.234567890123456789,2.34567890123456789\n')
    app = launch_editor(path, action='Edit position')
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    row = pd.read_csv(path, dtype=str, keep_default_na=False).iloc[0]
    assert row.shares == '1.234567890123456789'
    assert row.acquisition_price == '2.34567890123456789'
    assert row.acquisition_currency == ''


def mock_rows(monkeypatch, rows):
    def editor(data, **kwargs):
        return pd.DataFrame(rows)
    monkeypatch.setattr('portfolio_app.position_entry_ui.st.data_editor', editor)


def test_purchase_mode_retains_manual_draft_and_saves_metadata_atomically(monkeypatch, tmp_path):
    path = tmp_path / 'holdings.csv'
    app = launch_editor(path, manual=True)
    by_label(app.text_input, 'Instrument name').set_value('Invented purchased holding')
    by_label(app.text_input, 'Account / broker').set_value('Synthetic account')
    by_label(app.number_input, 'Target allocation % (optional)').set_value(25.)
    by_label(app.text_input, 'Quantity held (total)').set_value('7').run()
    by_label(app.text_input, 'Average buy-in per unit (optional)').set_value('50').run()
    rows = [dict(date='2026-01-01', shares='2', price='100', fees='1'),
            dict(date='2026-02-01', shares='3', price='120', fees='2')]
    mock_rows(monkeypatch, rows)
    by_label(app.checkbox, 'Calculate from purchases').check().run()
    assert by_label(app.text_input, 'Quantity held (total)').value == '5'
    assert by_label(app.text_input, 'Quantity held (total)').disabled
    assert by_label(app.text_input, 'Total buy-in (optional)').value == '563'
    by_label(app.checkbox, 'Calculate from purchases').uncheck().run()
    assert by_label(app.text_input, 'Quantity held (total)').value == '7'
    assert by_label(app.text_input, 'Average buy-in per unit (optional)').value == '50'
    by_label(app.checkbox, 'Calculate from purchases').check().run()
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    saved = load_holdings(path).iloc[0]
    assert saved.shares == 5 and saved.acquisition_price == pytest.approx(112.6)
    assert saved.account == 'Synthetic account' and saved.target_allocation == .25
    assert len(json.loads(saved.purchase_history)[0]['purchases']) == 2
    assert len(load_holdings(path)) == 1


@pytest.mark.parametrize('price,shares,should_save', [('', '2', True), ('10', '-1', False)])
def test_purchase_unknown_cost_and_invalid_quantity(monkeypatch, tmp_path, price, shares, should_save):
    path = tmp_path / 'holdings.csv'
    app = launch_editor(path, manual=True)
    by_label(app.text_input, 'Instrument name').set_value('Invented purchase')
    mock_rows(monkeypatch, [dict(date='', shares=shares, price=price, fees='')])
    by_label(app.checkbox, 'Calculate from purchases').check().run()
    by_label(app.button, 'Save position').click().run()
    assert not app.exception
    assert path.exists() == should_save
    if should_save:
        saved = load_holdings(path).iloc[0]
        assert pd.isna(saved.acquisition_price)
        assert json.loads(saved.purchase_history)[0]['purchases'][0]['price'] == ''
    else:
        assert app.error


def test_purchase_save_rejects_stale_draft(monkeypatch, tmp_path):
    path = tmp_path / 'holdings.csv'
    app = launch_editor(path, manual=True)
    by_label(app.text_input, 'Instrument name').set_value('Invented stale purchase')
    mock_rows(monkeypatch, [dict(date='', shares='2', price='10', fees='')])
    by_label(app.checkbox, 'Calculate from purchases').check().run()
    external = 'id,name,shares\nexternal,Invented external change,3\n'
    path.write_text(external)
    by_label(app.button, 'Save position').click().run()
    assert path.read_text() == external
    assert any('Holdings changed' in item.value for item in app.warning)


def test_new_position_does_not_inherit_purchase_draft(monkeypatch, tmp_path):
    app = launch_editor(tmp_path / 'holdings.csv', manual=True)
    mock_rows(monkeypatch, [dict(date='', shares='2', price='10', fees='')])
    by_label(app.checkbox, 'Calculate from purchases').check().run()
    by_label(app.button, 'Cancel').click().run()
    position_action(app, 'Add position')
    assert not by_label(app.checkbox, 'Calculate from purchases').value
    assert by_label(app.text_input, 'Quantity held (total)').value == ''
    assert not app.session_state['view_editor_drafts']


def test_new_buy_in_only_offers_eur_and_saves(tmp_path):
    currency = 'EUR'
    path = tmp_path / 'holdings.csv'
    app = launch_editor(path, manual=True)
    assert by_label(app.selectbox, 'Buy-in currency').value == 'EUR'
    assert not any(item.label == 'Buy-in currency' for item in app.text_input)
    by_label(app.text_input, 'Instrument name').set_value('Invented foreign cost')
    by_label(app.text_input, 'Quantity held (total)').set_value('2').run()
    by_label(app.text_input, 'Average buy-in per unit (optional)').set_value('12.5').run()
    assert by_label(app.selectbox, 'Buy-in currency').options == ['EUR']
    assert by_label(app.text_input, 'Average buy-in per unit (optional)').value == '12.5'
    assert by_label(app.text_input, 'Total buy-in (optional)').value == '25'
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    saved = load_holdings(path).iloc[0]
    assert saved.acquisition_currency == currency
    assert saved.acquisition_price == 12.5


@pytest.mark.parametrize('currency', ['GBP', 'ISK', ''])
def test_existing_cost_currency_is_preserved_even_outside_picker_defaults(tmp_path, currency):
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,shares,acquisition_price,acquisition_currency\n'
                    f'a,Invented historical holding,2,12.5,{currency}\n')
    app = launch_editor(path, action='Edit position')
    assert by_label(app.selectbox, 'Buy-in currency').value == currency
    if currency:
        assert by_label(app.selectbox, 'Buy-in currency').disabled
        assert any('excluded from portfolio EUR gain' in item.value for item in app.caption)
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    saved = load_holdings(path).iloc[0]
    assert saved.acquisition_currency == currency
    assert saved.acquisition_price == 12.5
