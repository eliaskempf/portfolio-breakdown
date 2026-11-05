"""Exercise the real frozen WebView2 window outside the checkout, with invented data.

The debug port is enabled only in this test process's environment. Normal app
launches do not enable remote debugging. Windows 11 human acceptance stays separate.
"""
import argparse
import ctypes
from contextlib import contextmanager
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
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
    api.SetForegroundWindow.restype = wintypes.BOOL
    api.IsWindow.argtypes = api.IsIconic.argtypes = [wintypes.HWND]
    api.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    api.GetWindow.restype = wintypes.HWND
    api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    api.GetAncestor.restype = wintypes.HWND
    api.WindowFromPoint.argtypes = [wintypes.POINT]
    api.WindowFromPoint.restype = wintypes.HWND
    api.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    api.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    api.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    api.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    api.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    api.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(MouseInputEvent), ctypes.c_int]
    api.SendInput.restype = wintypes.UINT
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


def window_pid(api, handle):
    pid = wintypes.DWORD()
    if handle:
        api.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    return pid.value


def focus_for_keyboard(api, callback, process, original, page, records, iteration):
    """Select our synthetic window with real input before sending global keys.

    SetForegroundWindow may legally refuse an automation controller. One owned
    mouse click models the user selecting the app; no global focus-policy change
    or repeated activation is used. All readiness shares the existing five seconds.
    """
    deadline = time.monotonic() + 5
    record = {'iteration': iteration, 'original_hwnd': original,
              'target_pid': process.pid, 'controller_pid': os.getpid()}
    records.append(record)
    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError('Owned native foreground check did not complete within five seconds.')
        return seconds
    def snapshot():
        foreground = api.GetForegroundWindow()
        current = window_handle(api, callback, process.pid)
        return {'foreground_hwnd': foreground, 'foreground_pid': window_pid(api, foreground),
                'current_hwnd': current, 'current_pid': window_pid(api, current),
                'original_valid': bool(api.IsWindow(original)),
                'visible': bool(current and api.IsWindowVisible(current)),
                'minimized': bool(current and api.IsIconic(current)), 'process_exit': process.poll()}
    surface = page.locator('#native-smoke-focus-surface')
    previous_dpi = None
    try:
        record['before'] = snapshot()
        handle = record['before']['current_hwnd']
        if (not handle or window_pid(api, handle) != process.pid or process.poll() is not None
                or api.GetWindow(handle, 4) or api.IsIconic(handle)):
            raise AssertionError('Synthetic main window is not available for native input.')
        remaining()
        record['set_foreground_result'] = bool(api.SetForegroundWindow(handle))
        # A per-thread context keeps client coordinates and pointer APIs in the
        # same physical-pixel space, including a scaled/mixed-DPI host.
        previous_dpi = api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not previous_dpi:
            raise RuntimeError('Could not establish per-monitor DPI coordinates for native input.')
        page.locator('body').evaluate("""body => {
            const target = document.createElement('div'); target.id = 'native-smoke-focus-surface';
            target.setAttribute('aria-hidden', 'true');
            target.style.cssText = 'position:fixed;left:16px;top:16px;width:24px;height:24px;z-index:2147483647;background:#111;pointer-events:auto';
            target.addEventListener('pointerdown', event => {
                target.__down = {trusted:event.isTrusted,x:event.clientX,y:event.clientY};
            });
            target.addEventListener('pointerup', event => {
                target.__up = {trusted:event.isTrusted,x:event.clientX,y:event.clientY};
            });
            body.append(target);
        }""", timeout=remaining() * 1000)
        geometry = surface.evaluate("""target => {
            const box = target.getBoundingClientRect(), x = box.x + box.width/2, y = box.y + box.height/2;
            return {x,y,width:innerWidth,height:innerHeight,owned:document.elementFromPoint(x,y)===target};
        }""", timeout=remaining() * 1000)
        if not geometry['owned'] or not geometry['width'] or not geometry['height']:
            raise AssertionError('Synthetic inert focus surface is obscured.')
        rect, origin = wintypes.RECT(), wintypes.POINT(0, 0)
        if not api.GetClientRect(handle, ctypes.byref(rect)) or not api.ClientToScreen(handle, ctypes.byref(origin)):
            raise RuntimeError('Could not locate the owned native client area.')
        point = wintypes.POINT(round(origin.x + geometry['x'] * rect.right / geometry['width']),
                              round(origin.y + geometry['y'] * rect.bottom / geometry['height']))
        record['point'] = {'x': point.x, 'y': point.y}
        record['geometry'] = geometry
        record['client'] = {'width': rect.right, 'height': rect.bottom, 'x': origin.x, 'y': origin.y}
        def point_owned():
            hit = api.WindowFromPoint(point)
            root = api.GetAncestor(hit, 2)
            record['hit'] = {'hwnd': hit, 'root': root, 'pid': window_pid(api, hit)}
            return (window_handle(api, callback, process.pid) == handle
                    and window_pid(api, handle) == process.pid and api.IsWindowVisible(handle)
                    and not api.IsIconic(handle) and root == handle)
        remaining()
        if not point_owned():
            raise AssertionError('Native focus point is not owned by the synthetic window; refusing input.')
        if not api.SetCursorPos(point.x, point.y):
            raise RuntimeError('Could not move the pointer to the owned focus point.')
        actual = wintypes.POINT()
        if not api.GetCursorPos(ctypes.byref(actual)) or (actual.x, actual.y) != (point.x, point.y):
            raise AssertionError('Native pointer did not reach the owned focus point.')
        remaining()
        if not point_owned():
            raise AssertionError('Native focus point changed ownership; refusing input.')
        inputs = (MouseInputEvent * 2)(mouse_event(0x0002), mouse_event(0x0004))
        sent = api.SendInput(2, inputs, ctypes.sizeof(MouseInputEvent))
        record['mouse_events_sent'] = sent
        if sent != 2:
            if sent == 1:  # Release only our partially delivered button-down.
                release = mouse_event(0x0004)
                api.SendInput(1, ctypes.byref(release), ctypes.sizeof(MouseInputEvent))
            raise RuntimeError('Native owned click was not completely delivered.')
        while True:
            remaining()
            current = window_handle(api, callback, process.pid)
            pointer = surface.evaluate('target => ({down:target.__down,up:target.__up})',
                                       timeout=remaining() * 1000)
            record['pointer_events'] = pointer
            if (current == handle and window_pid(api, current) == process.pid
                    and not api.GetWindow(current, 4) and api.GetForegroundWindow() == current
                    and (pointer.get('down') or {}).get('trusted') and (pointer.get('up') or {}).get('trusted')):
                if any(abs(event[axis] - geometry[axis]) > 1
                       for event in (pointer['down'], pointer['up']) for axis in ('x', 'y')):
                    raise AssertionError('Native click did not land at the inert surface center.')
                record['after'] = snapshot()
                return current
            page.wait_for_timeout(min(50, remaining() * 1000))
    except BaseException as exc:
        record['error'] = repr(exc)
        raise
    finally:
        try:
            record['after'] = snapshot()
            if previous_dpi and not api.SetThreadDpiAwarenessContext(previous_dpi):
                record['native_cleanup_error'] = 'Could not restore the controller DPI context.'
        except Exception as exc:
            record['native_cleanup_error'] = repr(exc)
        # Cleanup cannot replace the focus failure or start another action budget.
        try:
            surface.evaluate('target => target.remove()', timeout=max(1, (deadline-time.monotonic())*1000))
        except Exception as exc:
            record['surface_cleanup_error'] = repr(exc)


