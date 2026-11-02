"""Delayed native-window navigation with a real browser and synthetic pages."""
import importlib.util
import os
from pathlib import Path

import pytest

playwright = pytest.importorskip('playwright.sync_api')
spec = importlib.util.spec_from_file_location('window_smoke', Path(__file__).resolve().parents[1] / 'tools/window_smoke.py')
window_smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(window_smoke)


def test_native_smoke_receives_navigation_after_attaching():
    with playwright.sync_playwright() as runner:
        browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
        try:
            context = browser.new_context()
            context.route('http://example.invalid/**', lambda route: route.fulfill(
                content_type='text/html', body='<p>Invented native startup</p>'))
            page = context.new_page()
            page.goto('http://example.invalid/starting')
            page.evaluate("setTimeout(() => location.href = '/__portfolio_window__', 250)")
            assert page.url.endswith('/starting')
            found = window_smoke.wait_for_window_page(browser, timeout=5000)
            assert found is page
            assert found.url.endswith('/__portfolio_window__')
            playwright.expect(found.get_by_text('Invented native startup', exact=True)).to_be_visible()
        finally:
            browser.close()


def focus_driver(monkeypatch, *, foreign=False, no_delivery=False, slow=False):
    """Model OS ownership separately from browser pointer acknowledgement."""
    from types import SimpleNamespace
    clock = [0.0]
    monkeypatch.setattr(window_smoke.time, 'monotonic', lambda: clock[0])
    state = SimpleNamespace(hwnd=22, foreground=99, cursor=None, sent=0, activation=0,
                            removed=False, pointer={'down': None, 'up': None}, timeouts=[], dpi=[])
    class Page:
        def locator(self, selector):
            return self
        def evaluate(self, script, *, timeout):
            state.timeouts.append(timeout)
            if 'body.append' in script:
                if slow:
                    clock[0] += 4.9
            elif 'getBoundingClientRect' in script:
                return dict(x=28, y=28, width=800, height=600, owned=True)
            elif '__down' in script:
                return state.pointer
            elif 'remove' in script:
                state.removed = True
        def wait_for_timeout(self, delay):
            clock[0] += delay / 1000
    page = Page()
    class API:
        def GetWindowThreadProcessId(self, handle, pointer):
            pointer._obj.value = 321 if handle == state.hwnd else 654
        def GetForegroundWindow(self):
            return state.foreground
        def SetForegroundWindow(self, handle):
            state.activation += 1
            return 0  # Legal denial: the native owned click must establish focus.
        def IsWindow(self, handle):
            return handle == state.hwnd
        def IsWindowVisible(self, handle):
            return True
        def IsIconic(self, handle):
            return False
        def GetWindow(self, handle, kind):
            return 0
        def SetThreadDpiAwarenessContext(self, context):
            state.dpi.append(context)
            return 7
        def GetClientRect(self, handle, pointer):
            pointer._obj.right, pointer._obj.bottom = 1600, 1200
            return True
        def ClientToScreen(self, handle, pointer):
            pointer._obj.x, pointer._obj.y = 100, 200
            return True
        def WindowFromPoint(self, point):
            return 88
        def GetAncestor(self, handle, kind):
            return 99 if foreign else state.hwnd
        def SetCursorPos(self, x, y):
            state.cursor = (x, y)
            return True
        def GetCursorPos(self, pointer):
            pointer._obj.x, pointer._obj.y = state.cursor
            return True
        def SendInput(self, count, inputs, size):
            state.sent += count
            if not no_delivery:
                state.foreground = state.hwnd
                state.pointer = {'down': {'trusted': True, 'x': 28, 'y': 28}, 'up': {'trusted': True, 'x': 28, 'y': 28}}
            return count
    monkeypatch.setattr(window_smoke, 'window_handle', lambda *args: state.hwnd)
    return API(), SimpleNamespace(pid=321, poll=lambda: None), page, state, clock


