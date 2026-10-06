"""Currency flows in the release interface, with invented workspaces only."""
import pytest
from portfolio_app.demo import create_demo_data
from portfolio_app.portfolio_settings import load_settings, save_settings
from portfolio_app.positions import read_snapshot
from portfolio_app.cost_basis import active_components, resolve_cost
from test_startup import launch_workspaces
from test_ui import by_label, launch, position_action, activate
from test_onboarding_ui import add_category


@pytest.mark.parametrize('currency', ['EUR', 'USD', 'GBP'])
def test_manual_setup_skip_persists_currency_and_default(tmp_path, currency):
    personal = tmp_path / 'invented'
    demo = create_demo_data(tmp_path / 'demo')
    app = launch_workspaces(personal, demo)
    by_label(app.button, 'Start my portfolio').click().run()
    app.selectbox(key='onboarding_currency').set_value(currency).run()
    by_label(app.button, 'Skip setup').click().run()
    assert not app.exception
    assert load_settings(personal).reporting_currency == currency
    by_label(app.button, 'Not now').click().run()
    assert app.selectbox(key='currency_setting_choice').value == currency
    activate(app, 'Positions')
    by_label(app.button, 'Add position').click().run()
    assert by_label(app.selectbox, 'Buy-in currency').value == currency
    by_label(app.button, 'Cancel').click().run()
    assert load_settings(personal).reporting_currency == currency


def test_review_categories_preserves_currency_through_modal_unmount(tmp_path):
    personal = tmp_path / 'invented'
    app = launch_workspaces(personal, create_demo_data(tmp_path / 'demo'))
    by_label(app.button, 'Start my portfolio').click().run()
    app.selectbox(key='onboarding_currency').set_value('GBP').run()
    add_category(app, 'Invented core', 100)
    app.run()
    assert any('Portfolio currency: GBP' in item.value for item in app.caption)
    by_label(app.button, 'Continue to first position').click().run()
    assert not app.exception
    assert load_settings(personal).reporting_currency == 'GBP'
    assert by_label(app.selectbox, 'Buy-in currency').value == 'GBP'
    by_label(app.button, 'Finish later').click().run()
    assert load_settings(personal).reporting_currency == 'GBP'


def change_review(app, target):
    app.selectbox(key='currency_setting_choice').set_value(target).run()
    by_label(app.button, 'Review currency change').click().run()
    assert not app.exception


def test_currency_change_cancel_then_exclusions_and_roundtrip(tmp_path):
    directory = create_demo_data(tmp_path / 'demo')
    original = (directory / 'holdings.csv').read_bytes()
    app = launch(directory, 'Overview')
    change_review(app, 'USD')
    assert any('excluded from gains' in warning.value for warning in app.warning)
    by_label(app.button, 'Cancel currency change').click().run()
    assert load_settings(directory).reporting_currency == 'EUR'
    change_review(app, 'USD')
    by_label(app.button, 'Apply currency change').click().run()
    assert not app.exception
    assert app.metric[0].value == '$103,538.17'
    assert (directory / 'holdings.csv').read_bytes() == original
    for tab in ['Positions', 'Exposure', 'Rebalance', 'Overview']:
        activate(app, tab)
        assert not app.exception
    change_review(app, 'EUR')
    by_label(app.button, 'Apply currency change').click().run()
    assert app.metric[0].value == '€93,184.35'
    assert (directory / 'holdings.csv').read_bytes() == original


def test_currency_estimates_need_review_and_confirmation(tmp_path):
    directory = create_demo_data(tmp_path / 'demo')
    app = launch(directory, 'Overview')
    change_review(app, 'USD')
    app.multiselect(key='currency_estimate_selection').set_value(['position-0']).run()
    assert by_label(app.button, 'Apply currency change').disabled
    by_label(app.button, 'Review FX estimates').click().run()
    assert not app.exception
    assert by_label(app.button, 'Apply currency change').disabled
    by_label(app.checkbox, 'Use these FX estimates for the selected positions').check().run()
    by_label(app.button, 'Apply currency change').click().run()
    assert not app.exception
    assert load_settings(directory).reporting_currency == 'USD'
    row = read_snapshot(directory / 'holdings.csv').holdings.iloc[0]
    assert resolve_cost(active_components(row), 'USD').estimated
    assert any('confirmed FX estimates' in w.value for w in app.warning)
    restarted = launch(directory, 'Overview')
    assert any('confirmed FX estimates' in w.value for w in restarted.warning)


