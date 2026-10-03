"""Startup transitions with invented workspaces and no market network."""
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from portfolio_app.demo import create_demo_data
from portfolio_app.positions import read_snapshot
from portfolio_app.settings import theme_options

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture(scope='module')
def intro_server(tmp_path_factory):
    directory = tmp_path_factory.mktemp('synthetic-intro')
    create_demo_data(directory / 'demo')
    app = directory / 'app.py'
    app.write_text('''
from pathlib import Path
from portfolio_app.ui import render_app
render_app(Path(__file__).parent / 'empty', demo_dir=Path(__file__).parent / 'demo', intro=True)
''', encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with (directory / 'server.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(app),
            '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
            '--server.fileWatcherType=none', '--browser.gatherUsageStats=false', *theme_options()], stdout=log, stderr=log)
        try:
            url = f'http://127.0.0.1:{port}'
            for _ in range(100):
                assert process.poll() is None
                try:
                    with urlopen(url + '/_stcore/health', timeout=1):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                pytest.fail('Synthetic startup preview did not start')
            yield url, directory
        finally:
            process.terminate()
            process.wait(timeout=10)


@pytest.fixture
def intro_page(intro_server):
    with playwright.sync_playwright() as runner:
        browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.set_default_timeout(15000)
        yield page, *intro_server
        browser.close()


def select_workspace(page, name):
    page.get_by_role('combobox', name='Portfolio workspace', exact=True).click()
    page.get_by_role('option', name=name, exact=True).click()


def test_intro_welcome_navigation_and_help(intro_page):
    page, url, directory = intro_page
    page.goto(url)
    frame = page.locator('iframe[title*=portfolio_breakdown_intro]')
    playwright.expect(frame).to_be_visible()
    dialog = page.get_by_role('dialog')
    playwright.expect(dialog.get_by_role('button', name='Explore demo', exact=True)).to_be_visible(timeout=15000)
    playwright.expect(dialog.get_by_role('button', name='Start my portfolio', exact=True)).to_be_visible()
    bounds = dialog.locator('..').bounding_box()
    assert bounds['width'] <= 880 and abs(bounds['x'] - (1440 - bounds['width']) / 2) < 2
    playwright.expect(page.get_by_test_id('stSidebar')).to_have_count(0)
    playwright.expect(page.get_by_role('button', name='Import holdings', exact=True)).to_have_count(0)
    page.screenshot(path=str(directory / 'synthetic-welcome.png'))
    dialog.get_by_role('button', name='Explore demo', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    playwright.expect(page.locator('.js-plotly-plot').first).to_be_visible()
    playwright.expect(frame).to_have_count(0)
    page.get_by_role('button', name='?', exact=True).click()
    playwright.expect(page.get_by_text('Try the demo', exact=True)).to_be_visible()
    page.keyboard.press('Escape')
    page.get_by_role('button', name='Settings', exact=False).click()
    page.get_by_role('radio', name='%', exact=True).click()
    page.keyboard.press('Escape')
    select_workspace(page, 'My portfolio')
    playwright.expect(dialog).to_be_visible()
    dialog.get_by_role('button', name='Start my portfolio', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    playwright.expect(page.get_by_role('button', name='Import portfolio — experimental', exact=True)).to_be_visible()
    page.get_by_role('button', name=re.compile('Add position$')).click()
    playwright.expect(dialog.get_by_role('textbox', name='Instrument name', exact=True)).to_have_value('')
    dialog.get_by_role('button', name='Cancel', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    assert not (directory / 'empty' / 'holdings.csv').exists()
    select_workspace(page, 'Demo portfolio')
    playwright.expect(page.locator('.js-plotly-plot').first).to_be_visible()
    playwright.expect(frame).to_have_count(0)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    assert read_snapshot(directory / 'demo' / 'holdings.csv').holdings.shares.iloc[0] == 450
    page.screenshot(path=str(directory / 'synthetic-header.png'))


def test_reduced_motion_and_narrow_welcome(intro_page):
    page, url, directory = intro_page
    page.emulate_media(reduced_motion='reduce')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(url)
    dialog = page.get_by_role('dialog')
    playwright.expect(dialog).to_be_visible(timeout=10000)
    for name in ['Explore demo', 'Start my portfolio']:
        button = dialog.get_by_role('button', name=name, exact=True)
        button.scroll_into_view_if_needed()
        bounds = button.bounding_box()
        assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
    assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
    page.screenshot(path=str(directory / 'synthetic-welcome-narrow.png'))
    dialog.get_by_role('button', name='Explore demo', exact=True).click()
    playwright.expect(page.locator('.js-plotly-plot').first).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    page.screenshot(path=str(directory / 'synthetic-header-narrow.png'))


def test_skip_works_when_animation_resource_fails(intro_page):
    page, url, _ = intro_page
    page.route('**/component/portfolio_app.intro.portfolio_breakdown_intro/**', lambda route: route.abort())
    page.goto(url)
    page.get_by_role('button', name='Skip intro', exact=True).click()
    playwright.expect(page.get_by_role('dialog')).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_animation_standalone_replay_and_exact_final_svg(intro_page):
    page, _, _ = intro_page
    asset = Path(__file__).resolve().parents[1] / 'src/portfolio_app/intro_frontend/index.html'
    page.goto(asset.as_uri())
    page.wait_for_function('window.BreakdownIntro?.completed === true')
    original = asset.parent.parent / 'assets/portfolio-breakdown.svg'
    from xml.etree import ElementTree as ET
    paths = [p.attrib['d'] for p in ET.fromstring(original.read_text()).findall('{http://www.w3.org/2000/svg}path')]
    assert page.locator('#canonical path').evaluate_all('nodes => nodes.map(n => n.getAttribute("d"))') == paths
    page.get_by_role('button', name='Replay', exact=False).click()
    assert page.evaluate('window.BreakdownIntro.playing')
    page.get_by_role('button', name='Pause', exact=True).click()
    assert not page.evaluate('window.BreakdownIntro.playing')
    page.emulate_media(reduced_motion='reduce')
    page.get_by_role('button', name='Replay', exact=False).click()
    page.wait_for_function('window.BreakdownIntro.completed && !window.BreakdownIntro.playing')
