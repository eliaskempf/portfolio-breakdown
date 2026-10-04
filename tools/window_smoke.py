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


def smoke(executable):
    if os.name != 'nt':
        raise RuntimeError('Run the frozen window smoke test on native Windows.')
    api, callback = native_api()
    with TemporaryDirectory(prefix='portfolio-native-synthetic-') as temporary:
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
                page = until(lambda: next((p for c in browser.contexts for p in c.pages
                                          if '/__portfolio_window__' in p.url), None))
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
                # The custom exit command exercises shutdown in borderless mode.
                page.get_by_role('button', name='Fullscreen', exact=True).click()
                page.get_by_role('button', name='Exit', exact=True).click()
                process.wait(timeout=25)
                assert process.returncode == 0
            assert not list((root / 'state/sessions').glob('*.json'))
            print('Frozen window: startup, charts/tabs, real F11, monitor edges, repeat launch, bundled help and shutdown passed.')
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=20)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    smoke(parser.parse_args().executable.resolve())
