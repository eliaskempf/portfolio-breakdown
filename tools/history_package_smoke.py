"""Exercise installed history, accounts and archive restoration with invented data."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory


def create_acceptance_workspace(directory: Path):
    """Create an isolated, offline history fixture, never a user's workspace."""
    from portfolio_app.demo_history import create_history_demo
    from portfolio_app.account_journal import load_journal, replay
    from portfolio_app.history_commands import correct_transaction, save_history
    from portfolio_app.history_observations import load_observations, save_observations, Observation
    from portfolio_app.positions import read_snapshot
    from portfolio_app.security_history import replay_securities
    from portfolio_app.transaction_csv import read_csv
    from portfolio_app.transaction_import_commands import save_transaction_import

    today = date.today()
    create_history_demo(directory, end=today)
    path = directory / 'holdings.csv'
    import pandas as pd
    frame = pd.read_csv(path, keep_default_na=False)
    frame['ticker'] = ''  # Installed acceptance needs no market-data network.
    frame['manual_price'] = [140, 52]
    frame['manual_price_currency'] = 'EUR'
    frame['manual_price_date'] = today.isoformat()
    frame['quantity_unit'] = 'units'
    frame.to_csv(path, index=False)
    preferences = directory / '.cache' / 'etf-refresh' / 'preferences.json'
    preferences.parent.mkdir(parents=True, exist_ok=True)
    preferences.write_text(json.dumps({'enabled': False, 'minimum_age_days': 1}))
    buy_day, sell_day = [(today-timedelta(days=n)).isoformat() for n in (10, 8)]
    rows = read_csv('reference,date,type,asset,quantity,amount,currency\n'
                    f'b,{buy_day},buy,CLOSED,2,20,EUR\ns,{sell_day},sell,CLOSED,2,24,EUR\n')
    journal, revision = load_journal(directory)
    save_transaction_import(directory, rows, {('Portfolio', 'CLOSED'): 'smoke-closed'},
        expected_journal_revision=revision, expected_holdings_revision=read_snapshot(path).revision,
        additions=[dict(position_key='smoke-closed', id='smoke-closed', name='Invented closed holding',
                        account='Portfolio', bucket_id='equity', instrument_type='equity')],
        selected_ids=[row.identity for row in rows], starts_at_zero=True,
        accept_reconciliation=True, comparison_date=today.isoformat())
    journal, revision = load_journal(directory)
    book = next(book for book in journal.securities if book.position_id == 'smoke-closed')
    sale = next(event for event in book.records if event.kind == 'sell')
    corrected = correct_transaction(book, sale.id, amount=Decimal(26))
    replacement = corrected.records[-1]
    corrected = replace(corrected, active_ids=tuple(
        replacement.id if identity == sale.id else identity for identity in book.active_ids))
    save_history(directory, corrected, expected_journal_revision=revision,
                 expected_holdings_revision=read_snapshot(path).revision,
                 activate=True, accept_balance_change=True)
    store, revision = load_observations(directory)
    prices = tuple(Observation(f'smoke-closed-{offset}', 'price', 'smoke-closed',
                              (today-timedelta(days=offset)).isoformat(), Decimal(10), 'EUR',
                              source='Invented acceptance price') for offset in (10, 9))
    save_observations(directory, replace(store, observations=(*store.observations, *prices)), revision)
    journal, _ = load_journal(directory)
    assert journal.version == 5 and journal.transaction_imports
    assert replay_securities(journal.securities).positions['smoke-closed'].quantity == 0
    assert any(event.replaces for book in journal.securities for event in book.records)
    cash = replay(journal).cash['demo-brokerage', 'EUR']
    # Fixed oracle for this invented schedule; do not make installed acceptance
    # silently follow a changed calculation in the source-side fixture generator.
    assert cash == Decimal('11201.85')
    return {'version': 5, 'cash': '11201.85', 'security_value': '22680',
            'portfolio_value': '33881.85', 'closed_position': 'smoke-closed'}


