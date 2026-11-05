"""Metric actions survive a queued history poll, using synthetic data only."""
from browser_runtime import browser_session, start_server
from contextlib import suppress
import json
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from streamlit.proto.BackMsg_pb2 import BackMsg

from browser_support import StreamlitReruns, settle
from portfolio_app.account_journal import save_journal
from test_history_download import setup

playwright = pytest.importorskip('playwright.sync_api')


PREVIEW = '''
from datetime import date, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Event, Thread
import streamlit as st
from streamlit.runtime.scriptrunner.script_runner import ScriptRunner
from portfolio_app.account_journal import load_journal
from portfolio_app.history_observations import HistoryScope, Observation
import portfolio_app.history_ui as history
import portfolio_app.personal_market as market
root = Path(ROOT)

@st.cache_resource
def controls():
    state = dict(armed=Event(), entered=Event(), release=Event(), click=Event(), poll=Event(), callbacks=0)
    original_request = ScriptRunner.request_rerun
    def request(runner, data):
        accepted = original_request(runner, data)
        # Observe acceptance after real queue/coalescing; never change its data.
        if accepted and state['entered'].is_set() and not state['release'].is_set():
            if data.fragment_id and not data.is_auto_rerun:
                state['click'].set()
            elif data.is_auto_rerun and state['click'].is_set():
                state['poll'].set()
        return accepted
    ScriptRunner.request_rerun = request
    original_render = history._render_history_results
    def render(*args, **kwargs):
        if state['armed'].is_set() and not state['entered'].is_set():
            state['entered'].set()
            if not state['release'].wait(15):
                raise RuntimeError('Synthetic history gate was not released')
        return original_render(*args, **kwargs)
    history._render_history_results = render
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            command = self.path.lstrip('/')
            if command == 'arm': state['armed'].set()
            if command == 'release': state['release'].set()
            completed = state[command].wait(6) if command in ('entered', 'poll') else True
            payload = json.dumps(dict(completed=completed, callbacks=state['callbacks'],
                click=state['click'].is_set(), poll=state['poll'].is_set())).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', CONTROL_PORT), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return state
state = controls()

def prices(ticker, identity, start, end):
    # Ensure the initial history render registers its real two-second timer.
    # The driver releases this event once the first metric is mounted.
    if not state['armed'].wait(10):
        raise RuntimeError('Synthetic history download was not released')
    stamps = [start, (date.today()-timedelta(days=5)).isoformat(), end]
    return tuple(Observation(str(i), 'price', identity, stamp, Decimal(10+i), 'EUR',
        source='Invented feed') for i, stamp in enumerate(stamps))
market.fetch_security_prices = prices
market.fetch_fx = lambda source, target, start, end: (Observation('fx', 'fx', source+'/'+target,
    start, Decimal(1), source, target=target, source='Invented FX'),)
def toggle():
    state['callbacks'] += 1
    st.session_state.percent = not st.session_state.get('percent', False)
def visibility():
    st.session_state.show_summary = not st.session_state.get('show_summary', True)
st.button('Hide summary' if st.session_state.get('show_summary', True) else 'Show summary', on_click=visibility)
history.render_history_results(root, load_journal(root)[0], HistoryScope(('position-0',)), key='test_',
    automatic=True, percent=st.session_state.get('percent', False), on_toggle_gain=toggle,
    show_summary=st.session_state.get('show_summary', True))
'''


def unused_ports():
    # Keep both reservations open until their distinct ports are selected.
    with socket.socket() as server, socket.socket() as control:
        server.bind(('127.0.0.1', 0))
        control.bind(('127.0.0.1', 0))
        return server.getsockname()[1], control.getsockname()[1]


