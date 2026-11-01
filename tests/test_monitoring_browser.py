"""Actual Streamlit navigation and alerts, using only invented local workspaces."""
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from test_monitoring_service import account_workspace
from portfolio_app.monitoring_store import load_monitoring
from browser_support import settle

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def monitoring_page(tmp_path):
    root=tmp_path/'synthetic'
    root.mkdir()
    account_workspace(root)
    # Keep these manual synthetic observations current independently of when CI runs.
    from datetime import date
    path=root/'holdings.csv'
    path.write_text(path.read_text().replace('2026-10-12',date.today().isoformat()))
    from portfolio_app.account_journal import load_journal, save_journal
    from dataclasses import replace
    journal, revision=load_journal(root)
    save_journal(root,replace(journal,accounts=journal.accounts[:1],events=journal.events[:1]),revision)
    app=tmp_path/'app.py'
    app.write_text('''
from pathlib import Path
from portfolio_app.ui import render_app
from portfolio_app.prices import PriceService, StaticProvider
root=Path(__file__).parent/'synthetic'
render_app(root,demo=True,ignore_selection=True,price_service=PriceService(StaticProvider(root/'demo_prices.json')))
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    with (tmp_path/'server.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-m','streamlit','run',str(app),f'--server.port={port}',
            '--server.address=127.0.0.1','--server.headless=true','--server.fileWatcherType=none',
            '--browser.gatherUsageStats=false'],stdout=log,stderr=log)
        try:
            url=f'http://127.0.0.1:{port}'
            for _ in range(100):
                assert process.poll() is None
                try:
                    with urlopen(url+'/_stcore/health',timeout=1):break
                except OSError:time.sleep(.1)
            with playwright.sync_playwright() as runner:
                browser=runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1100})
                page.set_default_timeout(20000)
                yield page,url,root
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_trigger_dialog_acknowledgment_and_account_rule_navigation(monitoring_page):
    page,url,root=monitoring_page
    page.goto(url)
    dialog=page.get_by_role('dialog',name='Monitoring alert')
    playwright.expect(dialog).to_be_visible(timeout=20000)
    playwright.expect(dialog).to_contain_text('60.00%')
    dialog.get_by_role('button',name='Acknowledge alert',exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    assert load_monitoring(root)[0]['events'][0]['acknowledged']
    playwright.expect(page.get_by_role('button',name='Check all rules now',exact=True)).to_have_count(0)
    playwright.expect(page.get_by_role('button',name='View rule',exact=True)).to_have_count(0)
    page.get_by_role('tab',name='Monitoring',exact=True).click()
    page.get_by_role('radio',name='Tracked rules',exact=True).click()
    playwright.expect(page.get_by_role('tab',name='Monitoring',exact=True)).to_have_attribute('aria-selected','true')
    playwright.expect(page.get_by_role('combobox',name='Rule details',exact=True)).to_be_visible()
    # The previous rule controls can remain visible while Streamlit replaces
    # the tracked-rules view. Click Edit only after that render has settled.
    settle(page)
    page.get_by_role('button',name='Edit rule',exact=True).click()
    page.get_by_role('textbox',name='Maximum LTV (%)',exact=True).fill('70')
    page.get_by_role('button',name='Save account rule',exact=True).click()
    playwright.expect(page.get_by_role('button',name='Save account rule',exact=True)).to_have_count(0)
    assert load_monitoring(root)[0]['rules'][0]['threshold']==70
    page.get_by_role('tab',name='Futures',exact=True).click()
    page.get_by_role('button',name='Create account alert',exact=True).click()
    playwright.expect(page.get_by_role('combobox',name='Account metric',exact=True)).to_be_visible()
    page.get_by_role('combobox',name='Account metric',exact=True).click()
    page.get_by_role('option',name='Maintenance headroom',exact=True).click()
    page.get_by_role('textbox',name='Trigger at headroom (%)',exact=True).fill('0')
    page.get_by_role('textbox',name='Warn at headroom (%)',exact=True).fill('10')
    page.get_by_role('button',name='Save account rule',exact=True).click()
    playwright.expect(page.get_by_role('combobox',name='Rule details',exact=True)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    assert len(load_monitoring(root)[0]['rules'])==2
    page.reload()
    playwright.expect(page.get_by_role('tab',name='Monitoring',exact=True)).to_be_visible()
    playwright.expect(page.get_by_role('dialog',name='Monitoring alert')).to_have_count(0)


def test_indicator_browse_save_and_notification_destination(monitoring_page):
    page,url,root=monitoring_page
    page.goto(url)
    page.get_by_role('dialog',name='Monitoring alert').get_by_role('button',name='View rule',exact=True).click()
    playwright.expect(page.get_by_role('dialog',name='Monitoring alert')).to_have_count(0)
    page.get_by_role('radio',name='Browse',exact=True).click()
    page.get_by_role('radio',name='Provider ticker',exact=True).click()
    page.get_by_role('textbox',name='Provider ticker',exact=True).fill('INVENTED')
    page.get_by_role('textbox',name='Provider ticker',exact=True).press('Enter')
    playwright.expect(page.locator('.js-plotly-plot').first).to_be_visible(timeout=20000)
    page.get_by_role('tab',name='Positions',exact=True).click()
    playwright.expect(page.get_by_role('button',name='Track rule',exact=True)).to_have_count(0)
    page.wait_for_function("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'")
    page.get_by_role('tab',name='Monitoring',exact=True).click()
    playwright.expect(page.get_by_role('textbox',name='Provider ticker',exact=True)).to_have_value('INVENTED')
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    playwright.expect(page.get_by_role('button',name='Track rule',exact=True)).to_be_disabled()
    threshold=page.get_by_role('spinbutton',name='Distance from SMA trigger (%)',exact=True)
    threshold.fill('-2')
    threshold.press('Enter')
    page.get_by_role('button',name='Track rule',exact=True).click()
    playwright.expect(page.get_by_role('combobox',name='Rule details',exact=True)).to_be_visible()
    assert any(r['kind']=='sma' for r in load_monitoring(root)[0]['rules'])
    bell = page.get_by_role('button',name=re.compile('Notifications'))
    icon = bell.get_by_test_id('stIconMaterial').filter(has_text='notifications')
    playwright.expect(icon).to_be_visible()
    bell.click()
    playwright.expect(icon).to_be_visible()
    page.get_by_role('button',name='Review manual prices',exact=True).click()
    playwright.expect(page.get_by_role('tab',name='Positions',exact=True)).to_have_attribute('aria-selected','true')
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


def test_alerts_arrive_on_every_tab_without_global_status_or_banners(monitoring_page):
    from portfolio_app.monitoring import Evaluation
    from portfolio_app.monitoring_store import record_evaluations, rule_from_dict
    page,url,root=monitoring_page
    page.goto(url)
    dialog=page.get_by_role('dialog',name='Monitoring alert')
    dialog.get_by_role('button',name='Acknowledge alert',exact=True).click()
    playwright.expect(dialog).to_have_count(0)
    # Let acknowledgment finish before injecting the next external alert;
    # otherwise its rerun can legitimately open that alert over the next click.
    settle(page)
    rule=rule_from_dict(load_monitoring(root)[0]['rules'][0])
    for tab in ['Monitoring','Exposure','Positions','Rebalance','Futures','Overview']:
        record_evaluations(root,rule,[Evaluation('2026-10-13',10.,condition='normal'),Evaluation('2026-10-13',70.,condition='triggered')])
        page.get_by_role('tab',name=tab,exact=True).click()
        playwright.expect(dialog).to_be_visible()
        playwright.expect(dialog).to_contain_text('Your rule')
        playwright.expect(dialog).to_contain_text('Condition:')
        playwright.expect(dialog).to_contain_text('Observed:')
        playwright.expect(dialog.get_by_role('button',name='View rule',exact=True)).to_be_visible()
        playwright.expect(dialog.get_by_role('button',name='Acknowledge alert',exact=True)).to_be_visible()
        settle(page)
        # An alert fragment can replace the dialog after settle(). Reacquire
        # both controls and measure one DOM state, retaining the alignment check.
        page.wait_for_function("""() => {
            const dialog = document.querySelector('[role="dialog"]');
            const buttons = [...(dialog?.querySelectorAll('button') ?? [])];
            const controls = ['View rule', 'Acknowledge alert'].map(label =>
                buttons.find(button => button.textContent.trim() === label));
            if (controls.some(button => !button || getComputedStyle(button).visibility !== 'visible'))
                return false;
            const [a, b] = controls.map(button => button.getBoundingClientRect());
            return a.width > 0 && a.height > 0 && b.width > 0 && b.height > 0
                && Math.abs(a.top - b.top) < 3;
        }""")
        dialog.get_by_role('button',name='Acknowledge alert',exact=True).click()
        playwright.expect(dialog).to_have_count(0)
        settle(page)
        if tab!='Monitoring':
            playwright.expect(page.get_by_role('button',name='Check all rules now',exact=True)).to_have_count(0)
            playwright.expect(page.get_by_role('button',name='View rule',exact=True)).to_have_count(0)
        playwright.expect(page.get_by_text('Monitoring while this app is open',exact=False)).to_have_count(0)
        playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
