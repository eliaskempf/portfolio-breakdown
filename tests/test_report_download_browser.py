"""Deferred report downloads retain their callback while its worker is pending."""
from browser_runtime import browser_session, start_server
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from browser_support import settle, settle_layout
from portfolio_app.ingestion.statement_diagnostics import layout_report
from test_consors_pdf import statement

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def report_page(tmp_path):
    source = statement(missing=('price',))
    (tmp_path / 'invented-missing-price.pdf').write_bytes(source)
    script = tmp_path / 'report_preview.py'
    script.write_text('''
import io
import json
import time
from pathlib import Path
import streamlit as st
from streamlit.runtime.media_file_manager import MediaFileManager
from portfolio_app.ingestion.consors_pdf import parse_consors_batch
from portfolio_app.interface.views.ingestion.transaction_import_ui import _statement_details

root = Path(__file__).parent
if not hasattr(MediaFileManager, '_synthetic_report_gate'):
    original = MediaFileManager.execute_deferred
    def gated_lookup(self, file_id):
        marker = root / 'worker-entered.tmp'
        marker.write_text(json.dumps({'file_id': file_id}))
        marker.replace(root / 'worker-entered.json')
        deadline = time.monotonic() + 10
        while not (root / 'release-worker').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('Synthetic deferred worker gate was not released')
            time.sleep(.01)
        return original(self, file_id)
    MediaFileManager.execute_deferred = gated_lookup
    MediaFileManager._synthetic_report_gate = True

@st.fragment
def report_view():
    st.session_state.report_runs = st.session_state.get('report_runs', 0) + 1
    st.caption('Synthetic report renders: ' + str(st.session_state.report_runs))
    uploaded = io.BytesIO((root / 'invented-missing-price.pdf').read_bytes())
    uploaded.name = 'invented-missing-price.pdf'
    _statement_details([uploaded], parse_consors_batch([uploaded.getvalue()]), 'synthetic_')
report_view()
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with (tmp_path / 'server.log').open('w') as log:
        process = start_server([
            sys.executable, '-m', 'streamlit', 'run', str(script),
            '--server.address=127.0.0.1', f'--server.port={port}',
            '--server.headless=true', '--server.fileWatcherType=none',
            '--browser.gatherUsageStats=false',
        ], stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1)
                    break
                except OSError:
                    time.sleep(.1)
            with browser_session() as runner:
                browser = runner.chromium.launch(
                    executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                try:
                    yield page, f'http://127.0.0.1:{port}', tmp_path, source
                finally:
                    (tmp_path / 'release-worker').touch()
                    browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_report_download_does_not_rerun_while_deferred_worker_waits(report_page):
    from streamlit.proto.BackMsg_pb2 import BackMsg

    page, url, root, source = report_page
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
    page.goto(url)
    page.get_by_text('Statement details', exact=True).wait_for()
    settle(page)
    page.get_by_text('Statement details', exact=True).click()
    settle_layout(page)
    playwright.expect(page.get_by_text('Synthetic report renders: 1', exact=True)).to_be_visible()
    requests.clear()
    button = page.get_by_role('button', name='Download masked layout report', exact=True)
    with page.expect_download() as pending:
        button.click()
        try:
            # Hold the actual deferred worker before its ID lookup. The browser
            # and script runner remain free to finish any self-triggered rerun,
            # which would replace and orphan the pending callback.
            deadline = time.monotonic() + 5
            while not (root / 'worker-entered.json').exists():
                assert time.monotonic() < deadline, 'Deferred worker did not enter its gate'
                page.wait_for_timeout(10)
            assert json.loads((root / 'worker-entered.json').read_text())['file_id']
            settle(page)
            playwright.expect(button).to_be_disabled()
            assert not requests, 'Downloading the report must not request a script rerun'
            playwright.expect(page.get_by_text('Synthetic report renders: 1', exact=True)).to_be_visible()
        finally:
            (root / 'release-worker').touch()
    downloaded = Path(pending.value.path()).read_bytes()
    assert downloaded == layout_report([source]).encode('utf-8')
    report = downloaded.decode('utf-8')
    assert all(secret not in report for secret in
               ('XX0000000000', 'SYN001', 'INVENTED FUND', '100,00', '107,00'))
    settle(page)
    assert not requests
    playwright.expect(page.get_by_test_id('stDownloadButtonError')).to_have_count(0)
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
