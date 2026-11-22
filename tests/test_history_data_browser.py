"""Quote corrections retain disclosed provenance in a synthetic running view."""
from browser_runtime import browser_session, start_server
from contextlib import contextmanager
from dataclasses import replace
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from browser_support import select_tab, settle
from portfolio_app.ledger.account_journal import save_journal
from portfolio_app.performance.history_observations import load_observations, save_observations
from test_history_data_ui import fixture
from test_ux_browser import ux_page  # noqa: F401

playwright = pytest.importorskip('playwright.sync_api')


@contextmanager
def history_page(script):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    process = start_server([sys.executable, '-m', 'streamlit', 'run', str(script),
        '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
        '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                with urlopen(url+'/_stcore/health', timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail('Synthetic history preview did not start')
        with browser_session() as runner:
            browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.goto(url); settle(page)
            yield page
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_quote_provenance_correction_and_saved_details_in_browser(tmp_path):
    journal, _, store, revision = fixture(tmp_path)
    store = replace(store, observations=tuple(replace(o, source='Invented closing feed', listing='SYNTH',
        calendar='XNYS', exchange_timezone='America/New_York') if o.id == 'p1' else o for o in store.observations))
    save_observations(tmp_path, store, revision)
    save_journal(tmp_path, journal, None)
    before = (tmp_path/'holdings.csv').read_bytes()
    script = tmp_path/'preview.py'
    script.write_text('''
from pathlib import Path
from types import SimpleNamespace
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.performance.history_observations import load_observations, HistoryScope
from portfolio_app.interface.views.history.history_data_ui import render_history_data
root=Path(__file__).parent
journal,_=load_journal(root)
store,revision=load_observations(root)
result=SimpleNamespace(value_reasons=(),flow_reasons=(),capital_reasons=(),money_weighted_reasons=(),time_weighted_reasons=())
view=SimpleNamespace(result=result,all_result=result,missing=(),all_start='2024-01-01',all_end='2024-01-01',today='2024-01-01')
render_history_data(root,journal,HistoryScope(('position-0',)),store,revision,view,'EUR','browser_')
''')
    with history_page(script) as page:
        page.get_by_text('Correct market data', exact=True).click(); settle(page)
        page.get_by_text('Quote provenance', exact=True).click(); settle(page)
        for label, value in [('Source','Invented closing feed'), ('Quote listing','SYNTH'),
                             ('Exchange calendar','XNYS'), ('Exchange timezone','America/New_York')]:
            playwright.expect(page.get_by_role('textbox', name=label, exact=True)).to_have_value(value)
        amount = page.get_by_role('textbox', name='Price (USD)', exact=True)
        amount.fill('10.25'); amount.press('Tab'); settle(page)
        page.get_by_role('button', name='Replace saved value', exact=True).click(); settle(page)
        page.get_by_text('Saved market data', exact=True).click(); settle(page)
        saved = load_observations(tmp_path)[0].observations[-1]
        assert saved.replaces == 'p1' and str(saved.amount) == '10.25'
        assert saved.source == 'manual: Invented closing feed'
        assert saved.listing == 'SYNTH' and saved.calendar == 'XNYS'
        assert saved.exchange_timezone == 'America/New_York'
        assert (tmp_path/'holdings.csv').read_bytes() == before
        table = page.get_by_test_id('stDataFrame')
        playwright.expect(table).to_be_visible()
        assert not page.get_by_test_id('stException').count()
        page.reload(); settle(page)
        page.get_by_text('Correct market data', exact=True).click(); settle(page)
        playwright.expect(page.get_by_role('button', name='Replace saved value', exact=True)).to_be_visible()


@pytest.mark.parametrize('width', [1440, 390])
def test_return_repairs_preserve_category_period_and_position(ux_page, width):
    from datetime import date, timedelta
    from decimal import Decimal
    from portfolio_app.ledger.account_journal import Journal
    from portfolio_app.ledger.security_history import SecurityBook, SecurityEvent
    from portfolio_app.performance.history_observations import Observation, ObservationStore

    page, root = ux_page
    first = (date.today() - timedelta(days=20)).isoformat()
    middle = (date.today() - timedelta(days=5)).isoformat()
    last = (date.today() - timedelta(days=2)).isoformat()
    books = []
    quotes = []
    for identity, instrument, units, cost, currency in (('fund-a', 'a', 2, 80, 'USD'), ('fund-b', 'b', 1, 60, 'EUR')):
        event = SecurityEvent('open-'+identity, 'opening', first, quantity=Decimal(units),
                              amount=Decimal(units * cost), currency='EUR')
        books.append(SecurityBook(identity, instrument, (str(units), str(cost), 'EUR'),
                                  records=(event,), active_ids=(event.id,)))
        quotes.append(Observation('middle-'+identity, 'price', identity, middle, Decimal(18), currency))
        quotes.append(Observation('close-'+identity, 'price', identity, last, Decimal(20), currency))
    quotes += [Observation('open-a', 'price', 'fund-a', first, Decimal(10), 'USD'),
               Observation('middle-fx', 'fx', 'USD/EUR', middle, Decimal('.9'), 'USD', 'EUR'),
               Observation('close-fx', 'fx', 'USD/EUR', last, Decimal('.9'), 'USD', 'EUR')]
    save_journal(root, Journal(version=5, securities=tuple(books)), None)
    save_observations(root, ObservationStore(tuple(quotes)), None)
    page.set_viewport_size(dict(width=width, height=900))
    page.reload()
    select_tab(page, 'Overview')
    category = page.get_by_role('combobox', name='Category', exact=True)
    category.click()
    page.get_by_role('option', name='ETF core', exact=False).click()
    settle(page)
    page.get_by_role('radio', name='Allocation', exact=True).click()
    settle(page)
    page.get_by_role('button', name='Show gain as percentage', exact=True).click()
    gaps = page.get_by_role('button', name='Missing inputs (2)', exact=True)
    playwright.expect(page.locator('.st-key-overview_value').get_by_test_id('stMetricDelta')).to_have_text('Unavailable')
    playwright.expect(gaps).to_be_visible()
    gaps.click()
    popup = page.get_by_test_id('stPopoverBody')
    picker = popup.get_by_role('radiogroup', name='Missing input', exact=True)
    rate_choice = picker.get_by_role('radio', name='USD/EUR · '+first, exact=True)
    playwright.expect(rate_choice).to_be_visible()
    playwright.expect(picker.get_by_role('radio', name='Invented Regional', exact=False)).to_be_visible()
    page.keyboard.press('Escape')
    page.get_by_role('radio', name='Performance', exact=True).click()
    settle(page)
    period = page.get_by_role('radiogroup', name='Period').get_by_role('radio', name='3M', exact=True)
    period.click()
    settle(page)
    annual = page.get_by_test_id('stColumn').filter(has=page.get_by_test_id('stMetric').filter(has_text='Annualized return'))
    annual.get_by_role('button', name='Missing inputs (2)', exact=True).click()
    rate_choice.check()
    settle(page)
    value = popup.get_by_role('textbox', name='1 USD equals (EUR)', exact=True)
    value.fill('0.90')
    value.press('Tab')
    settle(page)
    popup.get_by_role('button', name='Save value', exact=True).click()
    playwright.expect(annual.get_by_role('button', name='Add price', exact=True)).to_be_visible()
    playwright.expect(annual.get_by_test_id('stMetricValue')).to_have_text('Unavailable')
    playwright.expect(period).to_be_checked()
    playwright.expect(category).to_have_value('ETF core')
    select_tab(page, 'Positions')
    page.get_by_role('table', name='Positions', exact=True).get_by_role('row').filter(has_text='Invented Regional').click()
    dialog = page.get_by_role('dialog')
    position_period = dialog.get_by_role('radio', name='6M', exact=True)
    position_period.click()
    settle(page)
    repair = dialog.locator('.st-key-position_summary').get_by_role('button', name='Add price', exact=True)
    playwright.expect(repair).to_be_visible()
    repair.click()
    playwright.expect(popup).to_contain_text('Invented Regional')
    playwright.expect(popup).to_contain_text(first)
    value = popup.get_by_role('textbox', name='Price (EUR)', exact=True)
    value.fill('10')
    value.press('Tab')
    settle(page)
    popup.get_by_role('button', name='Save value', exact=True).click()
    metric = dialog.get_by_test_id('stMetric').filter(has_text='Annualized return')
    playwright.expect(metric.get_by_test_id('stMetricValue')).not_to_have_text('Unavailable')
    playwright.expect(repair).to_have_count(0)
    playwright.expect(position_period).to_be_checked()
    dialog.get_by_role('button', name='Close', exact=True).first.click()
    playwright.expect(dialog).to_have_count(0)
    select_tab(page, 'Overview')
    playwright.expect(annual.get_by_test_id('stMetricValue')).not_to_have_text('Unavailable')
    playwright.expect(period).to_be_checked()
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    saved = load_observations(root)[0].observations
    assert any(o.kind == 'fx' and o.subject == 'USD/EUR' and o.date == first and o.amount == Decimal('.90') for o in saved)
    assert any(o.kind == 'price' and o.subject == 'fund-b' and o.date == first and o.amount == 10 for o in saved)

def test_uncommitted_return_price_survives_two_automatic_reruns(tmp_path):
    from decimal import Decimal
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from portfolio_app.performance.history_observations import Observation, ObservationStore

    journal, _, _, revision = fixture(tmp_path)
    save_journal(tmp_path, journal, None)
    save_observations(tmp_path, ObservationStore((
        Observation('last', 'price', 'position-0', '2024-12-02', Decimal(11), 'EUR'),
    )), revision)
    script = tmp_path / 'poll.py'
    script.write_text("""
from pathlib import Path
import streamlit as st
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.performance.personal_history_refresh import refresher, RefreshStatus
from portfolio_app.performance.position_summary import render_position_summary
from portfolio_app.portfolio.positions import read_snapshot
root = Path(__file__).parent
refresher.request = lambda *a, **kw: RefreshStatus()
@st.dialog('Invented position')
def position():
    row = read_snapshot(root/'holdings.csv').holdings.iloc[0]
    render_position_summary(row, root, load_journal(root)[0], has_history=True, demo=False)
if st.button('Open position'):
    position()
""")
    with history_page(script) as page:
        automatic = []
        def sent(payload):
            message = BackMsg.FromString(payload)
            if message.rerun_script.is_auto_rerun:
                automatic.append(message.rerun_script.fragment_id)
        page.on('websocket', lambda ws: ws.on('framesent', sent))
        page.reload()
        page.get_by_role('button', name='Open position', exact=True).click()
        page.get_by_role('dialog').get_by_role('button', name='Add price', exact=True).click()
        amount = page.get_by_test_id('stPopoverBody').get_by_role('textbox', name='Price (EUR)', exact=True)
        amount.fill('12.')
        previous = len(automatic)
        deadline = time.monotonic() + 12
        while len(automatic) < previous + 2 and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        assert len(automatic) >= previous + 2
        settle(page)
        playwright.expect(amount).to_be_focused()
        playwright.expect(amount).to_have_value('12.')
        playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