def test_foreign_buy_in_controls_supplied_rate_and_restart(tmp_path):
    directory = create_demo_data(tmp_path / 'demo')
    save_settings(directory, 'GBP', None)
    app = launch(directory, 'Positions')
    position_action(app, 'Add position')
    by_label(app.button, 'Enter manually').click().run()
    by_label(app.text_input, 'Instrument name').set_value('Invented currency test')
    by_label(app.text_input, 'Quantity held (total)').set_value('2')
    by_label(app.text_input, 'Average buy-in per unit (optional)').set_value('100')
    by_label(app.selectbox, 'Buy-in currency').set_value('USD').run()
    assert any('excluded from gains' in w.value for w in app.warning)
    by_label(app.selectbox, 'Purchase conversion').set_value('Exchange rate').run()
    by_label(app.number_input, '1 USD in GBP').set_value(.75).run()
    by_label(app.button, 'Save position').click().run()
    assert not app.exception
    row = read_snapshot(directory / 'holdings.csv').holdings.iloc[-1]
    assert resolve_cost(active_components(row), 'GBP').amount == 150
    assert row.acquisition_currency == 'USD' and row.acquisition_price == 100


@pytest.mark.parametrize('target,rate,expected', [('USD', '1.2', 300), ('GBP', '0.8', 200)])
def test_linked_total_cost_conversion_and_metadata_edit_preserve_evidence(tmp_path, target, rate, expected):
    directory = create_demo_data(tmp_path / 'demo')
    save_settings(directory, target, None)
    app = launch(directory, 'Positions')
    position_action(app, 'Add position')
    by_label(app.button, 'Enter manually').click().run()
    by_label(app.text_input, 'Instrument name').set_value('Invented linked cost')
    by_label(app.text_input, 'Quantity held (total)').set_value('2.5').run()
    by_label(app.text_input, 'Total buy-in (optional)').set_value('250').run()
    by_label(app.selectbox, 'Buy-in currency').set_value('EUR').run()
    by_label(app.selectbox, 'Purchase conversion').set_value('Exchange rate').run()
    by_label(app.number_input, f'1 EUR in {target}').set_value(float(rate)).run()
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error, [item.value for item in app.error]
    row = read_snapshot(directory / 'holdings.csv').holdings.iloc[-1]
    parts = active_components(row)
    assert resolve_cost(parts, target).amount == expected
    assert row.shares == 2.5 and row.acquisition_price == 100
    # Open this exact synthetic position through the same editor request state.
    app.session_state['position_edit_selected'] = row.position_id
    app.session_state['position_edit_action'] = 'Edit position'
    app.session_state['position_edit_dialog'] = True
    app.run()
    by_label(app.text_input, 'Instrument name').set_value('Invented renamed cost').run()
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error, [item.value for item in app.error]
    saved = read_snapshot(directory / 'holdings.csv').holdings.iloc[-1]
    assert active_components(saved) == parts


@pytest.mark.parametrize('rate,expected', [('0.75', 225), ('', None)])
def test_optional_purchase_rows_use_reporting_currency_and_keep_originals(monkeypatch, tmp_path, rate, expected):
    from test_position_entry import mock_rows
    directory = create_demo_data(tmp_path / 'demo')
    save_settings(directory, 'GBP', None)
    app = launch(directory, 'Positions')
    position_action(app, 'Add position')
    by_label(app.button, 'Enter manually').click().run()
    by_label(app.text_input, 'Instrument name').set_value('Invented converted purchases')
    by_label(app.selectbox, 'Buy-in currency').set_value('USD').run()
    mock_rows(monkeypatch, [dict(date='', shares='2.5', price='100', fees='0', fx_rate=rate),
                            dict(date='', shares='0.5', price='100', fees='0', fx_rate=rate)])
    by_label(app.checkbox, 'Calculate from purchases').check().run()
    assert by_label(app.text_input, 'Total buy-in (optional)').value == '300'
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error, [item.value for item in app.error]
    row = read_snapshot(directory / 'holdings.csv').holdings.iloc[-1]
    parts = active_components(row)
    assert len(parts) == 2
    assert all(part['currency'] == 'USD' for part in parts)
    assert resolve_cost(parts, 'GBP').amount == expected
    assert row.shares == 3 and row.acquisition_price == 100
