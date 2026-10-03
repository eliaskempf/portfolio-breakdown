"""Real-browser smoke test against an installed or extracted app; invented data only."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen
import json
import re
import csv
from io import BytesIO
from zipfile import ZipFile

from playwright.sync_api import sync_playwright, expect


def synthetic_workbook():
    """Exercise the packaged Excel engine with a genuine, invented XLSX."""
    output = BytesIO()
    with ZipFile(output, 'w') as book:
        book.writestr('[Content_Types].xml', '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>''')
        book.writestr('_rels/.rels', '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        book.writestr('xl/workbook.xml', '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>
<sheet name="Synthetic holdings" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        book.writestr('xl/_rels/workbook.xml.rels', '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>''')
        book.writestr('xl/worksheets/sheet1.xml', '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>Name</t></is></c><c r="B1" t="inlineStr"><is><t>Quantity</t></is></c></row>
<row r="2"><c r="A2" t="inlineStr"><is><t>Synthetic imported Excel</t></is></c><c r="B2"><v>2.5</v></c></row>
</sheetData></worksheet>''')
    return output.getvalue()


def wait_for_instance(state, process):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Packaged launcher exited: {process.returncode}')
        for path in (state / 'sessions').glob('*.json'):
            try:
                from urllib.request import Request
                info = json.loads(path.read_text(encoding='utf-8'))
                request = Request(f'http://127.0.0.1:{info["control_port"]}/status',
                                  headers={'Authorization': 'Bearer ' + info['token']})
                with urlopen(request, timeout=1) as response:
                    status = json.load(response)
                if status['ready']:
                    return status['url']
            except (OSError, ValueError, KeyError):
                pass
        time.sleep(.2)
    raise RuntimeError('Packaged application did not become ready.')


def add_synthetic_fund(workspace):
    """Seed an invented, manually priced fund for the packaged CSV setup route."""
    path = workspace / 'holdings.csv'
    with path.open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        rows, columns = list(reader), reader.fieldnames
    fund = dict(id='synthetic-bond-fund', name='Synthetic bond fund', isin='ZZ0000009991',
                shares='1', instrument_type='etf', manual_price='100', manual_price_currency='EUR',
                manual_price_date='2026-01-02', quantity_unit='units')
    if 'position_key' in columns:
        fund['position_key'] = 'synthetic-bond-position'
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(dict.fromkeys([*columns, *fund])))
        writer.writeheader()
        writer.writerows([*rows, fund])
    preferences = workspace / '.cache' / 'etf-refresh' / 'preferences.json'
    preferences.parent.mkdir(parents=True, exist_ok=True)
    preferences.write_text(json.dumps({'enabled': False, 'minimum_age_days': 1}), encoding='utf-8')


def exercise_manual_breakdown(page):
    page.get_by_role('tab', name='Exposure', exact=True).click()
    page.get_by_role('button', name='Data & settings', exact=True).click()
    page.get_by_text('ETF refresh & snapshots', exact=True).click()
    page.get_by_text('Set up a breakdown', exact=True).click()
    position = page.get_by_role('combobox', name='Fund position', exact=True)
    position.click()
    position.fill('Synthetic bond fund')
    page.get_by_role('option', name='Synthetic bond fund', exact=True).click()
    page.get_by_text('Normalized holdings CSV', exact=True).click()
    page.locator('input[type=file]').set_input_files({
        'name': 'invented-bonds.csv', 'mimeType': 'text/csv',
        'buffer': (b'constituent_id,name,ticker,isin,weight,instrument_type,issuer,country,market_currency,maturity\n'
                   b'bond-one,Invented bond one,,ZZ0000000016,0.6,bond,Invented issuer,Invented country,EUR,2030-01-02\n'
                   b'bond-two,Invented bond two,,ZZ0000000024,0.3,bond,Invented issuer,Invented country,EUR,2036-01-02\n'),
    })
    page.get_by_role('combobox', name='Physical fund asset class', exact=True).click()
    page.get_by_role('combobox', name='Physical fund asset class', exact=True).fill('fixed_income')
    page.get_by_role('option', name='fixed_income', exact=True).click()
    page.get_by_role('button', name='Preview breakdown', exact=True).click()
    expect(page.get_by_text('90.00% represented', exact=False)).to_be_visible()
    page.get_by_role('button', name='Save breakdown', exact=True).click()
    panel = page.get_by_text('ETF breakdown: Synthetic bond fund', exact=True)
    expect(panel).to_be_visible()
    panel.click()
    page.get_by_role('combobox', name='Summarize by', exact=True).wait_for()
    expect(page.locator('.js-plotly-plot').last).to_be_visible()
    page.get_by_role('radio', name='Holdings', exact=True).click()
    table = page.get_by_role('table', name='Synthetic bond fund holdings', exact=True)
    expect(table.get_by_text('Invented bond one', exact=True)).to_be_visible()
    expect(page.get_by_test_id('stException')).to_have_count(0)


def smoke(command):
    with TemporaryDirectory(prefix='portfolio smoke ü ') as temporary:
        root = Path(temporary)
        state = root / 'state'
        workspace = root / 'synthetic workspace é'
        env = dict(os.environ, PORTFOLIO_STATE_DIR=str(state), PYTHONPATH='',
                   XDG_DATA_HOME=str(root / 'user-data'), XDG_CACHE_HOME=str(root / 'cache'))
        # No imports from the checkout are available to the packaged child.
        env.pop('VIRTUAL_ENV', None)
        assert subprocess.check_output(command + ['--version'], env=env, cwd=root, text=True).strip()
        assert '--fork-futures' not in subprocess.check_output(command + ['--help'], env=env, cwd=root, text=True)
        log = root / 'synthetic-server.log'
        with log.open('w', encoding='utf-8') as handle:
            child = subprocess.Popen(command + ['--foreground', '--no-browser', '--data-dir', str(workspace)],
                                     cwd=root, env=env, stdout=handle, stderr=handle)
            try:
                url = wait_for_instance(state, child)
                repeated = subprocess.run(command + ['--foreground', '--no-browser', '--data-dir', str(workspace)],
                                          env=env, cwd=root, capture_output=True, text=True, timeout=30)
                assert repeated.returncode == 0 and 'Already running' in repeated.stdout
                with sync_playwright() as runner:
                    browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                    page.set_default_timeout(20000)
                    page.goto(url)
                    expect(page.get_by_role('heading', name='Welcome to Portfolio Breakdown', exact=True)).to_be_visible()
                    page.get_by_role('button', name='Explore demo', exact=True).click()
                    page.locator('.js-plotly-plot').first.wait_for()
                    expect(page.get_by_test_id('stMetric').filter(has=page.get_by_text('Current value', exact=True))).to_contain_text('100,000.00')
                    favicon = page.locator('link[rel="shortcut icon"]')
                    expect(favicon).to_have_attribute('href', re.compile(r'^data:image/svg\+xml;base64,'))
                    assert page.evaluate('''async href => {
                        const icon = new Image();
                        icon.src = href;
                        await icon.decode();
                        return icon.naturalWidth === 1024 && icon.naturalHeight === 1024;
                    }''', favicon.get_attribute('href'))
                    expect(page.get_by_role('tab', name='Overview', exact=True)).to_have_attribute('aria-selected', 'true')
                    expect(page.get_by_role('tab', name='Futures', exact=True)).to_have_count(0)
                    for tab in ['Exposure', 'Positions', 'Rebalance', 'Overview']:
                        page.get_by_role('tab', name=tab, exact=True).click()
                        expect(page.get_by_role('tab', name=tab, exact=True)).to_have_attribute('aria-selected', 'true')
                        # Wait for Streamlit's rerun, including navigation-triggered imports.
                        page.wait_for_timeout(800)
                        expect(page.get_by_test_id('stException')).to_have_count(0)
                    page.get_by_role('tab', name='Exposure', exact=True).click()
                    assets = page.get_by_role('table', name='Exposure assets', exact=True)
                    expect(assets.get_by_text('Nvidia', exact=True)).to_be_visible()
                    breakdown = page.get_by_test_id('stTabs').get_by_text('Break down ETFs', exact=True)
                    breakdown.click()
                    expect(assets.get_by_text('Nvidia', exact=True)).to_have_count(0)
                    breakdown.click()
                    expect(assets.get_by_text('Nvidia', exact=True)).to_be_visible()
                    page.get_by_role('tab', name='Rebalance', exact=True).click()
                    page.get_by_role('button', name='Calculate plan', exact=True).click()
                    expect(page.get_by_role('table', name='Suggested trades', exact=True)).to_be_visible()
                    page.get_by_role('tab', name='Targets', exact=True).click()
                    page.wait_for_timeout(800)
                    expect(page.get_by_test_id('stException')).to_have_count(0)
                    page.get_by_role('tab', name='Positions', exact=True).click()
                    table = page.get_by_role('table', name='Positions', exact=True)
                    table.get_by_role('button', name=re.compile('^Edit ')).first.click()
                    page.get_by_role('textbox', name='Instrument name', exact=True).fill('Synthetic packaged position')
                    page.get_by_role('button', name='Save position', exact=True).click()
                    expect(page.get_by_role('dialog')).to_have_count(0)
                    expect(table.get_by_text('Synthetic packaged position', exact=True).first).to_be_visible()
                    # Exercise the empty persistent workspace and a save with no
                    # provider ticker, so this check needs no market network.
                    page.get_by_text('My portfolio', exact=True).click()
                    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
                    expect(page.get_by_role('button', name='Explore demo', exact=True)).to_be_visible()
                    page.get_by_role('button', name='Start manually', exact=True).click()
                    expect(page.get_by_role('textbox', name='Instrument name', exact=True)).to_have_value('')
                    page.get_by_role('button', name='Cancel', exact=True).click()
                    assert not (workspace / 'holdings.csv').exists()
                    page.get_by_role('button', name='Getting started', exact=True).click()
                    page.get_by_role('button', name='Import holdings', exact=True).click()
                    page.locator('input[type=file]').set_input_files([
                        {'name': 'invented.csv', 'mimeType': 'text/csv',
                         'buffer': b'Name;Quantity\nSynthetic imported CSV;1,25\n'},
                        {'name': 'invented.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         'buffer': synthetic_workbook()},
                    ])
                    units = page.get_by_text('Quantities are shares/units and prices are amounts per unit (not nominal values or percent quotes)', exact=True)
                    expect(units).to_have_count(2)
                    units.nth(0).click()
                    units.nth(1).click()
                    page.get_by_role('button', name='Import reviewed positions', exact=True).click()
                    expect(page.get_by_text('Portfolio imported.', exact=False)).to_be_visible()
                    page.get_by_role('tab', name='Positions', exact=True).click()
                    for name in ['Synthetic imported CSV', 'Synthetic imported Excel']:
                        expect(page.get_by_role('table', name='Positions', exact=True).get_by_text(name, exact=True)).to_be_visible()
                    page.get_by_role('button', name=re.compile(r'Add position$')).click()
                    page.get_by_role('textbox', name='Instrument name', exact=True).fill('Synthetic persistent position')
                    page.get_by_role('spinbutton', name='Quantity held (total)', exact=True).fill('1')
                    page.get_by_role('button', name='Save position', exact=True).click()
                    expect(page.get_by_role('dialog')).to_have_count(0)
                    expect(page.get_by_role('table', name='Positions', exact=True)
                           .get_by_text('Synthetic persistent position', exact=True)).to_be_visible()
                    for name in ['Synthetic imported CSV', 'Synthetic imported Excel']:
                        expect(page.get_by_role('table', name='Positions', exact=True).get_by_text(name, exact=True)).to_be_visible()
                    assert 'Synthetic persistent position' in (workspace / 'holdings.csv').read_text(encoding='utf-8')
                    expect(page.get_by_test_id('stException')).to_have_count(0)
                    browser.close()
                stop = subprocess.run(command + ['--data-dir', str(workspace), '--stop'], env=env, cwd=root,
                                      capture_output=True, text=True, timeout=30)
                assert stop.returncode == 0 and 'Stopped' in stop.stdout
                child.wait(timeout=15)
                assert child.returncode == 0
                assert not list((state / 'sessions').glob('*.json'))
                add_synthetic_fund(workspace)
                saved = (workspace / 'holdings.csv').read_bytes()
                child = subprocess.Popen(command + ['--foreground', '--no-browser', '--data-dir', str(workspace)],
                                         cwd=root, env=env, stdout=handle, stderr=handle)
                url = wait_for_instance(state, child)
                with sync_playwright() as runner:
                    browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
                    page = browser.new_page()
                    page.goto(url)
                    page.get_by_role('tab', name='Positions', exact=True).click()
                    expect(page.get_by_role('table', name='Positions', exact=True)
                           .get_by_text('Synthetic persistent position', exact=True)).to_be_visible()
                    expect(page.get_by_test_id('stException')).to_have_count(0)
                    exercise_manual_breakdown(page)
                    browser.close()
                subprocess.run(command + ['--data-dir', str(workspace), '--stop'], env=env, cwd=root,
                               check=True, capture_output=True, timeout=30)
                child.wait(timeout=15)
                assert (workspace / 'holdings.csv').read_bytes() == saved
                desktop = subprocess.run(command + ['--desktop', '--demo', '--no-browser', '--data-dir', str(workspace)],
                                         env=env, cwd=root, capture_output=True, text=True, timeout=90)
                assert desktop.returncode == 0, desktop.stderr
                assert 'http://127.0.0.1:' in desktop.stdout
                with sync_playwright() as runner:
                    browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
                    page = browser.new_page()
                    page.goto(re.search(r'http://127\.0\.0\.1:\d+', desktop.stdout)[0])
                    expect(page.get_by_test_id('stMetric').filter(has=page.get_by_text('Current value', exact=True))).to_contain_text('100,000.00')
                    page.get_by_role('tab', name='Positions', exact=True).click()
                    page.get_by_role('table', name='Positions', exact=True).wait_for()
                    expect(page.get_by_role('table', name='Positions', exact=True)
                           .get_by_text('Synthetic packaged position', exact=True)).to_have_count(0)
                    expect(page.get_by_test_id('stException')).to_have_count(0)
                    browser.close()
                subprocess.run(command + ['--data-dir', str(workspace), '--stop'], env=env, cwd=root,
                               check=True, capture_output=True, timeout=30)
                assert (workspace / 'holdings.csv').read_bytes() == saved
                assert not list((state / 'sessions').glob('*.json'))
                backup, restored = root / 'backup', root / 'restored'
                subprocess.run(command + ['--data-dir', str(workspace), '--backup-to', str(backup)],
                               env=env, cwd=root, check=True, capture_output=True, timeout=30)
                subprocess.run(command + ['--data-dir', str(restored), '--restore-from', str(backup)],
                               env=env, cwd=root, check=True, capture_output=True, timeout=30)
                assert (restored / 'holdings.csv').read_bytes() == saved
                assert {p.name: p.read_bytes() for p in (workspace / 'etfs').iterdir()} == {
                    p.name: p.read_bytes() for p in (restored / 'etfs').iterdir()}
            except BaseException as exc:
                handle.flush()
                print(log.read_text(encoding='utf-8'), file=sys.stderr)
                if isinstance(exc, subprocess.CalledProcessError):
                    print(exc.stderr, file=sys.stderr)
                raise
            finally:
                if child.poll() is None:
                    subprocess.run(command + ['--data-dir', str(workspace), '--stop'], env=env, cwd=root,
                                   capture_output=True, timeout=30)
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
    print('Installed application browser, navigation and lifecycle checks passed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', nargs='?', type=Path)
    args = parser.parse_args()
    smoke([str(args.executable.resolve())] if args.executable else [sys.executable, '-m', 'portfolio_app.app'])
