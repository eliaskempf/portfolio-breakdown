"""Exercise the real frozen WebView2 window outside the checkout, with invented data.

The debug port is enabled only in this test process's environment. Normal app
launches do not enable remote debugging. Windows 11 human acceptance stays separate.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect


def until(check, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.1)
    raise TimeoutError('Native window check did not complete.')


def wait_for_window_page(browser, timeout=90000):
    """Wait for the single startup window without blocking Playwright events."""
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.wait_for_event('page', timeout=timeout)
    page.wait_for_url('**/__portfolio_window__', timeout=timeout)
    return page


def native_api():
    api = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetForegroundWindow.restype = wintypes.HWND
    api.SetForegroundWindow.argtypes = [wintypes.HWND]
    api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    api.MonitorFromWindow.restype = wintypes.HANDLE
    api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    return api, callback


def window_handle(api, callback, pid):
    found = []
    def inspect(handle, _):
        owner = wintypes.DWORD()
        api.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid and api.IsWindowVisible(handle):
            found.append(handle)
        return True
    api.EnumWindows(callback(inspect), 0)
    return found[0] if found else None


def native_file_choice(api, callback, owner, path, errors):
    """Drive only a file dialog owned by our app, using language-neutral IDs."""
    try:
        import clr
        framework = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/WPF'
        clr.AddReference(str(framework / 'UIAutomationTypes.dll'))
        clr.AddReference(str(framework / 'UIAutomationClient.dll'))
        from System import IntPtr
        from System.Windows.Automation import AutomationElement, TreeScope, PropertyCondition, AndCondition, ControlType, ValuePattern, InvokePattern
        api.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        api.GetWindow.restype = wintypes.HWND
        api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        api.GetAncestor.restype = wintypes.HWND
        api.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        def find_dialog():
            found = []
            def inspect(handle, _):
                name = ctypes.create_unicode_buffer(100)
                api.GetClassNameW(handle, name, len(name))
                parent = api.GetWindow(handle, 4)  # GW_OWNER
                if (name.value == '#32770' and api.IsWindowVisible(handle)
                        and parent and api.GetAncestor(parent, 2) == owner):
                    found.append(handle)
                return True
            api.EnumWindows(callback(inspect), 0)
            return found[0] if found else None
        dialog_handle = until(find_dialog, 15)
        dialog = AutomationElement.FromHandle(IntPtr(dialog_handle))
        def filename_field():
            for identifier in ('1148', '1001'):
                element = dialog.FindFirst(TreeScope.Descendants, AndCondition(
                    PropertyCondition(AutomationElement.AutomationIdProperty, identifier),
                    PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Edit)))
                if element is not None:
                    return element
        edit = until(filename_field, 5)
        edit.SetFocus()
        edit.GetCurrentPattern(ValuePattern.Pattern).SetValue(str(path))
        assert edit.GetCurrentPattern(ValuePattern.Pattern).Current.Value == str(path)
        # File-list items can also have ID 1, so require the button role.
        button = until(lambda: dialog.FindFirst(TreeScope.Descendants, AndCondition(
            PropertyCondition(AutomationElement.AutomationIdProperty, '1'),
            PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button))), 5)
        button.GetCurrentPattern(InvokePattern.Pattern).Invoke()
        until(lambda: not api.IsWindowVisible(dialog_handle), 10)
    except BaseException as exc:
        errors.append(exc)


def file_and_link_checks(page, frame, api, callback, handle, root):
    frame.get_by_role('combobox', name='Portfolio workspace', exact=True).click()
    frame.get_by_role('option', name='My portfolio', exact=True).click()
    frame.get_by_role('button', name='Start my portfolio', exact=True).click()
    frame.get_by_role('button', name='Skip setup', exact=True).click()
    frame.get_by_role('button', name='Import portfolio — experimental', exact=True).click()
    upload = root / 'invented-native-upload.csv'
    upload.write_text('Name;Quantity\nInvented native upload;2,5\n', encoding='utf-8')
    errors = []
    def choose(path):
        thread = threading.Thread(target=native_file_choice,
            args=(api, callback, handle, path, errors), daemon=True)
        thread.start()
        return thread
    worker = choose(upload)
    expect(frame.locator('input[type=file]')).to_have_count(1)
    frame.get_by_test_id('stFileUploaderDropzone').click(no_wait_after=True)
    deadline = time.monotonic() + 25
    while worker.is_alive() and time.monotonic() < deadline:
        # Keep Playwright's route/event loop running while Windows shows a dialog.
        page.wait_for_timeout(100)
    if errors:
        raise errors[0]
    assert not worker.is_alive()
    units = frame.get_by_text('Quantities are shares/units and prices are amounts per unit (not nominal values or percent quotes)', exact=True)
    expect(units).to_be_visible(timeout=15000)
    units.click()
    frame.get_by_role('button', name='Import reviewed positions', exact=True).click()
    expect(frame.get_by_text('Portfolio imported.', exact=False)).to_be_visible()
    assert 'Invented native upload' in (root / 'workspace/holdings.csv').read_text(encoding='utf-8')
    # The app currently has no export button. Exercise WebView2's real download
    # handler with an invented same-origin attachment, without adding app routes.
    cdp = page.context.new_cdp_session(page)
    cdp.send('Browser.setDownloadBehavior', {'behavior': 'default'})
    attachment = page.url.split('/__portfolio_window__')[0] + '/synthetic-download.txt'
    page.route(attachment, lambda route: route.fulfill(status=200,
        headers={'Content-Disposition': 'attachment; filename="synthetic-download.txt"'},
        content_type='application/octet-stream', body='Invented native download'))
    download = root / 'invented-native-download.txt'
    worker = choose(download)
    page.evaluate("url => { const a = document.createElement('a'); a.href=url; a.click(); }", attachment)
    deadline = time.monotonic() + 25
    while worker.is_alive() and time.monotonic() < deadline:
        # Keep Playwright's route/event loop running while Windows shows a dialog.
        page.wait_for_timeout(100)
    if errors:
        raise errors[0]
    until(lambda: download.exists() and download.read_bytes() == b'Invented native download', 15)
    # A distinct loopback origin is external to the app, just like an HTTPS link.
    # Its only content is a synthetic success page, opened in the system browser.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    opened = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            opened.set()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<h1>Synthetic external-link check passed</h1><p>You can close this tab.</p>')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = page.url
    try:
        page.evaluate("url => window.open(url, '_blank')", f'http://127.0.0.1:{server.server_port}/')
        assert opened.wait(15), 'The system browser did not open the external link'
        assert page.url == original
    finally:
        server.shutdown()
        server.server_close()
    print('Native upload/save dialogs, saved synthetic CSV, download bytes and system-browser external link passed.', flush=True)


def smoke(executable, *, interactive=False):
    if os.name != 'nt':
        raise RuntimeError('Run the frozen window smoke test on native Windows.')
    api, callback = native_api()
    with TemporaryDirectory(prefix='portfolio-native-synthetic-', ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            debug_port = sock.getsockname()[1]
        # Deliberately remove every Python/uv directory from the launched app's
        # PATH. The test controller can use Python; the executable cannot rely on it.
        windows = os.environ['WINDIR']
        env = os.environ | {'PORTFOLIO_STATE_DIR': str(root / 'state'),
            'PATH': os.pathsep.join([windows, str(Path(windows) / 'System32')]),
            'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS': f'--remote-debugging-port={debug_port}',
            'PYTHONPATH': '', 'PYTHONHOME': ''}
        command = [str(executable), '--data-dir', str(root / 'workspace'), '--offline-demo']
        process = subprocess.Popen(command, cwd=root, env=env)
        try:
            handle = until(lambda: window_handle(api, callback, process.pid))
            def debugger_ready():
                if process.poll() is not None:
                    raise RuntimeError(f'Window exited with code {process.returncode}')
                try:
                    with urlopen(f'http://127.0.0.1:{debug_port}/json/version', timeout=1) as response:
                        return json.load(response)
                except OSError:
                    return None
            until(debugger_ready)
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{debug_port}')
                page = wait_for_window_page(browser)
                frame = page.frame_locator('#portfolio-app')
                expect(frame.get_by_role('button', name='Explore demo', exact=True)).to_be_visible(timeout=90000)
                expect(page.locator('main')).to_have_count(0, timeout=15000)
                frame.get_by_role('button', name='Explore demo', exact=True).click()
                expect(frame.locator('.js-plotly-plot').first).to_be_visible(timeout=30000)
                for tab in ['Exposure', 'Positions', 'Rebalance', 'Overview']:
                    frame.get_by_role('tab', name=tab, exact=True).click()
                    expect(frame.get_by_test_id('stException')).to_have_count(0)
                # Real F11 is sent only after confirming our own foreground HWND.
                for fullscreen in (True, False):
                    api.SetForegroundWindow(handle)
                    until(lambda: api.GetForegroundWindow() == handle, 5)
                    api.keybd_event(0x7A, 0, 0, 0)
                    api.keybd_event(0x7A, 0, 2, 0)
                    expect(page.get_by_role('button', name='Windowed' if fullscreen else 'Fullscreen', exact=True)).to_be_visible()
                    if fullscreen:
                        class Monitor(ctypes.Structure):
                            _fields_ = [('size', wintypes.DWORD), ('bounds', wintypes.RECT),
                                        ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
                        info = Monitor()
                        info.size = ctypes.sizeof(info)
                        api.GetMonitorInfoW(api.MonitorFromWindow(handle, 2), ctypes.byref(info))
                        rect, client = wintypes.RECT(), wintypes.RECT()
                        api.GetWindowRect(handle, ctypes.byref(rect))
                        api.GetClientRect(handle, ctypes.byref(client))
                        assert tuple(getattr(rect, k) for k in ('left', 'top', 'right', 'bottom')) == tuple(
                            getattr(info.bounds, k) for k in ('left', 'top', 'right', 'bottom'))
                        assert client.right == rect.right - rect.left and client.bottom == rect.bottom - rect.top
                # Repeat launch must focus the existing process, not create a server.
                repeat = subprocess.run(command, cwd=root, env=env, timeout=20)
                assert repeat.returncode == 0 and process.poll() is None
                assert window_handle(api, callback, process.pid) == handle
                frame.get_by_role('button', name='?', exact=True).click()
                help_link = frame.get_by_role('link', name='User guide', exact=False)
                href = help_link.get_attribute('href')
                assert href.startswith('http://127.0.0.1:')
                with urlopen(href, timeout=3) as response:
                    assert b'Portfolio Breakdown' in response.read()
                frame.get_by_role('button', name='?', exact=True).click()
                if interactive:
                    file_and_link_checks(page, frame, api, callback, handle, root)
                # The custom exit command exercises shutdown in borderless mode.
                page.get_by_role('button', name='Fullscreen', exact=True).click()
                page.get_by_role('button', name='Exit', exact=True).click()
                process.wait(timeout=25)
                assert process.returncode == 0
            assert not list((root / 'state/sessions').glob('*.json'))
            print('Frozen window: startup, charts/tabs, real F11, monitor edges, repeat launch, bundled help and shutdown passed.')
        finally:
            if process.poll() is None:
                if (handle := window_handle(api, callback, process.pid)):
                    api.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE: normal owned-window cleanup.
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=20)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--interactive', action='store_true', help='Also exercise native file dialogs and the system browser')
    args = parser.parse_args()
    smoke(args.executable.resolve(), interactive=args.interactive)