class NativeMouseInput(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]


class MouseInputUnion(ctypes.Union):
    _fields_ = [('mouse', NativeMouseInput)]


class MouseInputEvent(ctypes.Structure):
    _fields_ = [('kind', wintypes.DWORD), ('data', MouseInputUnion)]


def mouse_event(flags):
    return MouseInputEvent(0, MouseInputUnion(NativeMouseInput(0, 0, 0, flags, 0, 0)))


@contextmanager
def native_evidence(browser, evidence, label):
    """Capture only this synthetic app, before CDP/browser teardown on failure."""
    records = []
    context = browser.contexts[0]
    if evidence is not None:
        evidence = Path(evidence) / label
        evidence.mkdir(parents=True, exist_ok=True)
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
    failed = False
    try:
        yield records
    except BaseException as exc:
        failed = True
        if evidence is not None:
            errors = []
            for name, capture in [
                ('screenshot', lambda: context.pages[-1].screenshot(path=str(evidence/'failure.png'), timeout=5000)),
                ('trace', lambda: context.tracing.stop(path=str(evidence/'trace.zip'))),
            ]:
                try:
                    capture()
                except Exception as capture_error:
                    errors.append({'capture': name, 'error': repr(capture_error)})
            try:
                (evidence/'failure.json').write_text(json.dumps(
                    {'error': repr(exc), 'focus': records, 'capture_errors': errors}, indent=2), encoding='utf-8')
            except Exception as capture_error:
                print(f'Native failure evidence could not be written: {capture_error}', flush=True)
        raise
    finally:
        if evidence is not None and not failed:
            try:
                context.tracing.stop()
            except Exception:
                # Successful native exit can already have closed the CDP context.
                pass
            (evidence/'focus.json').write_text(json.dumps(records, indent=2), encoding='utf-8')


