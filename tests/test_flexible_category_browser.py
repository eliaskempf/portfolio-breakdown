"""Real-browser category navigation and planning with synthetic offline values."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from browser_runtime import browser_session, start_server
from browser_support import open_app, select_tab, settle
from test_flexible_category_ui import workspace

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def category_page(workspace, request):
    if getattr(request, 'param', '') == 'mixed-exposure':
        import pandas as pd
        from test_mixed_exposure_targets import mixed
        from portfolio_app.exposure.exposure_targets import save_exposure_targets
        from portfolio_app.funds.leverage_settings import LeverageSettings, save_leverage_settings
        rows = pd.read_csv(workspace/'holdings.csv').assign(instrument_type='equity',exposure_kind='equity')
        rows.to_csv(workspace/'holdings.csv',index=False)
        config, _, targets, _ = mixed()
        save_exposure_targets(workspace,targets,config,None)
        save_leverage_settings(workspace,LeverageSettings(enabled=True,active_basis='exposure'),None)
    if getattr(request, 'param', '') == 'legacy':
        import pandas as pd
        from portfolio_app.portfolio.allocation import Allocation, Bucket, allocation_content
        rows = pd.read_csv(workspace/'holdings.csv')
        rows['bucket_id'] = 'sleeve'
        rows['within_bucket_target'] = [.2,.3,.5]
        rows.to_csv(workspace/'holdings.csv', index=False)
        (workspace/'allocation.yaml').write_text(allocation_content(Allocation((Bucket('sleeve','Example sleeve',target=1.),))))
    app = workspace/'app.py'
    app.write_text('from pathlib import Path\nfrom portfolio_app.interface.ui import render_app\nrender_app(Path(__file__).parent, demo=True)\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    process=start_server([sys.executable,'-m','streamlit','run',str(app),'--server.address=127.0.0.1',f'--server.port={port}',
        '--server.headless=true','--server.fileWatcherType=none','--browser.gatherUsageStats=false'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urlopen(f'http://127.0.0.1:{port}/_stcore/health',timeout=1)
                break
            except OSError:
                time.sleep(.1)
        with browser_session() as runner:
            browser=runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.set_default_timeout(15000)
            open_app(page,f'http://127.0.0.1:{port}')
            yield page,workspace
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def choose(page, label, option):
    page.get_by_role('combobox',name=label,exact=True).click()
    target = page.get_by_role('option',name=option,exact=True)
    if target.get_attribute('aria-selected') == 'true':
        target.click()
        settle(page)
    else:
        page.streamlit_reruns.click(page,target)



def flatten_checked(page):
    page.get_by_role('button',name='Table options',exact=True).click()
    checked = page.get_by_role('checkbox',name='Flatten groups',exact=True).is_checked()
    page.keyboard.press('Escape')
    return checked


def flatten_groups(page, enabled=True):
    page.get_by_role('button',name='Table options',exact=True).click()
    checkbox = page.get_by_role('checkbox',name='Flatten groups',exact=True)
    if checkbox.is_checked() != enabled:
        page.streamlit_reruns.click(page,page.get_by_text('Flatten groups',exact=True))
    page.keyboard.press('Escape')
    settle(page)


def test_flat_category_navigation_and_planning(category_page):
    page,directory=category_page
    choose(page,'Category','Example sleeve')
    table=page.get_by_role('table',name='Allocation',exact=True)
    playwright.expect(table).to_contain_text('Example chips')
    chart_before = page.locator('.js-plotly-plot').first.evaluate('el => JSON.stringify(el.data[0])')
    flatten_groups(page)
    assert page.locator('.js-plotly-plot').first.evaluate('el => JSON.stringify(el.data[0])') == chart_before
    playwright.expect(table).to_contain_text('Invented investment 0')
    playwright.expect(table).to_contain_text('Invented investment 1')
    playwright.expect(table).to_contain_text('Invented investment 2')
    assert table.locator('tbody tr').count()==3
    # Flattening is a secondary table option; the chart retains its hierarchy.
    labels=page.locator('.js-plotly-plot').first.evaluate('el => Array.from(el.data[0].labels)')
    assert 'Example chips' in labels
    assert page.get_by_role('radiogroup',name='Allocation view',exact=True).count() == 0
    assert all(f'Invented investment {i}' in labels for i in range(3))
    choose(page,'Category','Example sleeve › Example chips')
    assert table.locator('tbody tr').count()==2
    page.streamlit_reruns.click(page,page.get_by_role('button',name='Up one level',exact=True))
    assert table.locator('tbody tr').count()==3
    select_tab(page,'Rebalance')
    select_tab(page,'Targets')
    choose(page,'Allocation category','Example sleeve')
    playwright.expect(page.get_by_text('100% assigned',exact=True)).to_be_visible()
    choose(page,'Allocation category','Example sleeve › Example chips')
    playwright.expect(page.get_by_role('radio',name='Flexible',exact=True)).to_be_checked()
    select_tab(page,'Plan')
    page.streamlit_reruns.click(page,page.get_by_role('button',name='Calculate plan',exact=True))
    playwright.expect(page.get_by_role('heading',name='Suggested trades',exact=True)).to_be_visible()
    page.streamlit_reruns.click(page,page.get_by_role('radio',name='Within a category',exact=True))
    choose(page,'Planning category','Example sleeve')
    for mode in ('Fewest trades (buys and sells)','Minimum new money (no sells)','Allocate new money'):
        choose(page,'Rebalancing mode',mode)
        page.streamlit_reruns.click(page,page.get_by_role('button',name='Calculate plan',exact=True))
        playwright.expect(page.get_by_role('heading',name='Suggested trades',exact=True)).to_be_visible()
        playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    select_tab(page,'Overview')
    assert flatten_checked(page)
    page.set_viewport_size({'width':720,'height':1000})
    settle(page)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    open_app(page,page.url)
    assert flatten_checked(page)


def test_expandable_allocation_keeps_chart_scope_and_opens_holdings(category_page):
    page, _ = category_page
    table = page.get_by_role('table', name='Allocation', exact=True)
    assert table.locator('tbody tr').count() == 1
    chart_before = page.locator('.js-plotly-plot').first.evaluate('el => Array.from(el.data[0].ids)')
    table.get_by_role('button', name='Expand Example sleeve', exact=True).click()
    assert table.locator('tbody tr').count() == 3
    group = table.get_by_role('button', name='Expand Example chips', exact=True)
    group.focus()
    group.press('Enter')
    assert table.locator('tbody tr').count() == 5
    child = table.locator('tr[data-position-id="position:p1"]')
    assert child.locator('td').nth(2).inner_text() == '20.00'
    assert child.locator('td').nth(3).inner_text() == '—'
    assert child.locator('.hierarchy-label').evaluate('el => el.style.paddingLeft') == '40px'
    assert page.locator('.js-plotly-plot').first.evaluate('el => Array.from(el.data[0].ids)') == chart_before
    # Sorting only siblings keeps descendants adjacent to their group.
    table.get_by_role('button', name='Category', exact=False).first.click()
    ids = table.locator('tbody tr').evaluate_all('rows => rows.map(row => row.dataset.positionId)')
    assert ids.index('position:p1') == ids.index('category:group') + 1
    assert ids.index('position:p2') == ids.index('category:group') + 2
    table.get_by_role('button', name='Collapse Example chips', exact=True).click()
    assert table.locator('tbody tr').count() == 3
    page.set_viewport_size({'width':720,'height':1000})
    table.get_by_role('button', name='Expand Example chips', exact=True).click()
    playwright.expect(table.get_by_role('button', name='Collapse Example chips')).to_have_attribute('aria-expanded', 'true')
    page.streamlit_reruns.click(page, child)
    playwright.expect(page.get_by_role('dialog')).to_contain_text('Invented investment 0')
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


@pytest.mark.parametrize('category_page', ['mixed-exposure'], indirect=True)
def test_mixed_market_exposure_overview_targets_and_planning(category_page):
    page, _ = category_page
    choose(page,'Category','Example sleeve')
    table = page.get_by_role('table',name='Market exposure',exact=True)
    assert table.locator('tbody tr').count() == 2
    table.get_by_role('button',name='Expand Example chips',exact=True).click()
    assert table.locator('tbody tr').count() == 4
    direct = table.locator('tr[data-position-id="position:p3"]')
    assert direct.locator('td').nth(3).inner_text() == '50.00'
    assert table.locator('tr[data-position-id="position:p1"] td').nth(3).inner_text() == '—'
    flatten_groups(page)
    assert table.locator('tbody tr').count() == 3
    # Flat exposure keeps the explicit sibling target, without distributing the group target.
    assert table.locator('tr[data-position-id="position:p3"] td').nth(4).inner_text() == '50.00'
    page.streamlit_reruns.click(page,page.get_by_role('radio',name='Capital allocation',exact=True))
    playwright.expect(page.get_by_role('table',name='Allocation',exact=True)).to_be_visible()
    select_tab(page,'Rebalance')
    select_tab(page,'Targets')
    target_view = page.get_by_role('radio',name='Exposure targets',exact=True)
    if not target_view.is_checked():
        page.streamlit_reruns.click(page,target_view)
    playwright.expect(page.get_by_role('combobox',name='Composition weights',exact=True)).to_have_value('Categories and investments within each parent')
    page.streamlit_reruns.click(page,page.get_by_role('button',name='Save exposure targets',exact=True))
    select_tab(page,'Plan')
    for name in ('Invented investment 0','Invented investment 1','Invented investment 2'):
        page.get_by_role('combobox',name='Permitted instruments',exact=True).click()
        page.streamlit_reruns.click(page,page.get_by_role('option',name=name,exact=True))
    page.keyboard.press('Escape')
    page.streamlit_reruns.click(page,page.get_by_text('Allow funded-asset sales',exact=True))
    page.streamlit_reruns.click(page,page.get_by_role('button',name='Calculate exposure plan',exact=True))
    playwright.expect(page.get_by_role('heading',name='Suggested exposure trades',exact=True)).to_be_visible()
    playwright.expect(page.get_by_text('Targets reached within EUR 0.01.',exact=True)).to_be_visible()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


@pytest.mark.parametrize('category_page', ['legacy'], indirect=True)
def test_create_category_and_return_to_flat_view(category_page):
    page,directory = category_page
    select_tab(page,'Rebalance')
    select_tab(page,'Targets')
    page.get_by_role('button',name='Create category from selection',exact=True).click()
    panel = page.get_by_test_id('stPopoverBody')
    for label in ('Invented investment 0 · Account 0', 'Invented investment 1 · Account 1'):
        panel.get_by_role('combobox',name='Investments to combine',exact=True).click()
        page.streamlit_reruns.click(page,page.get_by_role('option',name=label,exact=True))
    page.keyboard.press('Escape')
    page.streamlit_reruns.commit_text(page,panel.get_by_role('textbox',name='New category name',exact=True),'Invented combined')
    playwright.expect(panel.get_by_role('spinbutton',name='Combined target (% of parent)',exact=True)).to_have_value('50.00')
    page.streamlit_reruns.click(page,panel.get_by_role('button',name='Create category',exact=True))
    page.keyboard.press('Escape')
    from portfolio_app.portfolio.allocation import load_allocation
    config = load_allocation(directory/'allocation.yaml')
    assert any(b.name == 'Invented combined' and b.allocation_mode == 'flexible' for b in config.buckets)
    select_tab(page,'Overview')
    choose(page,'Category','Example sleeve')
    table = page.get_by_role('table',name='Allocation',exact=True)
    playwright.expect(table).to_contain_text('Invented combined')
    assert table.locator('tbody tr').count() == 2
    flatten_groups(page)
    assert table.locator('tbody tr').count() == 3
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


@pytest.mark.parametrize('category_page',['mixed-exposure'],indirect=True)
def test_context_target_dialog_navigation_and_save(category_page):
    from portfolio_app.exposure.exposure_targets import load_exposure_targets
    from portfolio_app.portfolio.allocation import load_allocation
    page, workspace = category_page
    choose(page,'Category','Example sleeve')
    before = (workspace/'exposure-targets.json').read_bytes()
    page.get_by_role('button',name='Edit targets',exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.wait_for()
    playwright.expect(dialog.get_by_text('Example sleeve', exact=True)).to_have_count(1)
    playwright.expect(dialog.get_by_text('100% assigned', exact=True)).to_have_count(1)
    assert dialog.get_by_role('combobox').count() == 0
    dialog.get_by_role('button',name='Cancel',exact=True).click()
    dialog.wait_for(state='hidden')
    settle(page)
    assert (workspace/'exposure-targets.json').read_bytes() == before
    choose(page,'Category','Portfolio')
    page.get_by_role('button',name='Edit targets',exact=True).click()
    dialog.wait_for()
    multiple = dialog.get_by_role('spinbutton',name='Total exposure (× net equity)',exact=True)
    multiple.fill('1.25')
    multiple.press('Enter')
    settle(page)
    dialog.get_by_role('button',name='Save',exact=True).click()
    dialog.wait_for(state='hidden')
    settle(page)
    assert load_exposure_targets(workspace,load_allocation(workspace/'allocation.yaml'))[0].multiple == 1.25
    page.get_by_role('button',name='Edit targets',exact=True).click()
    dialog.wait_for()
    dialog.get_by_role('button',name='Manage targets',exact=True).click()
    dialog.wait_for(state='hidden')
    page.get_by_role('combobox',name='Composition weights',exact=True).wait_for()
    assert not page.get_by_test_id('stException').count()
