"""Currency and portable-workspace integration, using invented data only."""
from dataclasses import replace
import json

import pytest

from portfolio_app.backup import create_backup, inspect_backup, commit_restore
from portfolio_app.demo import create_demo_data
from portfolio_app.futures_workspace import fork_futures_workspace, load_sandbox, save_sandbox
from portfolio_app.holdings import DataError
from portfolio_app.leverage_settings import LeverageSettings, MarginLiability, save_leverage_settings
from portfolio_app.portfolio_settings import load_settings, save_settings
from portfolio_app.workspace import inventory


@pytest.mark.parametrize('currency', ['USD', 'GBP'])
def test_currency_change_and_fork_reject_active_eur_records(tmp_path, currency):
    source = create_demo_data(tmp_path / 'synthetic-source')
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    before = inventory(sandbox)
    with pytest.raises(DataError, match='require EUR'):
        save_settings(sandbox, currency, None)
    assert inventory(sandbox) == before
    save_settings(source, currency, None)
    with pytest.raises(DataError, match='EUR source'):
        fork_futures_workspace(source, tmp_path / 'unsupported')
    assert not (tmp_path / 'unsupported').exists()
    with pytest.raises(DataError, match='require EUR'):
        save_leverage_settings(source, LeverageSettings(liabilities=(MarginLiability('synthetic', 'Invented debt', 10.),)), None)


def test_funded_closed_future_and_unknown_debt_still_require_eur(tmp_path):
    source = create_demo_data(tmp_path / 'synthetic-source')
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    future, revision = load_sandbox(sandbox)
    save_sandbox(sandbox, replace(future, contracts=0, reference_capital_eur=100.), revision)
    with pytest.raises(DataError, match='require EUR'):
        save_settings(sandbox, 'USD', None)
    save_leverage_settings(source, LeverageSettings(liabilities=(MarginLiability('synthetic', 'Invented debt', None),)), None)
    with pytest.raises(DataError, match='require EUR'):
        save_settings(source, 'GBP', None)


def test_backup_roundtrip_preserves_futures_and_leverage(tmp_path):
    source = create_demo_data(tmp_path / 'synthetic-source')
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    save_leverage_settings(sandbox, LeverageSettings(enabled=True), None)
    from portfolio_app.futures_funding import FundingPolicy, FundingStudy
    from portfolio_app.futures_funding_data import save_funding_settings
    save_funding_settings(sandbox, FundingStudy(1000., FundingPolicy(700.), (FundingPolicy(400.),)), '', None)
    before = inventory(sandbox)
    draft = inspect_backup(create_backup(sandbox))
    try:
        restored = commit_restore(draft, tmp_path / 'restored', sandbox)
        assert inventory(restored) == before == inventory(sandbox)
        assert load_sandbox(restored)[0] == load_sandbox(sandbox)[0]
    finally:
        draft.close()


@pytest.mark.parametrize('currency', ['USD', 'GBP'])
def test_existing_non_eur_leverage_remains_visible_without_mixed_totals(tmp_path, currency):
    from test_futures_ui import launch
    source = create_demo_data(tmp_path / 'synthetic-source')
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    # Simulate a pre-existing external edit, not a supported currency conversion.
    (sandbox / 'portfolio.yaml').write_text(f'version: 1\nreporting_currency: {currency}\n')
    before = (sandbox / 'futures-sandbox.json').read_bytes()
    app = launch(sandbox, open_futures=False)
    assert not app.exception
    assert any('Saved leverage records are in EUR' in item.value for item in app.warning)
    assert any(item.label == 'Saved EUR leverage records' for item in app.expander)
    assert not app.metric
    assert (sandbox / 'futures-sandbox.json').read_bytes() == before
    settings = load_settings(sandbox)
    save_settings(sandbox, 'EUR', settings.revision)
    app.run()
    assert not app.exception and app.metric


