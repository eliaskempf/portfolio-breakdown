"""Native calendar layout and keyboard behavior with synthetic dates only."""
import os
import json
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from portfolio_app.display_preferences import load_preferences

playwright = pytest.importorskip('playwright.sync_api')
from browser_support import settle


@pytest.fixture
def calendar_page(tmp_path, request):
    script=tmp_path/'calendar_app.py'
    script.write_text('''from pathlib import Path
from datetime import date
import streamlit as st
from portfolio_app.calendar_ui import configure_calendar
configure_calendar(Path(__file__).parent, st.container())
chosen=st.date_input('Invented date', value=date(2026,1,7), min_value=date(2026,1,1), max_value=date(2026,1,31))
st.text(chosen.isoformat())
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    child=subprocess.Popen([sys.executable,'-m','streamlit','run',str(script),
        '--server.address=127.0.0.1',f'--server.port={port}','--server.headless=true',
        '--server.fileWatcherType=none','--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health',timeout=1);break
            except OSError:time.sleep(.1)
        with playwright.sync_playwright() as runner:
            browser=runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),args=['--no-sandbox'])
            page=browser.new_page(locale='en-US')
            page.add_init_script("Object.defineProperty(navigator,'language',{configurable:true,value:"+json.dumps(request.param)+"});")
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_test_id('stDateInput').wait_for();settle(page)
            yield page,tmp_path
            browser.close()
    finally:
        child.terminate();child.wait(timeout=10)


@pytest.mark.parametrize('calendar_page',['en-US-u-fw-sun','en-US@posix'],indirect=True)
def test_week_preference_changes_grid_and_preserves_date_selection(calendar_page):
    page,root=calendar_page
    expect=playwright.expect
    field=page.get_by_test_id('stDateInput')
    field.get_by_role('spinbutton').first.click()
    grid=page.get_by_role('grid');expect(grid).to_be_visible()
    assert grid.locator('thead th').all_text_contents()==['M','T','W','T','F','S','S']
    assert page.evaluate('navigator.language')=='en-US-u-fw-mon'
    field.get_by_role('spinbutton').first.press('Alt+ArrowDown')
    page.keyboard.press('ArrowLeft')
    expect(grid.get_by_role('button',name='Tuesday, January 6, 2026',exact=True)).to_be_focused()
    page.keyboard.press('Enter');settle(page)
    expect(page.get_by_text('2026-01-06',exact=True)).to_be_visible()
    page.get_by_role('combobox',name='Week starts on',exact=True).click()
    page.get_by_role('option',name='Sunday',exact=True).click();settle(page)
    assert load_preferences(root).week_start=='Sunday'
    expect(page.get_by_text('2026-01-06',exact=True)).to_be_visible()
    field.get_by_role('spinbutton').first.click()
    expect(grid).to_be_visible()
    assert grid.locator('thead th').all_text_contents()==['S','M','T','W','T','F','S']
    assert grid.locator('tbody tr').nth(1).get_by_role('button').first.get_attribute('aria-label')=='Sunday, January 4, 2026'
    page.reload();page.get_by_test_id('stDateInput').wait_for();settle(page)
    expect(page.get_by_role('combobox',name='Week starts on',exact=True)).to_have_value('Sunday')
    field.get_by_role('spinbutton').first.click()
    expect(grid).to_be_visible()
    assert grid.locator('thead th').all_text_contents()[0]=='S'
