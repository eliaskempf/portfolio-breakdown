"""Optional browser coverage for position list interactions, using invented data."""

import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from portfolio_app.holdings import load_holdings

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def position_page(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('position_key,id,name,shares,account\n'
                    'first,a,Invented <b>Token</b>,1,First\n'
                    'second,a,Invented <b>Token</b>,2,Second\n'
                    'third,b,Other invented token,3,Third\n')
    app = tmp_path / 'app.py'
    app.write_text('''
from pathlib import Path
import streamlit as st
from portfolio_app.positions import read_snapshot
from portfolio_app.position_ui import render_position_editor
st.set_page_config(layout='wide')
path = Path(__file__).parent / 'holdings.csv'
render_position_editor(path, read_snapshot(path), [], demo=True, embedded=True)
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
            pytest.fail('Synthetic server did not start')
        with playwright.sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1400, 'height': 1000})
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('table', name='Positions', exact=True).wait_for()
            yield page, path
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_pencil_after_sort_and_filter_opens_exact_account_and_renames(position_page):
    page, path = position_page
    table = page.get_by_role('table', name='Positions', exact=True)
    # Names are plain text, never markup, and identity survives client sorting.
    playwright.expect(table.locator('b')).to_have_count(0)
    table.get_by_role('button', name='Quantity', exact=True).click()
    table.get_by_role('button', name='Quantity ↑', exact=True).click()
    page.get_by_role('searchbox', name='Filter positions').fill('Second')
    rows = table.locator('tbody tr')
    playwright.expect(rows).to_have_count(1)
    rows.get_by_role('button', name='Edit Invented <b>Token</b> · Second', exact=True).click()
    account = page.get_by_role('textbox', name='Account / broker', exact=True)
    playwright.expect(account).to_have_value('Second')
    assert float(page.get_by_role('spinbutton', name='Quantity held (total)', exact=True).input_value()) == 2.
    name = page.get_by_role('textbox', name='Instrument name', exact=True)
    playwright.expect(name).to_be_enabled()
    name.fill('Custom browser name')
    page.get_by_role('button', name='Save position', exact=True).click()
    playwright.expect(page.get_by_role('dialog')).to_have_count(0)
    table.wait_for()
    stored = load_holdings(path)
    assert stored.name.tolist() == ['Custom browser name', 'Custom browser name', 'Other invented token']
    assert stored.shares.tolist() == [1., 2., 3.]
    assert stored.position_id.tolist() == ['first', 'second', 'third']


def test_keyboard_and_edit_button_open_rows_and_back_returns_to_list(position_page):
    page, _ = position_page
    table = page.get_by_role('table', name='Positions', exact=True)
    table.locator('tbody tr').first.focus()
    page.keyboard.press('ArrowDown')
    page.keyboard.press('Enter')
    page.get_by_role('dialog').get_by_role('button', name='Edit position').click()
    playwright.expect(page.get_by_role('textbox', name='Account / broker', exact=True)).to_have_value('Second')
    page.get_by_role('button', name='Cancel', exact=True).click()
    table.wait_for()
    table.get_by_role('button', name='Edit Other invented token · Third', exact=True).click()
    playwright.expect(page.get_by_role('textbox', name='Account / broker', exact=True)).to_have_value('Third')