def test_workspace_validation_rejects_corrupt_leverage_settings(tmp_path):
    from portfolio_app.workspace import validate_workspace
    source = create_demo_data(tmp_path / 'synthetic')
    (source / 'leverage-settings.json').write_text(json.dumps({'version': 999}))
    with pytest.raises(DataError, match='leverage'):
        validate_workspace(source)


@pytest.mark.parametrize('currency', ['EUR', 'USD', 'GBP'])
def test_enabled_only_exposure_policy_is_currency_independent_and_preserves_basis(tmp_path, currency):
    from portfolio_app.leverage_settings import load_leverage_settings
    source = create_demo_data(tmp_path / 'synthetic')
    save_settings(source, currency, None)
    save_leverage_settings(source, LeverageSettings(enabled=True, active_basis='exposure'), None)
    assert load_settings(source).reporting_currency == currency
    assert load_leverage_settings(source)[0].active_basis == 'exposure'


@pytest.mark.parametrize('contracts', [0, 1])
def test_future_only_portfolio_preserves_wealth_and_default_overview(tmp_path, contracts):
    from test_futures_ui import launch
    from test_ui import by_label
    from list_helpers import list_frame
    source = create_demo_data(tmp_path / 'synthetic-source')
    holdings = source / 'holdings.csv'
    holdings.write_text(holdings.read_text().splitlines()[0] + '\n')
    sandbox = fork_futures_workspace(source, tmp_path / 'synthetic-sandbox')
    future, revision = load_sandbox(sandbox)
    save_sandbox(sandbox, replace(future, contracts=contracts, reference_capital_eur=500.), revision)
    app = launch(sandbox, open_futures=False)
    assert not app.exception
    assert app.session_state['main_tabs'] == 'Overview'
    assert by_label(app.metric, 'Portfolio value').value == '€500.00'
    assert 'future:overlay' in list_frame(app, 'Positions').id.tolist()


def test_orphaned_exposure_policy_is_a_recoverable_validation_error(tmp_path):
    from portfolio_app.workspace import validate_workspace
    source = create_demo_data(tmp_path / 'synthetic')
    (source / 'allocation.yaml').unlink(missing_ok=True)
    (source / 'exposure-targets.json').write_text(json.dumps(dict(multiple=1.2,
        policy='composition', composition={'invented': 1.}, version=1)))
    with pytest.raises(DataError, match='allocation hierarchy'):
        validate_workspace(source)


@pytest.mark.parametrize('first', ['currency', 'leverage'])
def test_currency_and_leverage_commands_validate_inside_workspace_transaction(tmp_path, monkeypatch, first):
    """Hold the first command after validation; the competing save must recheck."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from portfolio_app import portfolio_settings, leverage_settings
    validated, release, competing = Event(), Event(), Event()
    module = portfolio_settings if first == 'currency' else leverage_settings
    original = module.save_document

    def paused_write(*args, **kwargs):
        validated.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'save_document', paused_write)
    commands = {
        'currency': lambda: save_settings(tmp_path, 'USD', None),
        'leverage': lambda: save_leverage_settings(tmp_path, LeverageSettings(
            liabilities=(MarginLiability('synthetic', 'Invented debt', 10.),)), None),
    }
    def other():
        competing.set()
        return commands['leverage' if first == 'currency' else 'currency']()

    with ThreadPoolExecutor(2) as pool:
        winner = pool.submit(commands[first])
        try:
            assert validated.wait(5)
            loser = pool.submit(other)
            assert competing.wait(5)
            # A writer cannot get past validation while the first command holds
            # its transaction, even though they write different documents.
            from concurrent.futures import TimeoutError
            with pytest.raises(TimeoutError):
                loser.result(timeout=.1)
        finally:
            release.set()
        winner.result(timeout=5)
        with pytest.raises(DataError, match='EUR'):
            loser.result(timeout=5)
    currency = load_settings(tmp_path).reporting_currency
    leverage = leverage_settings.load_leverage_settings(tmp_path)[0]
    assert (currency, leverage.enabled) == (('USD', False) if first == 'currency' else ('EUR', True))
