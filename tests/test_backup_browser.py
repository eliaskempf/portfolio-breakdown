"""Real backup upload/download, cancellation, activation and restart, offline."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from portfolio_app.demo import create_demo_data
from portfolio_app.launcher import request_instance
from portfolio_app.portfolio_settings import save_settings
from portfolio_app.workspace import inventory
from portfolio_app.workspace_selection import selection

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def backup_app(tmp_path):
    workspace = create_demo_data(tmp_path / 'invented original ü')
    save_settings(workspace, 'GBP', None)
    # A synthetic harness replaces only providers, retaining the actual launcher,
    # control endpoint, Streamlit UI and durable workspace selection.
    view = tmp_path / 'offline_view.py'
    view.write_text('''import argparse
from pathlib import Path
from portfolio_app.ui import render_app
from portfolio_app.prices import PriceService, StaticProvider
from portfolio_app.workspace_selection import selection
from portfolio_app.etf_refresh import coordinator
coordinator.schedule = lambda *a, **kw: False
parser = argparse.ArgumentParser()
parser.add_argument('--data-dir', type=Path)
parser.add_argument('--demo-dir', type=Path)
parser.add_argument('--skip-intro', action='store_true')
args = parser.parse_args()
chosen = selection(args.data_dir).directory
render_app(args.data_dir, demo_dir=args.demo_dir,
           price_service=PriceService(StaticProvider(chosen / 'demo_prices.json')))
''')
    supervisor = tmp_path / 'supervisor.py'
    supervisor.write_text(f'''from pathlib import Path
import portfolio_app.app as app
real = app.run_server
def run(command, *args, **kwargs):
    command[command.index(str(Path(app.__file__).with_name('ui.py')))] = {str(view)!r}
    return real(command, *args, **kwargs)
app.run_server = run
app.main()
''')
    log = (tmp_path / 'synthetic-server.log').open('w')
    children = []
    def start():
        child = subprocess.Popen([sys.executable, str(supervisor), '--data-dir', str(workspace),
                                  '--foreground', '--no-browser', '--offline-demo', '--skip-intro'],
                                 stdout=log, stderr=log)
        children.append(child)
        for _ in range(150):
            assert child.poll() is None, 'Synthetic launcher failed; inspect its temporary log.'
            info = request_instance(workspace)
            if info and info['ready']:
                return info['url']
            time.sleep(.1)
        raise AssertionError('Synthetic launcher did not become ready')
    def stop():
        from portfolio_app.launcher import stop_instance
        assert stop_instance(workspace)
        children[-1].wait(timeout=15)
    try:
        yield workspace, start, stop
    finally:
        for child in children:
            if child.poll() is None:
                # Let the launcher stop its Streamlit child too. Terminating
                # just the supervisor leaves a server behind on Windows.
                try:
                    stop()
                finally:
                    if child.poll() is None:
                        if sys.platform == 'win32':
                            subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            child.terminate()
                        child.wait(timeout=15)
        log.close()


def settings(page, action):
    playwright.expect(page.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning')
    page.get_by_role('button', name='tune Portfolio settings', exact=True).click()
    page.get_by_role('button', name=action, exact=True).click()
    return page.get_by_role('dialog', name=action, exact=True)


def test_archive_review_cancel_switch_restart_and_tabs(backup_app, tmp_path):
    original, start, stop = backup_app
    before = inventory(original)
    url = start()
    with playwright.sync_playwright() as runner:
        browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.set_default_timeout(15000)
        page.goto(url)
        page.get_by_role('tab', name='Overview', exact=True).wait_for()
        stale = browser.new_page()
        stale.goto(url)
        stale.get_by_role('tab', name='Overview', exact=True).wait_for()
        playwright.expect(stale.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning')
        stale.get_by_role('button', name='tune Portfolio settings', exact=True).click()
        stale.get_by_role('combobox', name='Portfolio currency').click()
        stale.get_by_role('option', name='USD', exact=True).click()
        stale.get_by_role('button', name='Review currency change').click()
        stale_dialog = stale.get_by_role('dialog', name='Change portfolio currency')
        playwright.expect(stale_dialog).to_be_visible()
        dialog = settings(page, 'Create backup')
        with page.expect_download() as downloaded:
            dialog.get_by_role('button', name='Download backup').click()
        archive = tmp_path / 'invented.portfolio-backup.zip'
        downloaded.value.save_as(archive)
        from portfolio_app.backup import inspect_backup
        draft = inspect_backup(archive.read_bytes())
        assert draft.digests == before
        draft.close()
        dialog.get_by_role('button', name='Close backup').click()
        dialog = settings(page, 'Restore backup')
        dialog.locator('input[type=file]').set_input_files(archive)
        dialog.get_by_role('button', name='Review backup').click()
        playwright.expect(dialog).to_contain_text('Currency: GBP')
        target = Path(dialog.get_by_role('textbox', name='New workspace folder').input_value())
        dialog.get_by_role('button', name='Cancel restore').click()
        assert not target.exists() and selection(original).directory == original
        assert inventory(original) == before
        dialog = settings(page, 'Restore backup')
        dialog.locator('input[type=file]').set_input_files(archive)
        dialog.get_by_role('button', name='Review backup').click()
        target = tmp_path / 'restored chosen é'
        field = dialog.get_by_role('textbox', name='New workspace folder')
        field.fill(str(original)); field.press('Tab')
        playwright.expect(dialog.get_by_role('button', name='Restore and switch')).to_be_disabled()
        field.fill(str(target)); field.press('Tab')
        playwright.expect(dialog.get_by_role('button', name='Restore and switch')).to_be_enabled()
        dialog.get_by_role('button', name='Restore and switch').click()
        playwright.expect(dialog).not_to_be_visible()
        page.get_by_role('tab', name='Overview', exact=True).wait_for()
        assert page.url == url + '/' or page.url == url
        assert selection(original).directory == target
        assert inventory(target) == before == inventory(original)
        stale_dialog.get_by_role('button', name='Apply currency change').click()
        playwright.expect(stale_dialog).to_contain_text('active portfolio changed')
        assert inventory(original) == before
        stale_dialog.get_by_role('button', name='Cancel currency change').click()
        stale.get_by_role('tab', name='Positions', exact=True).click()
        playwright.expect(stale.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning')
        playwright.expect(stale.get_by_test_id('stException')).to_have_count(0)
        stale.close()
        for name in ['Positions', 'Exposure', 'Rebalance', 'Overview']:
            page.get_by_role('tab', name=name, exact=True).click()
            playwright.expect(page.get_by_role('tab', name=name, exact=True)).to_have_attribute('aria-selected', 'true')
            playwright.expect(page.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning')
            playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
        stop()
        page.goto(start())
        page.get_by_role('tab', name='Overview', exact=True).wait_for()
        page.get_by_role('button', name='tune Portfolio settings', exact=True).click()
        playwright.expect(page.get_by_role('combobox', name='Portfolio currency')).to_have_value('GBP')
        assert request_instance(original)['workspace']['directory'] == str(target)
        assert inventory(original) == before
        browser.close()


def test_bad_upload_and_dismiss_write_nothing(backup_app):
    original, start, _ = backup_app
    before = inventory(original)
    with playwright.sync_playwright() as runner:
        browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
        page = browser.new_page()
        page.goto(start())
        page.get_by_role('tab', name='Overview', exact=True).wait_for()
        dialog = settings(page, 'Restore backup')
        dialog.locator('input[type=file]').set_input_files(dict(name='bad.zip', mimeType='application/zip', buffer=b'invented invalid archive'))
        dialog.get_by_role('button', name='Review backup').click()
        playwright.expect(dialog).to_contain_text('Invalid or damaged')
        page.keyboard.press('Escape')
        playwright.expect(dialog).not_to_be_visible()
        assert selection(original).directory == original
        assert inventory(original) == before
        playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
        browser.close()
