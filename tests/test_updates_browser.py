"""Full app update dialogs and settings, with invented holdings and releases."""
import os
import re
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from browser_support import settle, select_tab
from synthetic_sample import create_sample_data
from portfolio_app.updates import UpdateStore, WEEK

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def update_page(tmp_path):
    root = create_sample_data(tmp_path / 'synthetic')
    state = tmp_path / 'app-state'
    clock = tmp_path / 'clock'
    clock.write_text(str(time.time()))
    app = tmp_path / 'app.py'
    app.write_text('''
from pathlib import Path
import streamlit as st
import time
from portfolio_app import update_ui
from portfolio_app.updates import UpdateService, Release
from portfolio_app.ui import render_app
from portfolio_app.prices import PriceService, StaticProvider
from portfolio_app.etf_refresh import coordinator
root = Path(__file__).parent
@st.cache_resource
def update_service():
    def fetch(target):
        with (root/'checks').open('a') as handle: handle.write('check\\n')
        time.sleep(.5)
        return Release('v0.2.0')
    return UpdateService(root/'app-state', 'linux-x64', fetch=fetch,
                         now=lambda: float((root/'clock').read_text()))
update_ui.updates_for = update_service
update_ui.app_version = lambda: '0.1.0'
if st.query_params.get('windows') == '1':
    from portfolio_app.windows_update import InstallerDownload, DownloadStatus
    from portfolio_app import launcher
    class SyntheticDownload(InstallerDownload):
        def _download(self, tag):
            time.sleep(.5)
            self._set(DownloadStatus(tag, 'ready', 1))
    @st.cache_resource
    def downloads():
        return SyntheticDownload(root/'downloads')
    update_ui.downloads_for = downloads
    update_ui.installed_directory = lambda: root/'invented-install'
    def handoff(directory, action, **kwargs):
        assert action == 'update' and kwargs['payload'] == {'tag': 'v0.2.0'}
        marker = root/'handoff'
        failed = not marker.exists()
        marker.write_text('synthetic handoff only')
        return {'error': 'Invented verification failure' if failed else ''}
    launcher.request_instance = handoff
coordinator.schedule = lambda *args, **kwargs: False
st.session_state.setdefault('main_tabs', st.query_params.get('start', 'Overview'))
render_app(root/'synthetic', ignore_selection=True,
           price_service=PriceService(StaticProvider(root/'synthetic'/'demo_prices.json')))
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with (tmp_path / 'server.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(app),
            f'--server.port={port}', '--server.address=127.0.0.1', '--server.headless=true',
            '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'], stdout=log, stderr=log,
            env={**os.environ, 'PORTFOLIO_STATE_DIR': str(state)})
        try:
            url = f'http://127.0.0.1:{port}'
            for _ in range(100):
                assert process.poll() is None
                try:
                    with urlopen(url + '/_stcore/health', timeout=1):
                        break
                except OSError:
                    time.sleep(.1)
            with playwright.sync_playwright() as runner:
                browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                page.set_default_timeout(20000)
                yield page, url, root, UpdateStore(state), clock
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_update_reminder_close_weekly_repeat_optout_and_manual_check(update_page):
    page, url, root, store, clock = update_page
    original = (root / 'holdings.csv').read_bytes()
    page.goto(url)
    dialog = page.get_by_role('dialog', name='Update available')
    playwright.expect(dialog).to_be_visible()
    playwright.expect(dialog.get_by_role('link', name='View release')).to_have_attribute('href',
        'https://github.com/eliaskempf/portfolio-breakdown/releases/tag/v0.2.0')
    playwright.expect(page.get_by_role('tab', name='Overview', exact=True)).to_have_attribute('aria-selected', 'true')
    dialog.get_by_role('button', name='Close', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    settle(page)
    playwright.expect(page.locator('.js-plotly-plot').first).to_be_visible()
    page.reload()
    settle(page)
    playwright.expect(dialog).to_have_count(0)
    clock.write_text(str(float(clock.read_text()) + WEEK))
    page.reload()
    playwright.expect(dialog).to_be_visible()
    dialog.get_by_role('button', name='Never remind me again').click()
    playwright.expect(dialog).to_have_count(0)
    assert store.read()['enabled'] is False
    clock.write_text(str(float(clock.read_text()) + WEEK))
    count = (root.parent / 'checks').read_text().count('check')
    page.reload()
    settle(page)
    assert (root.parent / 'checks').read_text().count('check') == count
    page.get_by_role('button', name=re.compile('Portfolio settings')).click()
    page.get_by_text('Updates', exact=True).click()
    page.get_by_role('button', name='Check for updates', exact=True).click()
    playwright.expect(page.get_by_role('link', name='View release 0.2.0')).to_be_visible()
    playwright.expect(page.get_by_role('button', name='Check for updates', exact=True)).to_be_enabled()
    playwright.expect(page.get_by_text('Checking for updates…', exact=True)).to_have_count(0)
    settle(page)
    assert (root.parent / 'checks').read_text().count('check') == count + 1
    assert store.read()['enabled'] is False
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    assert (root / 'holdings.csv').read_bytes() == original


def test_update_waits_for_position_editor_and_notification_navigation(update_page):
    page, url, root, store, clock = update_page
    page.goto(url + '?start=Positions')
    page.get_by_role('button', name=re.compile('Add position')).click()
    playwright.expect(page.get_by_role('dialog', name='Add position')).to_be_visible()
    playwright.expect(page.get_by_role('dialog', name='Update available')).to_have_count(0)
    assert store.read()['remind_after'] == 0
    page.get_by_role('dialog').get_by_role('button', name='Close', exact=True).click()
    select_tab(page, 'Overview')
    dialog = page.get_by_role('dialog', name='Update available')
    playwright.expect(dialog).to_be_visible()
    dialog.get_by_role('button', name='Remind me in a week').click()
    playwright.expect(dialog).to_have_count(0)
    page.get_by_role('button', name=re.compile('Notifications')).click()
    playwright.expect(page.get_by_role('link', name='View release', exact=True)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_windows_download_survives_dialog_close_and_failed_handoff(update_page):
    page, url, root, store, clock = update_page
    original = (root / 'holdings.csv').read_bytes()
    page.goto(url + '?windows=1')
    dialog = page.get_by_role('dialog', name='Update available')
    playwright.expect(dialog).to_be_visible()
    dialog.get_by_role('button', name='Download update', exact=True).click()
    playwright.expect(dialog.get_by_role('button', name='Exit and update', exact=True)).to_be_visible()
    assert not (root.parent / 'handoff').exists()
    dialog.get_by_role('button', name='Remind me in a week').click()
    playwright.expect(dialog).to_have_count(0)
    page.get_by_role('button', name=re.compile('Portfolio settings')).click()
    page.get_by_text('Updates', exact=True).click()
    page.get_by_role('button', name='Exit and update', exact=True).click()
    playwright.expect(page.get_by_text('Invented verification failure', exact=True)).to_be_visible()
    page.get_by_role('button', name='Download update', exact=True).click()
    page.get_by_role('button', name='Exit and update', exact=True).click()
    playwright.expect(page.get_by_text('Closing the app. Continue in the Windows installer.', exact=True)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    assert (root / 'holdings.csv').read_bytes() == original
