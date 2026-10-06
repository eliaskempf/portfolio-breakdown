"""Startup transitions with invented workspaces and no market network."""
import os
import json
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


@pytest.fixture
def intro_server(tmp_path_factory):
    directory = tmp_path_factory.mktemp('synthetic-intro')
    create_demo_data(directory / 'demo')
    fixture_path = directory / 'demo' / 'demo_prices.json'
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    fixture['prices']['spot:gold:USD:troy_oz'] = dict(price=2000., currency='USD', observed_at='2026-01-05T00:00:00Z')
    fixture['fx']['USD'] = dict(price=.8, currency='EUR', observed_at='2026-01-05T00:00:00Z')
    fixture_path.write_text(json.dumps(fixture), encoding='utf-8')
    app = directory / 'app.py'
    app.write_text('''
from pathlib import Path
from portfolio_app.ui import render_app
from portfolio_app.prices import PriceService, StaticProvider
render_app(Path(__file__).parent / 'empty', demo_dir=Path(__file__).parent / 'demo', intro=True,
           price_service=PriceService(StaticProvider(Path(__file__).parent / 'demo' / 'demo_prices.json')))
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


def test_native_startup_asset_plays_centered_without_a_server(intro_page):
    from portfolio_app.window import startup_html
    page, _, _ = intro_page
    page.set_content(startup_html())
    page.wait_for_function('window.BreakdownIntro?.playing')
    page.evaluate('window.BreakdownIntro.pause()')
    for width, height in [(1440, 1000), (650, 500), (390, 844)]:
        page.set_viewport_size({'width': width, 'height': height})
        scene = page.locator('#scene').bounding_box()
        assert abs(scene['x'] + scene['width'] / 2 - width / 2) < 2
        assert abs(scene['y'] + scene['height'] / 2 - (height + 48) / 2) < 2
    playwright.expect(page.locator('.controls')).to_be_hidden()
    page.evaluate('window.BreakdownIntro.replay()')
    page.wait_for_function('window.BreakdownIntro.completed')


def open_window_splash(page, url, content='<p>Loading synthetic app</p>'):
    from portfolio_app.window_splash import startup_html
    page.route(url + '/__portfolio_window__', lambda route: route.fulfill(content_type='text/html', body=startup_html()))
    page.route(url + '/synthetic-app', lambda route: route.fulfill(content_type='text/html', body=content))
    page.goto(url + '/__portfolio_window__')
    assert page.evaluate('(url) => PortfolioSplash.open(url)', url + '/synthetic-app')
    frame = page.frame_locator('#portfolio-app')
    playwright.expect(frame.locator('body')).not_to_be_empty()
    return frame


READY_VIEW = '''<button role="tab" aria-selected="true">Overview</button>
<div class="js-plotly-plot"><svg class="main-svg"></svg></div>'''


def test_window_splash_holds_until_all_charts_draw_without_reloading(intro_page):
    page, url, _ = intro_page
    frame = open_window_splash(page, url)
    page.wait_for_function('BreakdownIntro.completed')
    playwright.expect(page.locator('main')).to_be_visible()
    assert page.locator('#scene').evaluate('e => getComputedStyle(e).animationName') == 'loading-pulse'
    frame.locator('body').evaluate('(e, html) => e.innerHTML = html', READY_VIEW + '<div id="pending" class="js-plotly-plot"></div>')
    page.wait_for_timeout(500)
    playwright.expect(page.locator('main')).to_be_visible()
    frame.locator('#pending').evaluate('e => e.innerHTML = \'<svg class="main-svg"></svg>\'')
    playwright.expect(page.locator('main')).to_have_count(0)
    assert page.evaluate('PortfolioSplash.state') == 'first-view-rendered'
    playwright.expect(frame.locator('#pending .main-svg')).to_have_count(1)
    assert page.locator('#portfolio-app').evaluate('e => e.inert') is False


def test_window_fast_load_finishes_animation_before_reveal(intro_page):
    page, url, _ = intro_page
    open_window_splash(page, url, READY_VIEW)
    page.evaluate('BreakdownIntro.pause()')
    page.wait_for_timeout(500)
    playwright.expect(page.locator('main')).to_be_visible()
    page.evaluate('BreakdownIntro.finish()')
    playwright.expect(page.locator('main')).to_have_count(0)


def test_window_splash_reduced_motion_and_error_reveal(intro_page):
    page, url, _ = intro_page
    page.emulate_media(reduced_motion='reduce')
    frame = open_window_splash(page, url)
    assert page.evaluate('BreakdownIntro.completed')
    assert page.locator('#scene').evaluate('e => getComputedStyle(e).animationName') == 'none'
    frame.locator('body').evaluate('e => e.innerHTML = \'<div data-testid="stException">Synthetic failure</div>\'')
    playwright.expect(page.locator('main')).to_have_count(0)
    assert page.evaluate('PortfolioSplash.state') == 'failed'
    playwright.expect(frame.get_by_text('Synthetic failure')).to_be_visible()


def test_window_splash_slow_start_has_a_way_to_show_app(intro_page):
    page, url, _ = intro_page
    page.clock.install()
    open_window_splash(page, url)
    page.clock.fast_forward(61000)
    playwright.expect(page.get_by_text('Loading is taking longer than expected.')).to_be_visible()
    page.get_by_role('button', name='Show application').click()
    page.clock.fast_forward(500)
    playwright.expect(page.locator('main')).to_have_count(0)


def test_window_splash_reveals_actual_welcome_and_navigation(intro_page):
    from portfolio_app.window_splash import startup_html
    page, url, _ = intro_page
    page.route(url + '/__portfolio_window__', lambda route: route.fulfill(content_type='text/html', body=startup_html()))
    page.goto(url + '/__portfolio_window__')
    page.evaluate('(url) => PortfolioSplash.open(url)', url)
    playwright.expect(page.locator('main')).to_have_count(0, timeout=20000)
    frame = page.frame_locator('#portfolio-app')
    frame.get_by_role('button', name='Explore demo', exact=True).click()
    frame.get_by_role('button', name='Not now', exact=True).click()
    playwright.expect(frame.locator('.js-plotly-plot').first).to_be_visible()
    frame.get_by_role('tab', name='Positions', exact=True).click()
    playwright.expect(frame.get_by_test_id('stException')).to_have_count(0)


def test_window_splash_reveals_actual_input_error(intro_page):
    from portfolio_app.window_splash import startup_html
    page, url, directory = intro_page
    workspace = directory / 'empty'
    workspace.mkdir(exist_ok=True)
    (workspace / 'holdings.csv').write_text('synthetic,invalid,columns\nexample,only,here\n')
    page.route(url + '/__portfolio_window__', lambda route: route.fulfill(content_type='text/html', body=startup_html()))
    page.goto(url + '/__portfolio_window__')
    page.evaluate('(url) => PortfolioSplash.open(url)', url)
    frame = page.frame_locator('#portfolio-app')
    playwright.expect(frame.get_by_test_id('stAlert').first).to_be_visible(timeout=20000)
    playwright.expect(page.locator('main')).to_have_count(0)
    playwright.expect(frame.locator('[data-portfolio-view-ready="true"]')).to_have_count(1)


def test_window_controls_and_skip_intro(intro_page):
    from portfolio_app.window_splash import startup_html
    page, url, _ = intro_page
    page.route(url + '/__portfolio_window__', lambda route: route.fulfill(content_type='text/html', body=startup_html(False)))
    page.goto(url + '/__portfolio_window__')
    playwright.expect(page.locator('main')).to_have_count(0)
    playwright.expect(page.get_by_role('button', name='Fullscreen', exact=True)).to_be_visible()
    playwright.expect(page.get_by_role('button', name='Exit', exact=True)).to_be_hidden()
    page.evaluate('window.commands = []; window.chrome ??= {}; window.chrome.webview = {postMessage: c => commands.push(c)}')
    page.get_by_role('button', name='Fullscreen', exact=True).click()
    page.evaluate('PortfolioSplash.setFullscreen(true)')
    playwright.expect(page.get_by_role('button', name='Windowed', exact=True)).to_be_visible()
    exit_button = page.get_by_role('button', name='Exit', exact=True)
    playwright.expect(exit_button).to_be_visible()
    exit_button.focus()
    page.keyboard.press('Enter')
    assert page.evaluate('commands') == ['portfolio:fullscreen', 'portfolio:exit']
    page.evaluate('PortfolioSplash.setFullscreen(false)')
    playwright.expect(exit_button).to_be_hidden()


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
    page.get_by_role('button', name='Not now', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    playwright.expect(page.locator('.js-plotly-plot').first).to_be_visible()
    playwright.expect(frame).to_have_count(0)
    page.get_by_role('button', name='?', exact=True).click()
    playwright.expect(page.get_by_role('link', name='User guide', exact=False)).to_be_visible()
    playwright.expect(page.get_by_role('link', name='ETF breakdowns', exact=True)).to_have_attribute('href', re.compile(r'/exposure/$'))
    page.keyboard.press('Escape')
    page.get_by_role('button', name='Settings', exact=False).click()
    page.get_by_role('radio', name='%', exact=True).click()
    page.keyboard.press('Escape')
    select_workspace(page, 'My portfolio')
    playwright.expect(dialog).to_be_visible()
    dialog.get_by_role('button', name='Start my portfolio', exact=True).click()
    dialog.get_by_role('button', name='Skip setup', exact=True).click()
    page.get_by_role('tab', name='Positions', exact=True).click()
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
    assert read_snapshot(directory / 'demo' / 'holdings.csv').holdings.shares.iloc[0] == pytest.approx(449.6327)
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
    page.get_by_role('button', name='Not now', exact=True).click()
    playwright.expect(page.locator('.js-plotly-plot').first).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    page.screenshot(path=str(directory / 'synthetic-header-narrow.png'))


def test_intro_automatically_continues_when_animation_resource_fails(intro_page):
    page, url, _ = intro_page
    page.route('**/component/portfolio_app.intro.portfolio_breakdown_intro/**', lambda route: route.abort())
    page.goto(url)
    playwright.expect(page.get_by_role('button', name='Skip intro', exact=True)).to_have_count(0)
    playwright.expect(page.get_by_role('dialog')).to_be_visible(timeout=15000)
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


def test_embedded_wordmark_has_separate_letters_before_swirl(intro_page):
    page, url, _ = intro_page
    page.goto(url)
    frame = page.frame_locator('iframe[title*=portfolio_breakdown_intro]')
    playwright.expect(frame.locator('body')).to_have_attribute('data-phase', 'wordmark')
    frame.locator('body').evaluate('() => BreakdownIntro.pause()')
    # Center in the app viewport, including after resizing during playback.
    for width, height in [(1440, 1000), (650, 500), (390, 844)]:
        page.set_viewport_size({'width': width, 'height': height})
        page.wait_for_function('''() => {
            const r = document.querySelector('iframe[title*=portfolio_breakdown_intro]')?.getBoundingClientRect();
            return r && Math.abs(r.x+r.width/2-innerWidth/2)<2
                && Math.abs(r.y+r.height/2-innerHeight/2)<2;
        }''')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    boxes = frame.locator('#letters text').evaluate_all('els => els.map(el => el.getBoundingClientRect().toJSON())')
    assert len(boxes) == 9
    assert all(right['x'] > left['x'] + left['width'] * .7 for left, right in zip(boxes, boxes[1:]))
    assert max(box['y'] + box['height']/2 for box in boxes) - min(box['y'] + box['height']/2 for box in boxes) < 2
    # Replay must remeasure visible letters too, including after the final mark.
    frame.locator('body').evaluate('() => { BreakdownIntro.seek(BreakdownIntro.duration); BreakdownIntro.replay(); }')
    playwright.expect(frame.locator('body')).to_have_attribute('data-phase', 'wordmark')
    frame.locator('body').evaluate('() => BreakdownIntro.pause()')
    replay = frame.locator('#letters text').evaluate_all('els => els.map(el => el.getBoundingClientRect().x)')
    assert all(right > left + 15 for left, right in zip(replay, replay[1:]))


@pytest.mark.parametrize('theme', ['Light', 'Dark'])
def test_welcome_cards_follow_selected_theme_with_readable_text(intro_page, theme):
    page, url, _ = intro_page
    page.emulate_media(reduced_motion='reduce')
    page.goto(url)
    page.get_by_role('button', name='Explore demo', exact=True).click()
    page.get_by_role('button', name='Not now', exact=True).click()
    page.get_by_role('button', name='Main menu', exact=True).click()
    page.get_by_test_id(f'stMainMenuItem-theme-{theme}').click()
    page.keyboard.press('Escape')
    select_workspace(page, 'My portfolio')
    playwright.expect(page.get_by_role('dialog')).to_be_visible()
    for reload in (False, True):
        if reload:
            page.reload()
            playwright.expect(page.get_by_role('dialog')).to_be_visible()
        for card in ('welcome_demo', 'welcome_personal'):
            colors = page.locator(f'.st-key-{card}').evaluate('''el => {
                const ctx = document.createElement('canvas').getContext('2d');
                const ancestors = [];
                for (let node = el; node; node = node.parentElement) ancestors.unshift(node);
                ctx.fillStyle = 'white'; ctx.fillRect(0, 0, 1, 1);
                for (const node of ancestors) {
                    ctx.fillStyle = getComputedStyle(node).backgroundColor;
                    ctx.fillRect(0, 0, 1, 1);
                }
                const bg = [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3);
                const text = [...el.querySelectorAll('[data-testid="stMarkdownContainer"] p')].filter(p => !p.closest('button')).map(p => {
                    ctx.fillStyle = `rgb(${bg.join(',')})`; ctx.fillRect(0, 0, 1, 1);
                    ctx.fillStyle = getComputedStyle(p).color; ctx.fillRect(0, 0, 1, 1);
                    return [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3);
                });
                return {bg, text};
            }''')
            def luminance(rgb):
                channels = [v/255 for v in rgb]
                linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in channels]
                return sum(v*w for v, w in zip(linear, (.2126, .7152, .0722)))
            background = luminance(colors['bg'])
            assert (background < .1) if theme == 'Dark' else (background > .7)
            assert colors['text']
            for rgb in colors['text']:
                foreground = luminance(rgb)
                assert (max(background, foreground)+.05)/(min(background, foreground)+.05) >= 4.5
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.get_by_role('dialog').evaluate('el => el.scrollWidth <= el.clientWidth + 1')
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_guided_setup_and_physical_asset_in_dark_narrow_view(intro_page):
    page, url, directory = intro_page
    page.emulate_media(color_scheme='dark', reduced_motion='reduce')
    page.set_viewport_size({'width': 650, 'height': 950})
    page.goto(url)
    page.get_by_role('button', name='Start my portfolio', exact=True).click()
    dialog = page.get_by_role('dialog')
    playwright.expect(dialog.get_by_text('1 of 2 · Categories and optional targets', exact=True)).to_be_visible()
    dialog.get_by_role('textbox', name='Category name', exact=True).fill('Gold')
    dialog.get_by_role('button', name='Add category', exact=True).click()
    playwright.expect(dialog.get_by_role('textbox', name='Category 1', exact=True)).to_have_value('Gold')
    playwright.expect(page.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning')
    playwright.expect(dialog.get_by_role('textbox', name='Category name', exact=True)).to_have_count(1)
    playwright.expect(dialog.get_by_role('textbox', name='Category name', exact=True)).to_have_value('')
    playwright.expect(dialog.get_by_role('textbox', name='Category name', exact=True)).to_be_focused()
    page.screenshot(path=str(directory / 'synthetic-guided-categories.png'))
    dialog.get_by_role('button', name='Save categories & continue', exact=True).click()
    playwright.expect(page.get_by_role('dialog', name='Add position', exact=True)).to_be_visible()
    dialog.get_by_role('radio', name='Physical asset', exact=True).click()
    dialog.get_by_text('Manual price', exact=True).click()
    playwright.expect(dialog.get_by_role('spinbutton', name='Current price per troy oz (optional)', exact=True)).to_be_visible()
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    dialog.get_by_role('textbox', name='Instrument name', exact=True).fill('Invented bullion')
    dialog.get_by_role('spinbutton', name='Quantity held (total)', exact=True).fill('2.5')
    dialog.get_by_role('spinbutton', name='Current price per troy oz (optional)', exact=True).fill('2000')
    dialog.get_by_role('combobox', name='Category', exact=True).click()
    page.get_by_role('option', name='Gold', exact=True).click()
    dialog.get_by_text('Buy-in (optional)', exact=True).click()
    dialog.get_by_role('spinbutton', name='Average buy-in per unit (optional)', exact=True).fill('1500')
    page.screenshot(path=str(directory / 'synthetic-physical-position.png'))
    assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
    dialog.get_by_role('button', name='Save position', exact=True).click()
    page.get_by_role('dialog', name='Welcome to Breakdown!', exact=True).get_by_role('button', name='Not now', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    playwright.expect(page.get_by_role('tab', name='Overview', exact=True)).to_have_attribute('aria-selected', 'true')
    page.get_by_role('tab', name='Positions', exact=True).click()
    playwright.expect(page.get_by_role('table', name='Positions', exact=True).get_by_text('Invented bullion', exact=True)).to_be_visible()
    stored = read_snapshot(directory / 'empty' / 'holdings.csv').holdings.iloc[0]
    assert stored.quantity_unit == 'troy oz' and stored.manual_price == 2000. and stored.shares == 2.5
    assert stored.bucket_id and stored.acquisition_price == 1500.
    page.reload()
    playwright.expect(page.get_by_role('tab', name='Overview', exact=True)).to_be_visible()
    playwright.expect(page.get_by_role('button', name='Start my portfolio', exact=True)).to_have_count(0)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_category_rows_keyboard_focus_and_target_confirmation(intro_page):
    page, url, directory = intro_page
    page.emulate_media(reduced_motion='reduce')
    page.goto(url)
    page.get_by_role('button', name='Start my portfolio', exact=True).click()
    dialog = page.get_by_role('dialog')
    name = dialog.get_by_role('textbox', name='Category name', exact=True)
    playwright.expect(name).to_be_focused()
    playwright.expect(name).to_have_value('')
    name.fill('Invented equity')
    page.keyboard.press('Tab')
    target = dialog.get_by_role('spinbutton', name='Target (%) · optional', exact=True)
    playwright.expect(target).to_be_focused()
    page.keyboard.press('Shift+Tab')
    playwright.expect(name).to_be_focused()
    page.keyboard.press('Tab')
    target.fill('75')
    page.keyboard.press('Enter')
    playwright.expect(dialog.get_by_role('textbox', name='Category 1', exact=True)).to_have_value('Invented equity')
    playwright.expect(page.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning')
    playwright.expect(name).to_have_count(1)
    playwright.expect(name).to_have_value('')
    playwright.expect(name).to_be_focused()
    playwright.expect(dialog.get_by_role('button', name='Help for Category 1', exact=True)).to_have_count(0)
    playwright.expect(dialog.get_by_role('button', name='Help for Target 1 (%)', exact=True)).to_have_count(0)
    # Help remains available with a pointer; its contents also describe the inputs.
    playwright.expect(name).to_have_attribute('aria-description', re.compile('Examples: Equities'))
    name.fill('Invented <reserve> & cash')
    page.keyboard.press('Tab')
    playwright.expect(target).to_be_focused()
    target.fill('25')
    page.keyboard.press('Enter')
    playwright.expect(page.get_by_role('dialog', name='All set?', exact=True)).to_be_visible()
    table = dialog.get_by_role('table', name='Category targets', exact=True)
    playwright.expect(table.get_by_role('row')).to_have_count(3)
    playwright.expect(table.get_by_role('rowheader', name='Invented <reserve> & cash', exact=True)).to_be_visible()
    playwright.expect(table.get_by_role('cell', name='25%', exact=True)).to_be_visible()
    assert table.locator('td').first.evaluate("el => getComputedStyle(el).textAlign") == 'right'
    assert not (directory / 'empty' / 'allocation.yaml').exists()
    page.get_by_role('button', name='Keep editing', exact=True).click()
    playwright.expect(dialog.get_by_role('textbox', name='Category 2', exact=True)).to_have_value('Invented <reserve> & cash')
    # An unchanged 100% total must not immediately reopen the confirmation.
    dialog.get_by_role('spinbutton', name='Target 2 (%)', exact=True).fill('20')
    page.keyboard.press('Tab')
    playwright.expect(dialog.get_by_text('Target total: 95% of portfolio', exact=True)).to_be_visible()
    dialog.get_by_role('spinbutton', name='Target 2 (%)', exact=True).fill('25')
    page.keyboard.press('Tab')
    # The acknowledged allocation is unchanged, so explicit Continue remains available.
    playwright.expect(dialog.get_by_text('Target total: 100% of portfolio', exact=True)).to_be_visible()
    # The total renders before Streamlit removes the previous form's controls.
    playwright.expect(page.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning')
    save = dialog.get_by_role('button', name='Save categories & continue', exact=True)
    playwright.expect(save).to_have_count(1)
    save.click()
    playwright.expect(page.get_by_role('dialog', name='Add position', exact=True)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_gold_spot_weight_save_reload_and_details(intro_page):
    page, url, directory = intro_page
    page.emulate_media(reduced_motion='reduce')
    page.goto(url)
    page.get_by_role('button', name='Start my portfolio', exact=True).click()
    page.get_by_role('button', name='Skip setup', exact=True).click()
    page.get_by_role('button', name='Not now', exact=True).click()
    page.get_by_role('tab', name='Positions', exact=True).click()
    page.get_by_role('button', name='Add position', exact=False).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('radio', name='Physical asset', exact=True).click()
    playwright.expect(dialog.get_by_role('radio', name='Gold spot price', exact=True)).to_be_checked()
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    dialog.get_by_role('textbox', name='Instrument name', exact=True).fill('Invented spot gold')
    dialog.get_by_role('spinbutton', name='Quantity held (total)', exact=True).fill('2.5')
    dialog.get_by_role('button', name='Save position', exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    rows = read_snapshot(directory / 'empty' / 'holdings.csv').holdings
    assert rows.price_source.tolist() == ['gold_spot'] and rows.quantity_unit.tolist() == ['troy oz']
    page.get_by_role('tab', name='Overview', exact=True).click()
    playwright.expect(page.get_by_test_id('stMetricValue').first).to_have_text('€4,000.00')
    page.reload()
    playwright.expect(page.get_by_test_id('stMetricValue').first).to_have_text('€4,000.00')
    page.get_by_role('tab', name='Positions', exact=True).click()
    page.get_by_role('table', name='Positions', exact=True).get_by_text('Invented spot gold', exact=True).click()
    playwright.expect(dialog.get_by_text('Gold spot valuation uses the latest available quote. Gold price history is not available here yet.', exact=True)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
