"""Capture guide crops from a fresh offline synthetic app; never open user data."""
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

from portfolio_app.demo_data.public_demo import create_public_demo
from portfolio_app.settings import theme_options

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / 'dist/docs-screenshots'
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='portfolio-docs-synthetic-') as temporary:
        work = Path(temporary)
        create_public_demo(work / 'demo', live=False)
        app = work / 'app.py'
        app.write_text('''from pathlib import Path
from portfolio_app.interface.ui import render_app
from portfolio_app.market.prices import PriceService, StaticProvider
directory = Path(__file__).parent / 'demo'
render_app(directory, demo=True,
           price_service=PriceService(StaticProvider(directory / 'demo_prices.json')))
''', encoding='utf-8')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        environment = dict(os.environ, PORTFOLIO_STATE_DIR=str(work / 'state'))
        with (output / 'server.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([
                sys.executable, '-m', 'streamlit', 'run', str(app),
                '--server.address=127.0.0.1', f'--server.port={port}',
                '--server.headless=true', '--server.fileWatcherType=none',
                '--browser.gatherUsageStats=false', *theme_options(),
            ], stdout=log, stderr=log, env=environment, cwd=ROOT)
            try:
                url = f'http://127.0.0.1:{port}'
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Synthetic preview exited; inspect dist/docs-screenshots/server.log')
                    try:
                        with urlopen(url + '/_stcore/health', timeout=1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Synthetic preview did not start')
                print(f'Synthetic preview: {url}; checkout: {ROOT}; data: {work / "demo"}', flush=True)
                capture(url, output)
            finally:
                process.terminate()
                process.wait(timeout=15)
    print(f'Review the synthetic Overview screenshot and three guide crops in {output}.')


def capture(url: str, output: Path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1700},
                                    device_scale_factor=2, color_scheme='dark', reduced_motion='reduce')
            page.set_default_timeout(30000)
            errors, external = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def route_request(route):
                if route.request.url.startswith(url + '/'):
                    route.continue_()
                else:
                    external.append(route.request.url)
                    route.abort()

            page.route('**/*', route_request)

            def idle():
                page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
                page.evaluate('document.fonts.ready')
                expect(page.get_by_test_id('stException')).to_have_count(0)

            page.goto(url)
            expect(page.get_by_role('tab', name='Overview', exact=True)).to_be_visible()
            allocation = page.get_by_role('radio', name='Allocation', exact=True)
            expect(allocation).to_be_checked()
            chart = page.locator('.st-key-overview_allocation .js-plotly-plot')
            expect(chart.get_by_text('Emergency fund', exact=True)).to_be_visible()
            idle()
            modes = page.get_by_role('radiogroup').filter(has=allocation).bounding_box()
            plot = chart.bounding_box()
            top = modes['y'] - 12
            page.screenshot(path=str(output / 'demo-overview.png'), animations='disabled',
                            clip={'x': 20, 'y': top, 'width': 1400, 'height': plot['y'] + plot['height'] + 10 - top})
            page.emulate_media(color_scheme='light')
            page.get_by_role('tab', name='Exposure', exact=True).click()
            table = page.get_by_role('table', name='Exposure assets', exact=True)
            expect(table).to_contain_text('Nvidia')
            idle()
            table.get_by_text('Nvidia', exact=True).click()
            page.get_by_role('button', name='Details for Nvidia', exact=True).click()
            dialog = page.get_by_role('dialog')
            dialog.get_by_text('ETF breakdown: Xtrackers MSCI World', exact=True).click()
            expect(dialog.get_by_role('table', name='Xtrackers MSCI World holdings', exact=True)).to_contain_text('Other')
            idle()
            dialog.get_by_test_id('stExpander').filter(has_text='ETF breakdown: Xtrackers MSCI World').screenshot(
                path=str(output / 'demo-etf-breakdown.png'), scale='css', animations='disabled')
            page.get_by_role('button', name='Close exposure details', exact=True).click()
            page.get_by_role('tab', name='Rebalance', exact=True).click()
            page.get_by_role('tab', name='Targets', exact=True).click()
            page.get_by_role('combobox', name='Allocation category', exact=True).click()
            page.get_by_role('option', name='Equities', exact=True).click()
            page.get_by_role('button', name='Edit targets', exact=True).click()
            dialog = page.get_by_role('dialog')
            expect(dialog.get_by_text('100% assigned',exact=True)).to_be_visible()
            expect(dialog.locator('canvas').first).to_be_visible()
            idle()
            page.mouse.move(0, 0)
            dialog.screenshot(path=str(output / 'demo-targets.png'), scale='css', animations='disabled')
            dialog.get_by_role('button', name='Cancel', exact=True).click()
            expect(dialog).to_have_count(0)
            page.get_by_role('tab', name='Plan', exact=True).click()
            page.get_by_role('button', name='Calculate plan', exact=True).click()
            expect(page.get_by_role('heading', name='Suggested trades', exact=True)).to_be_visible()
            idle()
            page.get_by_role('heading', name='Suggested trades', exact=True).click()
            page.mouse.move(0, 0)
            page.wait_for_timeout(800)  # Let the calculation button's tooltip dismiss.
            page.locator('.st-key-rebalance_tabs').screenshot(
                path=str(output / 'demo-contribution.png'), scale='css', animations='disabled')
            assert not errors, errors
            assert not external, external
        finally:
            browser.close()


if __name__ == '__main__':
    main()
