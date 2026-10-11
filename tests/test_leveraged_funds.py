"""Synthetic holdings and manually invented factors, without network access."""
import json

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.holdings import DataError
from portfolio_app.leverage_settings import (FundFactor, LeverageSettings, MarginLiability,
    load_leverage_settings, save_leverage_settings, requires_legacy_eur)
from portfolio_app.leveraged_funds_ui import update_fund_factor
from portfolio_app.positions import read_snapshot


def invented_workspace(directory, currency='EUR'):
    (directory / 'portfolio.yaml').write_text(f'version: 1\nreporting_currency: {currency}\n')
    (directory / 'holdings.csv').write_text(
        'id,name,shares,instrument_type,position_key,account\n'
        'invented-fund,Invented fund,10,etf,fund,Synthetic\n'
        'invented-proxy,Invented benchmark fund,5,etf,proxy,Synthetic\n'
        'invented-stock,Invented stock,1,equity,stock,Synthetic\n')
    return read_snapshot(directory / 'holdings.csv')


@pytest.mark.parametrize('currency', ['EUR', 'USD', 'GBP'])
@pytest.mark.parametrize('factor', [-2., 2., None])
def test_currency_independent_signed_unknown_factors_roundtrip(tmp_path, currency, factor):
    invented_workspace(tmp_path, currency)
    settings = LeverageSettings(fund_factors=(FundFactor('fund', factor, 'Invented issuer specification', '2001-01-02',
        benchmark='Invented benchmark', proxy_position_id='proxy', reset_convention='daily'),))
    save_leverage_settings(tmp_path, settings, None)
    loaded, _ = load_leverage_settings(tmp_path)
    assert loaded == settings and loaded.enabled
    assert not requires_legacy_eur(loaded)
    assert not (tmp_path / 'account-journal.json').exists()


def test_legacy_liabilities_still_require_eur(tmp_path):
    invented_workspace(tmp_path, 'USD')
    with pytest.raises(DataError, match='EUR'):
        save_leverage_settings(tmp_path, LeverageSettings(liabilities=(MarginLiability('loan', 'Invented debt', 100),)), None)
    assert not (tmp_path / 'leverage-settings.json').exists()


def test_legacy_factor_payload_loads_without_migration_writes(tmp_path):
    raw = json.dumps({'version': 1, 'liabilities': [], 'fund_factors': [
        {'position_id': 'fund', 'factor': -1, 'method': 'Invented method', 'as_of': '2001-01-02'}]})
    path = tmp_path / 'leverage-settings.json'
    path.write_text(raw)
    settings, _ = load_leverage_settings(tmp_path)
    assert settings.fund_factors[0].reset_convention == 'daily'
    assert settings.fund_factors[0].benchmark == ''
    assert path.read_text() == raw


def test_factor_validation_requires_provenance_date_and_finite_signed_value():
    for factor in (float('nan'), float('inf'), True):
        with pytest.raises(DataError):
            FundFactor('fund', factor, 'Invented', '2001-01-02')
    with pytest.raises(DataError):
        FundFactor('fund', 2, '', '2001-01-02')
    with pytest.raises(DataError):
        FundFactor('fund', 2, 'Invented', '2999-01-02')
    with pytest.raises(DataError):
        FundFactor('fund', 2, 'Invented', '2001-01-02', proxy_position_id='fund')
    with pytest.raises(DataError):
        FundFactor('fund', 2, 'Invented', '2001-01-02', reset_convention='weekly')


def test_single_factor_save_freezes_keys_and_preserves_other_settings(tmp_path):
    snapshot = invented_workspace(tmp_path)
    debt = MarginLiability('loan', 'Invented debt', 20)
    save_leverage_settings(tmp_path, LeverageSettings(liabilities=(debt,), active_basis='exposure'), None)
    _, revision = load_leverage_settings(tmp_path)
    calls = []
    factor = FundFactor('fund', -2, 'Invented issuer', '2001-01-02', proxy_position_id='proxy')
    update_fund_factor(tmp_path, 'fund', factor, settings_revision=revision, holdings_revision=snapshot.revision,
                       freeze_keys=lambda directory, expected: calls.append((directory, expected)))
    saved, revision = load_leverage_settings(tmp_path)
    assert calls == [(tmp_path, snapshot.revision)]
    assert saved.liabilities == (debt,) and saved.active_basis == 'exposure' and saved.fund_factors == (factor,)
    update_fund_factor(tmp_path, 'fund', None, settings_revision=revision, holdings_revision=snapshot.revision)
    assert load_leverage_settings(tmp_path)[0].liabilities == (debt,)
    assert load_leverage_settings(tmp_path)[0].fund_factors == ()