@pytest.fixture
def metric_page(tmp_path, request):
    journal, _ = setup(tmp_path)
    save_journal(tmp_path, journal, None)
    port, control_port = unused_ports()
    preview = tmp_path/'preview.py'
    script = getattr(request, 'param', PREVIEW)
    preview.write_text(script.replace('ROOT', repr(str(tmp_path))).replace('CONTROL_PORT', str(control_port)),
                       encoding='utf-8')
    def control(command):
        with urlopen(f'http://127.0.0.1:{control_port}/{command}', timeout=8) as response:
            return json.load(response)
    env = dict(os.environ, PORTFOLIO_STATE_DIR=str(tmp_path/'state'))
    with (tmp_path/'server.log').open('w', encoding='utf-8') as log:
        process = start_server([sys.executable, '-m', 'streamlit', 'run', str(preview),
            '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
            '--server.fileWatcherType=none', '--browser.gatherUsageStats=false'],
            env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                assert process.poll() is None
                try:
                    urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1).close()
                    break
                except OSError:
                    time.sleep(.1)
            with browser_session() as runner:
                browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'),
                    args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                reruns = StreamlitReruns(page, settle=settle)
                requests = []
                def connected(socket):
                    if '/_stcore/stream' not in socket.url:
                        return
                    def sent(payload):
                        message = BackMsg.FromString(payload)
                        if message.WhichOneof('type') == 'rerun_script':
                            requests.append(message.rerun_script)
                    socket.on('framesent', sent)
                page.on('websocket', connected)
                page.goto(f'http://127.0.0.1:{port}')
                try:
                    yield page, reruns, requests, control
                finally:
                    with suppress(OSError):
                        control('release')
                    browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_gain_toggle_survives_queued_history_poll_and_remount(metric_page):
    page, reruns, requests, control = metric_page
    percentage = page.get_by_role('button', name='Show gain as percentage', exact=True)
    amount = page.get_by_role('button', name='Show gain as amount', exact=True)
    playwright.expect(percentage).to_be_visible()
    reruns.wait(); settle(page)
    assert control('arm')['callbacks'] == 0
    assert control('entered')['completed'], 'The real history timer did not reach its gate'
    # Keep the original metric action and five-second UI expectation. The gate
    # ensures click then timer have both reached the server before it can rerun.
    try:
        percentage.click()
        accepted = control('poll')
        assert accepted['completed'] and accepted['click'] and accepted['poll']
    finally:
        with suppress(OSError):
            control('release')
    playwright.expect(amount).to_be_visible(timeout=5000)
    assert control('state')['callbacks'] == 1
    interactions = [r for r in requests if r.fragment_id and not r.is_auto_rerun]
    assert len(interactions) == 1
    assert any(r.is_auto_rerun and r.fragment_id == interactions[0].fragment_id for r in requests)

    # A later unchanged polling request must not replay the component action.
    def automatic(payload):
        message = BackMsg.FromString(payload)
        return message.WhichOneof('type') == 'rerun_script' and message.rerun_script.is_auto_rerun
    reruns.socket.wait_for_event('framesent', predicate=automatic, timeout=5000)
    reruns.wait(); settle(page)
    assert control('state')['callbacks'] == 1
    amount.press('Enter')
    playwright.expect(percentage).to_be_visible(timeout=5000)
    assert control('state')['callbacks'] == 2
    percentage.press('Space')
    playwright.expect(amount).to_be_visible(timeout=5000)
    assert control('state')['callbacks'] == 3
    amount.click()
    playwright.expect(percentage).to_be_visible(timeout=5000)
    assert control('state')['callbacks'] == 4

    reruns.click(page, page.get_by_role('button', name='Hide summary', exact=True))
    playwright.expect(percentage).to_have_count(0)
    reruns.click(page, page.get_by_role('button', name='Show summary', exact=True))
    playwright.expect(percentage).to_be_visible()
    assert control('state')['callbacks'] == 4
    percentage.click()
    playwright.expect(amount).to_be_visible(timeout=5000)
    assert control('state')['callbacks'] == 5
    page.reload()
    playwright.expect(percentage).to_be_visible()
    reruns.wait(); settle(page)
    assert control('state')['callbacks'] == 5
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)


