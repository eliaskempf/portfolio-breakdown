"""Named liabilities retain unknown balances, identities and unrelated settings."""
from dataclasses import replace

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.holdings import DataError
from portfolio_app.leverage_settings import MarginLiability, LeverageSettings, FundFactor, load_leverage_settings, save_leverage_settings
from portfolio_app.liabilities_ui import update_liability
from test_ui import by_label


def test_update_one_liability_preserves_factors_and_checks_revision(tmp_path):
    debt = MarginLiability('old', 'Invented loan', None)
    factor = FundFactor('synthetic-fund', None)
    settings = LeverageSettings(liabilities=(debt,), fund_factors=(factor,), active_basis='exposure')
    save_leverage_settings(tmp_path, settings, None)
    _, revision = load_leverage_settings(tmp_path)
    update_liability(tmp_path, 'old', replace(debt, balance_eur=0), revision)
    updated, latest = load_leverage_settings(tmp_path)
    assert updated.liabilities[0].id == 'old' and updated.liabilities[0].balance_eur == 0
    assert updated.fund_factors == (factor,) and updated.active_basis == 'exposure'
    with pytest.raises(DataError, match='changed'):
        update_liability(tmp_path, 'old', None, revision)
    update_liability(tmp_path, 'old', None, latest)
    assert load_leverage_settings(tmp_path)[0].fund_factors == (factor,)


def test_named_form_adds_unknown_edits_zero_and_removes(tmp_path):
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.liabilities_ui import render_liabilities
render_liabilities(Path({str(tmp_path)!r}))
''').run()
    by_label(app.text_input, 'Liability name').set_value('Invented loan')
    by_label(app.text_input, 'Account or lender').set_value('Synthetic lender')
    by_label(app.button, 'Save liability').click().run()
    assert not app.exception and not app.error
    original = load_leverage_settings(tmp_path)[0].liabilities[0]
    assert original.balance_eur is None
    by_label(app.selectbox, 'Liability').set_value(original.id).run()
    by_label(app.text_input, 'Outstanding balance (EUR)').set_value('0')
    by_label(app.button, 'Save liability').click().run()
    updated = load_leverage_settings(tmp_path)[0].liabilities[0]
    assert updated.id == original.id and updated.balance_eur == 0
    by_label(app.button, 'Remove liability').click().run()
    assert app.error and load_leverage_settings(tmp_path)[0].liabilities
    by_label(app.checkbox, 'Remove this liability').check()
    by_label(app.button, 'Remove liability').click().run()
    assert not app.exception and not app.error
    assert not load_leverage_settings(tmp_path)[0].liabilities


def test_unavailable_legacy_fund_retained_until_explicit_removal(tmp_path):
    from test_leveraged_funds import invented_workspace
    invented_workspace(tmp_path)
    save_leverage_settings(tmp_path, LeverageSettings(fund_factors=(FundFactor('missing-fund', None),)), None)
    before = (tmp_path / 'leverage-settings.json').read_bytes()
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.positions import read_snapshot
from portfolio_app.leveraged_funds_ui import render_leveraged_funds
directory=Path({str(tmp_path)!r})
render_leveraged_funds(directory, read_snapshot(directory/'holdings.csv').holdings)
''').run()
    assert not app.exception and not app.error
    assert any('no longer match' in c.value for c in app.caption)
    assert (tmp_path / 'leverage-settings.json').read_bytes() == before
    by_label(app.button, 'Remove unavailable fund 1').click().run()
    assert not app.exception and not app.error
    assert not load_leverage_settings(tmp_path)[0].fund_factors
