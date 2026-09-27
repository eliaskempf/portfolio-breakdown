from test_ui import position_action
"""Invented balances and costs; never read the working portfolio."""

import pandas as pd
import pytest

from portfolio_app.balances import patch_holdings, replacement_changes
from portfolio_app.costs import average_from_total
from portfolio_app.holdings import DataError, load_holdings
from portfolio_app.performance import position_performance
from portfolio_app.positions import read_snapshot
from test_instrument_ui import launch_editor
from test_ui import by_label


@pytest.mark.parametrize('total,quantity,expected', [
    (123.45, .03125, 3950.4), (1., .00000001, 100_000_000.),
    (0., .2, 0.), (0., 0., 0.), (None, 0., None), (float('nan'), .2, None),
])
def test_total_cost_conversion(total, quantity, expected):
    actual = average_from_total(total, quantity)
    assert actual is None if expected is None else actual == pytest.approx(expected)


@pytest.mark.parametrize('total,quantity', [(1., 0.), (-1., 2.), (float('inf'), 2.), (1., -1.), (1e308, 1e-10)])
def test_invalid_cost_conversion_is_rejected(total, quantity):
    with pytest.raises(DataError):
        average_from_total(total, quantity)


def total_mode(app):
    by_label(app.radio, 'Buy-in entry').set_value('Total buy-in').run()
    assert not app.exception


def test_add_fractional_position_with_total_cost_and_reopen(tmp_path):
    path = tmp_path / 'holdings.csv'
    app = launch_editor(path)
    total_mode(app)
    by_label(app.text_input, 'Instrument name').set_value('Invented token')
    by_label(app.selectbox, 'Instrument type').set_value('crypto')
    by_label(app.number_input, 'Quantity held (total)').set_value(.03125)
    by_label(app.number_input, 'Total buy-in (optional)').set_value(123.45)
    by_label(app.text_input, 'Buy-in currency').set_value('EUR')
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    stored = load_holdings(path)
    assert stored.acquisition_price.iloc[0] == pytest.approx(3950.4)
    assert stored.acquisition_currency.iloc[0] == 'EUR'
    performance = position_performance(stored.assign(quote_currency='EUR', current_price=4800., current_value_eur=150.))
    assert performance.cost_basis.iloc[0] == pytest.approx(123.45)
    assert performance.unrealized_gain.iloc[0] == pytest.approx(26.55)
    position_action(app, 'Edit position')
    total_mode(app)
    assert by_label(app.number_input, 'Total buy-in (optional)').value == pytest.approx(123.45)
    # Replacement quantity, same total cost: recompute the unit average.
    by_label(app.number_input, 'Quantity held (total)').set_value(.0625)
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    assert load_holdings(path).acquisition_price.iloc[0] == pytest.approx(1975.2)


@pytest.mark.parametrize('quantity,currency,message', [(0., 'EUR', 'quantity greater than zero'), (.25, '', 'currency')])
def test_invalid_total_does_not_create_position(tmp_path, quantity, currency, message):
    path = tmp_path / 'holdings.csv'
    app = launch_editor(path)
    total_mode(app)
    by_label(app.text_input, 'Instrument name').set_value('Invented token')
    by_label(app.number_input, 'Quantity held (total)').set_value(quantity)
    by_label(app.number_input, 'Total buy-in (optional)').set_value(80.)
    by_label(app.text_input, 'Buy-in currency').set_value(currency)
    by_label(app.button, 'Save position').click().run()
    assert not app.exception
    assert any(message in error.value for error in app.error)
    assert not path.exists()


def sample_balances(path):
    path.write_text('id,name,shares,acquisition_price,acquisition_currency,bucket_id,within_bucket_target\n'
                    'a,Invented Alpha,0.25,400,GBP,example,0.5\n'
                    'b,Invented Beta,0,25,EUR,example,0.3\n'
                    'c,Invented Gamma,1,,,example,0.2\n')


def test_bulk_conversion_preserves_planned_and_unknown_costs_and_targets(tmp_path):
    path = tmp_path / 'holdings.csv'
    sample_balances(path)
    snapshot = read_snapshot(path)
    rows = snapshot.holdings.assign(total_buy_in=lambda frame: frame.shares * frame.acquisition_price,
                                    holdings_confirmed_on='').drop(columns='acquisition_price')
    rows.loc[0, ['shares', 'total_buy_in']] = [.125, 75.]
    changes = replacement_changes(rows, snapshot.holdings, total_buy_in=True)
    patch_holdings(path, changes, expected_revision=snapshot.revision, replacement=True)
    stored = load_holdings(path)
    assert stored.shares.tolist() == [.125, 0., 1.]
    assert stored.acquisition_price.iloc[0] == 600.
    assert stored.acquisition_price.iloc[1] == 25.  # Preserve planned unit cost.
    assert pd.isna(stored.acquisition_price.iloc[2])
    assert stored.acquisition_currency.tolist() == ['GBP', 'EUR', '']
    assert stored.within_bucket_target.tolist() == [.5, .3, .2]
    assert stored.bucket_id.eq('example').all()
    snapshot = read_snapshot(path)
    patch_holdings(path, replacement_changes(rows, snapshot.holdings, total_buy_in=True),
                   expected_revision=snapshot.revision, replacement=True)
    assert load_holdings(path).acquisition_price.iloc[0] == 600.


def test_balance_editor_saves_total_cost_and_clears_missing_cost(monkeypatch, tmp_path):
    path = tmp_path / 'holdings.csv'
    sample_balances(path)
    app = launch_editor(path)
    position_action(app, 'Update balances')
    total_mode(app)

    def edit(frame, **kwargs):
        assert 'total_buy_in' in frame
        frame = frame.copy()
        frame.loc[0, ['shares', 'total_buy_in']] = [.125, 75.]
        frame.loc[1, 'total_buy_in'] = float('nan')
        return frame

    monkeypatch.setattr('portfolio_app.allocation_ui.st.data_editor', edit)
    by_label(app.button, 'Save replacement balances').click().run()
    assert not app.exception and not app.error
    stored = load_holdings(path)
    assert stored.acquisition_price.iloc[0] == 600.
    assert pd.isna(stored.acquisition_price.iloc[1])


def test_invalid_total_balance_update_is_atomic(monkeypatch, tmp_path):
    path = tmp_path / 'holdings.csv'
    sample_balances(path)
    before = path.read_bytes()
    app = launch_editor(path)
    position_action(app, 'Update balances')
    total_mode(app)

    def edit(frame, **kwargs):
        frame = frame.copy()
        frame.loc[0, 'total_buy_in'] = 80.
        frame.loc[1, 'total_buy_in'] = 10.  # Zero quantity cannot carry positive total cost.
        return frame

    monkeypatch.setattr('portfolio_app.allocation_ui.st.data_editor', edit)
    by_label(app.button, 'Save replacement balances').click().run()
    assert not app.exception
    assert any('Invented Beta' in error.value for error in app.error)
    assert path.read_bytes() == before
