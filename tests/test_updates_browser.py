"""Full app update notifications and settings, with invented holdings and releases."""
import os
import socket
import sys
import time
from urllib.request import urlopen

import pytest

from browser_support import open_app_menu, settle
from browser_runtime import browser_session, start_server
from synthetic_sample import create_sample_data
from portfolio_app.runtime.updates import UpdateStore

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def update_page(tmp_path):
    root = create_sample_data(tmp_path / 'synthetic')
    state = tmp_path / 'app-state'
    app = tmp_path / 'app.py'
    app.write_text('''
from pathlib import Path
import streamlit as st
import time
from portfolio_app.interface.shared import update_ui
from portfolio_app.runtime.updates import UpdateService, Release
from portfolio_app.ui import render_app
from portfolio_app.market.prices import PriceService, StaticProvider
from portfolio_app.funds.etf_refresh import coordinator
root = Path(__file__).parent
@st.cache_resource
def update_service():
    def fetch(target):
        with (root/'checks').open('a') as handle: handle.write('check\\n')
        deadline = time.monotonic() + 60
        while not (root/'release-ready').exists():
            if time.monotonic() > deadline:
                raise TimeoutError('Synthetic release was not supplied')
            time.sleep(.05)
        return Release((root/'release-ready').read_text())
    return UpdateService(root/'app-state', 'windows-x64' if (root/'windows').exists() else 'linux-x64', fetch=fetch)
update_ui.updates_for = update_service
update_ui.app_version = lambda: '0.2.0'
coordinator.schedule = lambda *args, **kwargs: False
launch = root/'synthetic'
managed = (root/'windows').exists()
if managed:
    from portfolio_app.runtime import launcher, windows_update
    from portfolio_app.workspace.workspace_selection import Selection, save_selection, selection_file
    launch = root/'launch'
    if not selection_file(launch).exists():
        save_selection(launch, Selection(root/'synthetic', 1))
    class SyntheticDownload(windows_update.InstallerDownload):
        def _download(self, tag):
            deadline = time.monotonic() + 30
            while not (root/'download-ready').exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self._set(windows_update.DownloadStatus(tag, 'ready', 1))
    @st.cache_resource
    def downloads():
        return SyntheticDownload(root/'downloads')
    windows_update.downloads_for = downloads
    windows_update.installed_directory = lambda: root/'invented-install'
    def handoff(directory, action, *, payload):
        assert directory == launch and action == 'update' and payload == {'tag': 'v0.2.1'}
        record = root/'handoff'
        failed = not record.exists()
        record.write_text('verification failed' if failed else 'installer requested')
        return {'error': 'Invented verification failure.' if failed else ''}
    launcher.request_instance = handoff
render_app(launch, managed=managed, ignore_selection=not managed,
           price_service=PriceService(StaticProvider(root/'synthetic'/'demo_prices.json')))
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with (tmp_path / 'server.log').open('w') as log:
        process = start_server([sys.executable, '-m', 'streamlit', 'run', str(app),
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
            with browser_session() as runner:
                browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                page.set_default_timeout(20000)
                yield page, url, root, UpdateStore(state)
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_upgrade_notification_and_manual_check_after_optout(update_page):
    page, url, root, store = update_page
    page.goto(url)
    playwright.expect(page.locator('.js-plotly-plot').first).to_be_visible(timeout=45000)
    settle(page)
    bell = page.locator('.st-key-header_notifications').get_by_test_id('stPopoverButton').filter(visible=True)
    badge = "button => Number(getComputedStyle(button, '::after').content.replaceAll('\"', ''))"
    previous = bell.evaluate(badge)
    (root.parent / 'release-ready').write_text('v0.2.1')
    page.wait_for_function("(count) => [...document.querySelectorAll('.st-key-header_notifications button')]"
                           ".some(button => button.checkVisibility() && (" + badge + ")(button) === count)",
                           arg=previous + 1)
    popup = page.get_by_role('dialog', name='Update available')
    notes = popup.get_by_role('link', name='View release')
    playwright.expect(notes).to_have_attribute('href',
        'https://github.com/eliaskempf/portfolio-breakdown/releases/tag/v0.2.1')
    popup.get_by_role('button', name='Close', exact=True).click()
    settle(page)
    assert store.read()['seen'] == 'v0.2.1'
    page.reload()
    playwright.expect(page.locator('.js-plotly-plot').first).to_be_visible(timeout=45000)
    settle(page)
    playwright.expect(popup).to_have_count(0)
    bell.click()
    release = page.get_by_role('link', name='Version 0.2.1 available')
    playwright.expect(release).to_have_attribute('href',
        'https://github.com/eliaskempf/portfolio-breakdown/releases/tag/v0.2.1')
    settle(page)
    page.get_by_role('button', name='Clear notification').click()
    playwright.expect(release).to_have_count(0)
    assert store.read()['cleared'] == 'v0.2.1'
    page.reload()
    playwright.expect(page.locator('.js-plotly-plot').first).to_be_visible(timeout=45000)
    settle(page)
    bell.click()
    playwright.expect(page.get_by_role('heading', name='Notifications', exact=True)).to_be_visible()
    playwright.expect(release).to_have_count(0)
    page.keyboard.press('Escape')
    help_section = open_app_menu(page, 'Help')
    playwright.expect(help_section.get_by_role('link', name='GitHub releases')).to_have_attribute(
        'href', 'https://github.com/eliaskempf/portfolio-breakdown/releases')
    help_section.get_by_text('Check automatically', exact=True).click()
    settle(page)
    assert store.read()['enabled'] is False
    (root.parent / 'release-ready').write_text('v0.2.2')
    help_section.get_by_role('button', name='Check for updates').click()
    playwright.expect(notes).to_have_attribute('href',
        'https://github.com/eliaskempf/portfolio-breakdown/releases/tag/v0.2.2')
    popup.get_by_role('button', name='Got it').click()
    playwright.expect(popup).to_have_count(0)
    open_app_menu(page, 'Help')
    playwright.expect(help_section.get_by_role('link', name='View release 0.2.2')).to_be_visible()
    page.keyboard.press('Escape')
    bell.click()
    playwright.expect(page.get_by_role('link', name='Version 0.2.2 available')).to_be_visible()
    settle(page)
    assert (root.parent / 'checks').read_text().splitlines() == ['check', 'check']
    assert store.read()['enabled'] is False
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_windows_update_download_resume_cancel_and_handoff(update_page):
    page, url, root, store = update_page
    state = root.parent
    (state / 'windows').touch()
    (state / 'release-ready').write_text('v0.2.1')
    before = (root / 'holdings.csv').read_bytes()
    page.set_viewport_size({'width': 640, 'height': 900})
    page.goto(url)
    popup = page.get_by_role('dialog', name='Update available')
    download = popup.get_by_role('button', name='Download update', exact=True)
    playwright.expect(download).to_be_visible(timeout=45000)
    download.click()
    playwright.expect(popup.get_by_role('progressbar')).to_be_visible()
    popup.get_by_role('button', name='Close', exact=True).click()
    playwright.expect(popup).to_have_count(0)
    settle(page)
    assert store.read()['seen'] == 'v0.2.1'
    assert not (state / 'handoff').exists()
    bell = page.locator('.st-key-header_notifications').get_by_test_id('stPopoverButton').filter(visible=True)
    bell.click()
    playwright.expect(page.get_by_role('progressbar')).to_be_visible()
    (state / 'download-ready').touch()
    keep = page.get_by_role('button', name='Keep working', exact=True)
    finish = page.get_by_role('button', name='Exit and update', exact=True)
    playwright.expect(keep).to_be_visible()
    keep.focus()
    page.keyboard.press('Tab')
    playwright.expect(finish).to_be_focused()
    keep.click()
    playwright.expect(finish).not_to_be_visible()
    assert not (state / 'handoff').exists()

    help_section = open_app_menu(page, 'Help')
    help_section.get_by_role('button', name='Exit and update', exact=True).click()
    playwright.expect(help_section.get_by_text('Invented verification failure.')).to_be_visible()
    assert (state / 'handoff').read_text() == 'verification failed'
    help_section.get_by_role('button', name='Download update', exact=True).click()
    help_section.get_by_role('button', name='Exit and update', exact=True).click()
    playwright.expect(help_section.get_by_text('Closing the app. Continue in Windows Setup.')).to_be_visible()
    assert (state / 'handoff').read_text() == 'installer requested'
    assert (root / 'holdings.csv').read_bytes() == before
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
