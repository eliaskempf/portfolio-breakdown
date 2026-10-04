"""Browser smoke check of generated public docs only; never serve the checkout."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import sync_playwright


def main():
    site = Path('dist/docs-site').resolve()
    info = json.loads((site / 'build-info.json').read_text())
    prefix = urlsplit(info['site_url']).path
    with TemporaryDirectory(prefix='portfolio-public-docs-') as temporary:
        public = Path(temporary)
        shutil.copytree(site, public / prefix.strip('/'))
        handler = partial(SimpleHTTPRequestHandler, directory=str(public))
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}{prefix}'
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                errors, external, failed = [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('response', lambda response: failed.append(response.url) if response.status >= 400 else None)

                def route_request(route):
                    if urlsplit(route.request.url).hostname not in {'127.0.0.1', 'localhost'}:
                        external.append(route.request.url)
                        route.abort()
                    else:
                        route.continue_()

                page.route('**/*', route_request)
                page.goto(url)
                page.get_by_role('button', name='Analysis', exact=True).click()
                page.get_by_role('link', name='Exposure and ETFs', exact=True).first.click()
                page.locator('h1#look-through').wait_for()
                page.locator('a[data-bs-target="#mkdocs_search_modal"]').click()
                page.locator('#mkdocs-search-query').press_sequentially('buy-in', delay=80)
                result = page.locator('#mkdocs-search-results a').first
                result.wait_for()
                result_url = urljoin(page.url, result.get_attribute('href'))
                result.click()
                page.wait_for_url(result_url, wait_until='domcontentloaded')
                page.locator('h1').wait_for()
                for route in info['topics'].values():
                    response = page.goto(url + route)
                    assert response is None or response.ok, route  # Fragment-only navigation has no HTTP response.
                    anchor = urlsplit(route).fragment
                    assert page.locator(f'[id="{anchor}"]').count() == 1, route
                page.set_viewport_size({'width': 390, 'height': 844})
                page.goto(url)
                page.get_by_role('button', name='Toggle navigation').click()
                page.get_by_role('button', name='Help', exact=True).click()
                page.get_by_role('link', name='Troubleshooting', exact=True).first.click()
                page.locator('h1#troubleshooting').wait_for()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Narrow layout overflow'
                page.get_by_role('button', name='Toggle navigation').click()
                page.locator('a[data-bs-target="#mkdocs_search_modal"]').click()
                page.locator('#mkdocs-search-query').press_sequentially('restore', delay=80)
                page.locator('#mkdocs-search-results a').first.wait_for()
                for route in sorted({urlsplit(route).path for route in info['topics'].values()}):
                    page.goto(url + route)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), route
                assert not errors, errors
                assert not failed, failed
                assert not external, external
                browser.close()
                print(f'Browser passed: {url}; navigation, search, 30 topics, 390px layout, no external requests')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main()