def smoke(command, evidence=None):
    from playwright.sync_api import sync_playwright, expect
    from package_smoke import wait_for_instance, settle_app
    from portfolio_app.backup import inspect_backup
    from portfolio_app.workspace import inventory

    with TemporaryDirectory(prefix='portfolio history acceptance ü ') as temporary:
        # macOS temporary paths commonly traverse /var -> /private/var. The
        # app deliberately rejects restore destinations with linked ancestors.
        root = Path(temporary).resolve()
        workspace, restored = root/'invented history', root/'restored history é'
        expected = create_acceptance_workspace(workspace)
        state = root/'state'
        env = dict(os.environ, PORTFOLIO_STATE_DIR=str(state), PYTHONPATH='',
                   XDG_DATA_HOME=str(root/'user-data'), XDG_CACHE_HOME=str(root/'cache'))
        env.pop('VIRTUAL_ENV', None)
        args = [*command, '--foreground', '--no-browser', '--skip-intro', '--data-dir', str(workspace)]
        log = root/'history-server.log'
        child = None

        def stop():
            if child is not None and child.poll() is not None:
                assert child.returncode == 0, f'Installed history launcher exited: {child.returncode}'
                return
            if child is not None and child.poll() is None:
                try:
                    subprocess.run([*command, '--data-dir', str(workspace), '--stop'], env=env,
                                   cwd=root, check=True, capture_output=True, timeout=30)
                    assert child.wait(timeout=20) == 0
                finally:
                    # This launcher owns a fresh process group (or Windows tree).
                    # Even failed startup/stop must not leave its synthetic server.
                    if child.poll() is None:
                        if os.name == 'nt':
                            subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                                           capture_output=True, timeout=20)
                        else:
                            try:
                                os.killpg(child.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            if os.name != 'nt':
                                try:
                                    os.killpg(child.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                            else:
                                child.kill()
                            child.wait(timeout=10)

        def inspect_views(page):
            page.get_by_role('tab', name='Overview', exact=True).click(); settle_app(page)
            expect(page.get_by_role('radio', name='Performance', exact=True)).to_be_checked()
            expect(page.locator('.js-plotly-plot:visible').first).to_be_visible()
            value = page.get_by_test_id('stMetric').filter(has=page.get_by_text('Portfolio value', exact=True))
            expect(value).to_contain_text(f"{Decimal(expected['portfolio_value']):,.2f}")
            page.get_by_role('tab', name='Positions', exact=True).click(); settle_app(page)
            table = page.get_by_role('table', name='Positions', exact=True)
            table.get_by_text('World equity — demo holding', exact=True).click(); settle_app(page)
            dialog = page.get_by_role('dialog')
            expect(dialog.locator('.js-plotly-plot:visible')).to_have_count(1)
            dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle_app(page)
            expect(dialog.get_by_role('table', name='Transactions', exact=True)).to_be_visible()
            dialog.get_by_role('button', name='Close', exact=True).first.click(); settle_app(page)
            page.get_by_role('button', name='Portfolio settings', exact=True).click()
            sold = page.get_by_role('checkbox', name='Show sold positions', exact=True, include_hidden=True)
            if not sold.is_checked():
                page.get_by_text('Show sold positions', exact=True).click(); settle_app(page)
            page.get_by_role('button', name='Portfolio settings', exact=True).click(); settle_app(page)
            table.get_by_text('Invented closed holding', exact=True).click(); settle_app(page)
            dialog.get_by_role('radio', name='Transactions', exact=True).click(); settle_app(page)
            expect(dialog.get_by_role('table', name='Transactions', exact=True)).to_contain_text('26.00')
            dialog.get_by_role('button', name='Close', exact=True).first.click(); settle_app(page)
            page.get_by_role('tab', name='Futures', exact=True).click(); settle_app(page)
            for name in ('Cash & financing', 'Collateral', 'Marks & FX', 'Positions & transactions'):
                page.get_by_role('radio', name=name, exact=True).click(); settle_app(page)
            page.get_by_role('tab', name='Overview', exact=True).click(); settle_app(page)

        def settings(page, action):
            settle_app(page)
            page.get_by_role('button', name='Portfolio settings', exact=True).click()
            page.get_by_role('button', name=action, exact=True).click()
            dialog = page.get_by_role('dialog', name=action, exact=True)
            expect(dialog).to_be_visible()
            return dialog

        with log.open('w', encoding='utf-8') as handle:
            try:
                child = subprocess.Popen(args, env=env, cwd=root, stdout=handle, stderr=handle,
                                         start_new_session=os.name != 'nt')
                url = wait_for_instance(state, child)
                with sync_playwright() as runner:
                    browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),
                                                      args=['--no-sandbox'])
                    page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                    page.set_default_timeout(30000)
                    page.goto(url)
                    page.get_by_role('tab', name='Overview', exact=True).wait_for()
                    inspect_views(page)
                    before = inventory(workspace)
                    dialog = settings(page, 'Create backup')
                    with page.expect_download() as downloaded:
                        dialog.get_by_role('button', name='Download backup', exact=True).click()
                    archive = root/'invented.portfolio-backup.zip'
                    downloaded.value.save_as(archive)
                    draft = inspect_backup(archive.read_bytes())
                    try:
                        assert draft.digests == before
                    finally:
                        draft.close()
                    dialog.get_by_role('button', name='Close backup', exact=True).click()
                    dialog = settings(page, 'Restore backup')
                    dialog.locator('input[type=file]').set_input_files(archive)
                    dialog.get_by_role('button', name='Review backup', exact=True).click()
                    folder = dialog.get_by_role('textbox', name='New workspace folder', exact=True)
                    expect(folder).not_to_have_value('')
                    folder.fill(str(restored)); folder.press('Tab')
                    dialog.get_by_role('button', name='Restore and switch', exact=True).click()
                    expect(dialog).not_to_be_visible(); settle_app(page)
                    assert inventory(restored) == before == inventory(workspace)
                    inspect_views(page)
                    stop()
                    child = subprocess.Popen(args, env=env, cwd=root, stdout=handle, stderr=handle,
                                             start_new_session=os.name != 'nt')
                    page.goto(wait_for_instance(state, child))
                    page.get_by_role('tab', name='Overview', exact=True).wait_for()
                    inspect_views(page)
                    # The original launch path must reopen the remembered restored workspace.
                    from portfolio_app.launcher import request_instance
                    from unittest.mock import patch
                    with patch.dict(os.environ, {'PORTFOLIO_STATE_DIR': str(state)}):
                        assert request_instance(workspace)['workspace']['directory'] == str(restored)
                    assert inventory(workspace) == before
                    assert (restored/'account-journal.json').read_bytes() == (workspace/'account-journal.json').read_bytes()
                    browser.close()
                stop()
                if evidence is not None:
                    (evidence/'history-acceptance.json').write_text(json.dumps({
                        'status': 'passed', 'synthetic': True, 'expected': expected,
                        'checks': ['history and transaction navigation', 'closed holding with corrected sale', 'four account modes',
                                   'downloaded full archive digests', 'restore and switch',
                                   'restart remembers restored workspace', 'retained v5 correction/import evidence'],
                    }, indent=2))
            except BaseException:
                handle.flush()
                if evidence is not None:
                    (evidence/'history-server.log').write_bytes(log.read_bytes())
                raise
            finally:
                if child is not None and child.poll() is None:
                    if sys.exc_info()[0] is None:
                        stop()
                    else:
                        try:
                            stop()
                        except Exception as cleanup_error:
                            print(f'Synthetic history cleanup failed: {cleanup_error}', file=sys.stderr)
    print('Installed history, accounts and full archive restoration checks passed.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', nargs='?', type=Path)
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    if args.evidence:
        args.evidence.mkdir(parents=True, exist_ok=False)
    smoke([str(args.executable.resolve())] if args.executable else [sys.executable, '-m', 'portfolio_app.app'], args.evidence)