def test_stale_or_invalid_fund_save_never_freezes_keys(tmp_path):
    snapshot = invented_workspace(tmp_path)
    def forbidden(*_):
        pytest.fail('Do not freeze holdings before validation')
    for selected, factor, revision in [
        ('fund', FundFactor('fund', None), 'stale'),
        ('stock', FundFactor('stock', None), snapshot.revision),
        ('fund', FundFactor('other', None), snapshot.revision),
        ('fund', FundFactor('fund', None, proxy_position_id='stock'), snapshot.revision),
    ]:
        with pytest.raises(DataError):
            update_fund_factor(tmp_path, selected, factor, settings_revision=None, holdings_revision=revision, freeze_keys=forbidden)
    assert not (tmp_path / 'leverage-settings.json').exists()


def test_renderer_read_only_with_unknown_and_short_factor(tmp_path):
    invented_workspace(tmp_path, 'USD')
    save_leverage_settings(tmp_path, LeverageSettings(fund_factors=(FundFactor('fund', None, as_of='2001-01-02'),
        FundFactor('proxy', -1., 'Invented issuer', '2001-01-02'))), None)
    before = {p: p.read_bytes() for p in tmp_path.glob('*') if p.is_file()}
    app = AppTest.from_string('''
from pathlib import Path
from portfolio_app.positions import read_snapshot
from portfolio_app.leveraged_funds_ui import render_leveraged_funds
directory = Path(__import__('os').environ['SYNTHETIC_FUND_WORKSPACE'])
render_leveraged_funds(directory, read_snapshot(directory / 'holdings.csv').holdings)
''')
    import os
    from unittest.mock import patch
    with patch.dict(os.environ, {'SYNTHETIC_FUND_WORKSPACE': str(tmp_path)}):
        app.run()
        assert not app.exception
        assert app.radio[0].value == 'Unknown'
        app.selectbox[0].select('proxy').run()
        assert not app.exception
        assert app.text_input[0].value == '-1.0'
    assert {p: p.read_bytes() for p in tmp_path.glob('*') if p.is_file()} == before


def test_renderer_saves_inverse_factor_in_gbp_with_source_and_reference(tmp_path):
    invented_workspace(tmp_path, 'GBP')
    app = AppTest.from_string('''
from pathlib import Path
from portfolio_app.positions import read_snapshot
from portfolio_app.leveraged_funds_ui import render_leveraged_funds
directory = Path(__import__('os').environ['SYNTHETIC_FUND_WORKSPACE'])
render_leveraged_funds(directory, read_snapshot(directory / 'holdings.csv').holdings)
''')
    import os
    from unittest.mock import patch
    calls = []
    def save(*args, **kwargs):
        calls.append(args[1])
        return update_fund_factor(*args, **kwargs, freeze_keys=lambda *_: None)
    with patch.dict(os.environ, {'SYNTHETIC_FUND_WORKSPACE': str(tmp_path)}), \
            patch('portfolio_app.leveraged_funds_ui.update_fund_factor', side_effect=save):
        app.run()
        app.text_input[0].input('-2')
        app.text_input[1].input('Invented issuer factsheet')
        app.text_input[2].input('Invented benchmark')
        app.selectbox[2].select('proxy')
        app.button[0].click().run()
        assert not app.exception and not app.error
    factor = load_leverage_settings(tmp_path)[0].fund_factors[0]
    assert factor.factor == -2 and factor.proxy_position_id == 'proxy' and factor.benchmark == 'Invented benchmark'
    assert calls == ['fund']