def close_without_dialog(api, callback, process, handle, close):
    """A transient WinForms error dialog can appear even with exit code zero."""
    dialogs = set()
    watching = threading.Event()
    def observe():
        while not watching.is_set():
            visible = window_handle(api, callback, process.pid)
            if visible and visible != handle:
                dialogs.add(visible)
            watching.wait(.005)
    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    try:
        close()
        process.wait(timeout=25)
    finally:
        watching.set()
        observer.join(timeout=2)
    assert process.returncode == 0
    assert not dialogs, 'An unexpected native dialog appeared during shutdown'


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
    frame.get_by_role('tab', name='Positions', exact=True).click()
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


@contextmanager
def process_evidence(root, destination, stage):
    """Cover launch/attachment failures, before a browser context exists."""
    import shutil
    records = []
    destination = Path(destination) / stage if destination else None
    if destination:
        destination.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    def record(event, **details):
        records.append(dict(seconds=round(time.monotonic()-started, 6), event=event, **details))
    try:
        yield record
    except BaseException as exc:
        record('failure', type=type(exc).__name__, error=str(exc))
        raise
    finally:
        if destination:
            try:
                for path in root.rglob('*.log'):
                    target = destination / 'logs' / path.relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                (destination / 'process.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
            except Exception as exc:
                print(f'Native process evidence capture failed: {exc}', file=sys.stderr)


def smoke(executable, *, interactive=False, welcome_only=False, evidence=None):
    if os.name != 'nt':
        raise RuntimeError('Run the frozen window smoke test on native Windows.')
    api, callback = native_api()
    with TemporaryDirectory(prefix='portfolio-native-synthetic-', ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            debug_port = sock.getsockname()[1]
        stage = 'welcome' if welcome_only else 'application'
        with process_evidence(root, evidence, stage) as record, (root / 'native-process.log').open('w', encoding='utf-8') as process_log:
            # Deliberately remove every Python/uv directory from the launched app's
            # PATH. The test controller can use Python; the executable cannot rely on it.
            windows = os.environ['WINDIR']
            env = os.environ | {'PORTFOLIO_STATE_DIR': str(root / 'state'),
                'PATH': os.pathsep.join([windows, str(Path(windows) / 'System32')]),
                'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS': f'--remote-debugging-port={debug_port}',
                'PYTHONPATH': '', 'PYTHONHOME': ''}
            command = [str(executable), '--data-dir', str(root / 'workspace'), '--offline-demo']
            process = subprocess.Popen(command, cwd=root, env=env, stdout=process_log, stderr=subprocess.STDOUT)
            record('launched', pid=process.pid, debugger_port=debug_port)
            try:
                handle = until(lambda: window_handle(api, callback, process.pid))
                def debugger_ready():
                    if process.poll() is not None:
                        raise RuntimeError(f'Window exited with code {process.returncode}')
                    try:
                        with urlopen(f'http://127.0.0.1:{debug_port}/json/version', timeout=1) as response:
                            version = json.load(response)
                            record('debugger-ready', websocket=version.get('webSocketDebuggerUrl'))
                            return version
                    except OSError:
                        return None
                until(debugger_ready)
                record('attaching', returncode=process.poll())
                with sync_playwright() as playwright:
                    browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{debug_port}')
                    with native_evidence(browser, evidence, 'welcome' if welcome_only else 'application') as focus_records:
                        page = wait_for_window_page(browser)
                        frame = page.frame_locator('#portfolio-app')
                        expect(frame.get_by_role('button', name='Explore demo', exact=True)).to_be_visible(timeout=90000)
                        expect(page.locator('main')).to_have_count(0, timeout=15000)
                        if welcome_only:
                            close_without_dialog(api, callback, process, handle,
                                                 lambda: api.PostMessageW(handle, 0x0010, 0, 0))
                            assert not list((root / 'state/sessions').glob('*.json'))
                            print('Frozen window: welcome-screen close without error dialogs and session cleanup passed.')
                            return
                        frame.get_by_role('button', name='Explore demo', exact=True).click()
                        frame.get_by_role('button', name='Not now', exact=True).click()
                        expect(frame.locator('.js-plotly-plot').first).to_be_visible(timeout=30000)
                        for tab in ['Exposure', 'Positions', 'Rebalance', 'Overview']:
                            frame.get_by_role('tab', name=tab, exact=True).click()
                            expect(frame.get_by_test_id('stException')).to_have_count(0)
                        # Real F11 is sent only after confirming our own foreground HWND.
                        original_handle = handle
                        for fullscreen in (True, False):
                            handle = focus_for_keyboard(api, callback, process, original_handle, page, focus_records, fullscreen)
                            assert api.GetForegroundWindow() == handle, 'Owned window lost foreground before F11'
                            api.keybd_event(0x7A, 0, 0, 0)
                            api.keybd_event(0x7A, 0, 2, 0)
                            expect(page.get_by_role('button', name='Windowed' if fullscreen else 'Fullscreen', exact=True)).to_be_visible()
                            # Border-style changes can recreate the WinForms HWND.
                            handle = window_handle(api, callback, process.pid)
                            assert handle and window_pid(api, handle) == process.pid
                            if fullscreen:
                                class Monitor(ctypes.Structure):
                                    _fields_ = [('size', wintypes.DWORD), ('bounds', wintypes.RECT),
                                                ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
                                info = Monitor()
                                info.size = ctypes.sizeof(info)
                                assert api.GetMonitorInfoW(api.MonitorFromWindow(handle, 2), ctypes.byref(info))
                                rect, client = wintypes.RECT(), wintypes.RECT()
                                assert api.GetWindowRect(handle, ctypes.byref(rect))
                                assert api.GetClientRect(handle, ctypes.byref(client))
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
                        expect(page.get_by_role('button', name='Windowed', exact=True)).to_be_visible()
                        handle = window_handle(api, callback, process.pid)
                        assert handle and window_pid(api, handle) == process.pid
                        close_without_dialog(api, callback, process, handle,
                                             lambda: page.get_by_role('button', name='Exit', exact=True).click())
                assert not list((root / 'state/sessions').glob('*.json'))
                print('Frozen window: startup, charts/tabs, real F11, monitor edges, repeat launch, bundled help and shutdown passed.')
            finally:
                record('before-cleanup', returncode=process.poll())
                if process.poll() is None:
                    if (handle := window_handle(api, callback, process.pid)):
                        api.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE: normal owned-window cleanup.
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=20)
                record('cleanup-complete', returncode=process.poll())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--interactive', action='store_true', help='Also exercise native file dialogs and the system browser')
    parser.add_argument('--evidence', type=Path, help='Synthetic native failure evidence directory')
    parser.add_argument('--stage', choices=['all', 'startup', 'interaction'], default='all')
    args = parser.parse_args()
    if args.stage in ('all', 'startup'):
        smoke(args.executable.resolve(), welcome_only=True, evidence=args.evidence)
    if args.stage in ('all', 'interaction'):
        smoke(args.executable.resolve(), interactive=args.interactive, evidence=args.evidence)
