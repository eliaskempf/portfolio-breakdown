"""Optional real-browser onboarding coverage using exclusively invented holdings."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from portfolio_app.positions import read_snapshot
from browser_support import StreamlitReruns, settle

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def import_page(tmp_path, request):
    app = tmp_path / 'app.py'
    delayed_navigation = getattr(request, 'param', None) == 'delayed_navigation'
    gate = '''
import time
import portfolio_app.presentation as presentation
if not hasattr(presentation, '_synthetic_empty_overview'):
    presentation._synthetic_empty_overview = presentation.empty_overview
    def gated_overview():
        presentation._synthetic_empty_overview()
        root = Path(__file__).parent
        deadline = time.monotonic() + 10
        while (root / 'hold-overview').exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('Synthetic navigation gate was not released')
            time.sleep(.01)
    presentation.empty_overview = gated_overview
''' if delayed_navigation else ''
    app.write_text('from pathlib import Path\n' + gate + '''
from portfolio_app.ui import render_app
from portfolio_app.prices import PriceService, UnavailableProvider
render_app(Path(__file__).parent / 'synthetic-portfolio', demo=True,
           price_service=PriceService(UnavailableProvider()))
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, '-m', 'streamlit', 'run', str(app), '--server.address=127.0.0.1',
         f'--server.port={port}', '--server.headless=true', '--server.fileWatcherType=none',
         '--browser.gatherUsageStats=false'], cwd=tmp_path,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                with urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail('Synthetic import preview did not start')
        with playwright.sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.streamlit_reruns = StreamlitReruns(page, settle=settle)
            page.delayed_navigation = delayed_navigation
            if delayed_navigation:
                # Hold an outgoing navigation request without changing its
                # contents or the ordering of any later WebSocket messages.
                page.add_init_script('''(() => {
                    const send = WebSocket.prototype.send;
                    window.heldNavigation = [];
                    WebSocket.prototype.send = function(data) {
                        if (window.holdNavigation) window.heldNavigation.push([this, data]);
                        else send.call(this, data);
                    };
                    window.releaseNavigation = () => {
                        window.holdNavigation = false;
                        for (const [socket, data] of window.heldNavigation) send.call(socket, data);
                        window.heldNavigation = [];
                    };
                })()''')
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('button', name='Import holdings', exact=True).wait_for()
            yield page, tmp_path / 'synthetic-portfolio' / 'holdings.csv'
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize('import_page', ['normal', 'delayed_navigation'], indirect=True)
def test_upload_review_chart_allocation_and_listing(import_page):
    page, path = import_page
    page.get_by_role('button', name='Import holdings', exact=True).click()
    page.locator('input[type=file]').set_input_files({
        'name': 'invented-holdings.csv', 'mimeType': 'text/csv',
        'buffer': ('Name;Stück/Nennwert Bank;Stück/Nennwert FinanzManager;ISIN;Kurs;Kursdatum;Währung\n'
                   'Invented stock;2,5;1,5;US67066G1040;12,00;2026-01-02;EUR\n').encode(),
    })
    save = page.get_by_role('button', name='Import reviewed positions', exact=True)
    playwright.expect(save).to_be_disabled()
    page.get_by_role('combobox', name='Quantity (required)', exact=True).click()
    page.get_by_role('combobox', name='Quantity (required)', exact=True).fill('Stück/Nennwert Bank')
    page.get_by_role('option', name='Stück/Nennwert Bank', exact=True).click()
    page.get_by_text('Quantities are shares/units and prices are amounts per unit (not nominal values or percent quotes)', exact=True).click()
    playwright.expect(save).to_be_enabled()
    gate = path.parent.parent / 'hold-overview'
    if page.delayed_navigation:
        gate.touch()
    page.get_by_role('tab', name='Overview', exact=True).click()
    playwright.expect(page.get_by_text('Open Positions and choose Add position to get started.', exact=True)).to_be_visible()
    if page.delayed_navigation:
        page.evaluate('window.holdNavigation = true')
    page.get_by_role('tab', name='Positions', exact=True).click()
    if page.delayed_navigation:
        page.wait_for_function('window.heldNavigation.length > 0')
        gate.unlink()
        page.streamlit_reruns.wait()
        settle(page)
        # The return request captured the old widget values, but reaches the
        # server after the completed Overview render removed those controls.
        with page.streamlit_reruns.socket.expect_event('framesent'):
            page.evaluate('window.releaseNavigation()')
        page.streamlit_reruns.wait()
        settle(page)
    playwright.expect(save).to_be_enabled()
    playwright.expect(page.get_by_role('radio', name='Import', exact=True)).to_be_checked()
    playwright.expect(page.get_by_role('radio', name='Holdings snapshot', exact=True)).to_be_checked()
    playwright.expect(page.get_by_role('combobox', name='Quantity (required)', exact=True)).to_have_value('Stück/Nennwert Bank')
    save.click()
    page.locator('.js-plotly-plot').first.wait_for()
    assert page.locator('.js-plotly-plot').first.evaluate('el => el.data[0].type') == 'sunburst'
    stored = read_snapshot(path).holdings
    assert stored.shares.tolist() == [2.5]
    assert stored.manual_price.tolist() == [12.]
    assert page.locator('[data-testid="stException"]').count() == 0
    page.get_by_role('button', name='Set up allocation', exact=True).click()
    page.get_by_role('button', name='Enable reviewed allocation', exact=True).wait_for()
    page.get_by_role('button', name='Enable reviewed allocation', exact=True).click()
    page.get_by_role('button', name='Save categories', exact=True).wait_for()
    assert path.with_name('allocation.yaml').exists()
    page.get_by_role('button', name='Connect live prices', exact=True).first.click()
    page.get_by_role('button', name='Search listings', exact=True).click()
    page.get_by_role('combobox', name='Price listing', exact=True).click()
    page.get_by_role('combobox', name='Price listing', exact=True).fill('NVDA')
    page.get_by_role('option', name='NVDA', exact=False).click()
    page.get_by_text('Switch to live prices and clear dated manual prices for this instrument', exact=True).click()
    page.get_by_text('I confirm this is the same instrument/share class and intended exchange listing', exact=True).click()
    page.get_by_role('button', name='Save listing', exact=True).click()
    playwright.expect(page.get_by_text('Listing saved. Live pricing enabled.', exact=True)).to_be_visible()
    linked = read_snapshot(path).holdings
    assert linked.ticker.tolist() == ['NVDA']
    assert linked.manual_price.isna().all()
    assert linked.position_key.tolist() == stored.position_key.tolist()
    # Saving the listing remounts navigation; old and new tabs can coexist until
    # Streamlit finishes the rerun. Wait for the unique live tab, not an arbitrary
    # first match that could belong to the stale tree.
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    exposure = page.get_by_role('tab', name='Exposure', exact=True)
    playwright.expect(exposure).to_have_count(1)
    exposure.click()
    playwright.expect(exposure).to_have_attribute('aria-selected', 'true')
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    playwright.expect(page.locator('[data-testid="stException"]')).to_have_count(0)


def test_cancel_upload_writes_nothing(import_page):
    page, path = import_page
    page.get_by_role('button', name='Import holdings', exact=True).click()
    page.locator('input[type=file]').set_input_files({
        'name': 'invented.csv', 'mimeType': 'text/csv', 'buffer': b'Name;Quantity\nInvented asset;2,5\n',
    })
    page.get_by_role('button', name='Import reviewed positions', exact=True).wait_for()
    page.get_by_role('button', name='Cancel import', exact=True).click()
    page.get_by_role('button', name='Import holdings', exact=True).wait_for()
    assert not path.exists()
    assert page.locator('[data-testid="stException"]').count() == 0


def test_remove_upload_discards_draft_and_new_upload_uses_new_rows(import_page):
    page, path = import_page
    runs = page.streamlit_reruns
    runs.click(page, page.get_by_role('button', name='Import holdings', exact=True))
    uploader = page.locator('input[type=file]')
    uploader.set_input_files({'name': 'first.csv', 'mimeType': 'text/csv',
                             'buffer': b'Name;Quantity\nInvented first;2\n'})
    save = page.get_by_role('button', name='Import reviewed positions', exact=True)
    playwright.expect(save).to_be_visible()
    runs.click(page, page.get_by_role('button', name='Remove first.csv', exact=True))
    playwright.expect(save).to_have_count(0)
    assert not path.exists()
    uploader.set_input_files({'name': 'replacement.csv', 'mimeType': 'text/csv',
                             'buffer': b'Name;Quantity\nInvented replacement;3\n'})
    playwright.expect(save).to_be_visible()
    runs.click(page, page.get_by_text(
        'Quantities are shares/units and prices are amounts per unit (not nominal values or percent quotes)', exact=True))
    runs.click(page, save)
    stored = read_snapshot(path).holdings
    assert stored.name.tolist() == ['Invented replacement']
    assert stored.shares.tolist() == [3.]