REMOUNT_PREVIEW = '''
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
import streamlit as st
from streamlit.runtime.state.session_state import SessionState
from portfolio_app.metric_interactions import toggle_gain_unit, toggle_metric_value

@st.cache_resource
def controls():
    state = dict(entered=Event(), release=Event(), callbacks=0, removed=False)
    original = SessionState.on_script_finished
    def finished(session, active, *args, **kwargs):
        previous = {k: v for k, v in session._old_state.items()
                    if k.endswith('-first_gain_toggle') and isinstance(v, dict) and v.get('toggle')}
        hold = (previous and not state['entered'].is_set()
                and any(k.endswith('-second_value_toggle') for k in active)
                and kwargs.get('remove_stale_widgets', True))
        result = original(session, active, *args, **kwargs)
        if hold:
            state['removed'] = all(k not in session._old_state for k in previous)
            state['entered'].set()
            # Hold completion after genuine cleanup, without changing widget
            # input or callbacks. A real click can still send the old nonce.
            if not state['release'].wait(15):
                raise RuntimeError('Synthetic component cleanup gate was not released')
        return result
    SessionState.on_script_finished = finished
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            command = self.path.lstrip('/')
            if command == 'release': state['release'].set()
            completed = state['entered'].wait(6) if command == 'entered' else True
            payload = json.dumps(dict(completed=completed, callbacks=state['callbacks'],
                                      removed=state['removed'])).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', CONTROL_PORT), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return state
state = controls()
def toggle():
    state['callbacks'] += 1
    st.session_state.percent = not st.session_state.get('percent', False)
view = st.segmented_control('View', ['First', 'Second'], default='First', required=True)
percent = st.session_state.get('percent', False)
label = 'Show gain as amount' if percent else 'Show gain as percentage'
if view == 'First':
    with st.container(key='first'):
        st.metric('Invented value', 'EUR 100', '10%' if percent else 'EUR 10')
    toggle_gain_unit(percent=percent, on_toggle=toggle, container_key='first')
else:
    with st.container(key='second'):
        st.metric('Invented value', '10%' if percent else 'EUR 10')
    toggle_metric_value(label=label, on_toggle=toggle, container_key='second', interaction_key='gain')
'''


@pytest.mark.parametrize('metric_page', [REMOUNT_PREVIEW], indirect=True, ids=['component-cleanup'])
def test_metric_action_is_not_replayed_after_inactive_host_cleanup(metric_page):
    page, reruns, requests, control = metric_page
    first = page.locator('.st-key-first').get_by_test_id('stMetricDelta')
    second = page.locator('.st-key-second').get_by_test_id('stMetricValue')
    playwright.expect(first).to_have_attribute('role', 'button')
    reruns.wait(); settle(page)
    first.click()
    playwright.expect(first).to_have_text('10%')
    reruns.wait(); settle(page)
    assert control('state')['callbacks'] == 1
    page.get_by_role('radio', name='Second', exact=True).click()
    playwright.expect(second).to_have_text('10%')
    try:
        state = control('entered')
        assert state['completed'] and state['removed']
        # Intentionally navigate before completion: the visible replacement
        # metric is ready, but the client still owns the old component state.
        page.get_by_role('radio', name='First', exact=True).click()
        playwright.expect(first).to_have_text('10%', timeout=5000)
        assert control('state')['callbacks'] == 1
    finally:
        with suppress(OSError):
            control('release')
    # The nonce genuinely arrived on multiple browser requests; it was not
    # injected by the test. Cleanup must not turn it into another action.
    nonces = [json.loads(w.json_value)['toggle']['nonce']
              for request in requests for w in request.widget_states.widgets
              if w.id.endswith('-first_gain_toggle') and w.json_value]
    assert len(nonces) >= 3 and len(set(nonces)) == 1
    reruns.wait(); settle(page)
    first.press('Enter')
    playwright.expect(first).to_have_text('EUR 10', timeout=5000)
    assert control('state')['callbacks'] == 2
    reruns.click(page, page.get_by_role('radio', name='Second', exact=True))
    playwright.expect(second).to_have_attribute('role', 'button')
    second.press('Space')
    playwright.expect(second).to_have_text('10%', timeout=5000)
    assert control('state')['callbacks'] == 3
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