def test_native_focus_uses_owned_physical_click_after_legal_denial_and_handle_recreation(monkeypatch):
    api, process, page, state, _ = focus_driver(monkeypatch)
    records = []
    evaluate = page.evaluate
    pending = [{'down': None, 'up': None}, {'down': {'trusted': True, 'x': 28, 'y': 28}, 'up': None}]
    def delayed(script, *, timeout):
        if '__down' in script and pending:
            return pending.pop(0)
        return evaluate(script, timeout=timeout)
    page.evaluate = delayed
    assert window_smoke.focus_for_keyboard(api, None, process, 11, page, records, True) == 22
    assert not pending
    assert state.activation == 1 and state.sent == 2
    assert state.cursor == (156, 256)  # Physical client pixels at 200% scaling.
    assert state.removed and state.dpi[-1] == 7
    assert records[0]['original_hwnd'] == 11 and not records[0]['before']['original_valid']
    assert records[0]['set_foreground_result'] is False
    assert records[0]['after']['foreground_hwnd'] == 22
    assert records[0]['pointer_events']['down']['trusted']


def test_native_focus_refuses_occluded_or_moved_point_without_click(monkeypatch):
    api, process, page, state, _ = focus_driver(monkeypatch, foreign=True)
    with pytest.raises(AssertionError, match='not owned'):
        window_smoke.focus_for_keyboard(api, None, process, 11, page, [], True)
    assert state.sent == 0 and state.cursor is None and state.removed
    # Ownership can change between initial hit-test and moving the pointer.
    api, process, page, state, _ = focus_driver(monkeypatch)
    original = api.SetCursorPos
    def move(x, y):
        original(x, y)
        api.GetAncestor = lambda *args: 99
        return True
    api.SetCursorPos = move
    with pytest.raises(AssertionError, match='changed ownership'):
        window_smoke.focus_for_keyboard(api, None, process, 11, page, [], True)
    assert state.sent == 0 and state.removed
    api, process, page, state, _ = focus_driver(monkeypatch)
    api.SetCursorPos = lambda *args: False
    with pytest.raises(RuntimeError, match='move the pointer'):
        window_smoke.focus_for_keyboard(api, None, process, 11, page, [], True)
    assert state.sent == 0 and state.removed
    api, process, page, state, _ = focus_driver(monkeypatch)
    api.SendInput = lambda *args: 0
    records = []
    with pytest.raises(RuntimeError, match='completely delivered'):
        window_smoke.focus_for_keyboard(api, None, process, 11, page, records, True)
    assert records[0]['mouse_events_sent'] == 0 and state.removed


def test_native_focus_deadline_includes_browser_work_and_requires_pointer_delivery(monkeypatch):
    api, process, page, state, clock = focus_driver(monkeypatch, no_delivery=True, slow=True)
    # Foreground equality alone cannot accept a click that missed the inert surface.
    state.foreground = state.hwnd
    records = []
    with pytest.raises(TimeoutError, match='five seconds'):
        window_smoke.focus_for_keyboard(api, None, process, 11, page, records, False)
    assert clock[0] == pytest.approx(5) and state.activation == 1 and state.sent == 2
    assert max(state.timeouts[1:]) <= 101
    assert state.removed and records[0]['iteration'] is False and records[0]['error']


def test_native_failure_evidence_is_captured_before_browser_teardown(tmp_path):
    import json
    with playwright.sync_playwright() as runner:
        browser = runner.chromium.launch(executable_path=os.environ.get('PORTFOLIO_TEST_CHROMIUM'), args=['--no-sandbox'])
        try:
            page = browser.new_page()
            page.set_content('<p>Invented native focus evidence</p>')
            primary = RuntimeError('Invented native focus failure')
            with pytest.raises(RuntimeError) as caught:
                with window_smoke.native_evidence(browser, tmp_path, 'application') as records:
                    records.append({'iteration': True, 'target_pid': 123})
                    raise primary
            assert caught.value is primary and not page.is_closed()
            evidence = tmp_path / 'application'
            assert (evidence/'failure.png').stat().st_size > 0
            assert (evidence/'trace.zip').stat().st_size > 0
            saved = json.loads((evidence/'failure.json').read_text())
            assert saved['focus'] == records and not saved['capture_errors']
            # Capture failure must still propagate the original acceptance error.
            page.screenshot = lambda **kw: (_ for _ in ()).throw(ValueError('Invented capture failure'))
            with pytest.raises(RuntimeError) as caught:
                with window_smoke.native_evidence(browser, tmp_path, 'capture-failure'):
                    raise primary
            assert caught.value is primary
            saved = json.loads((tmp_path/'capture-failure/failure.json').read_text())
            assert saved['capture_errors'][0]['capture'] == 'screenshot'
        finally:
            browser.close()
