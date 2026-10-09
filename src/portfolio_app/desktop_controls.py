"""Opt-in synthetic native-panel and window-state checks, never normal startup."""
import os
from pathlib import Path
import sys
import threading
import time

from portfolio_app.desktop_probe import wait


def gui(window, callback):
    done = threading.Event()
    result, errors = [], []
    def invoke():
        try:
            result.append(callback())
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()
    if sys.platform == 'darwin':
        from PyObjCTools import AppHelper
        AppHelper.callAfter(invoke)
    else:
        window._probe_dispatcher.call.emit(invoke)
    assert done.wait(15), 'Native GUI operation timed out'
    if errors:
        raise errors[0]
    return result[0]


def x11_keyboard(wid, type_key=False):
    """Observe X server keyboard ownership; optionally type into our own window."""
    import ctypes as C
    x = C.CDLL('libX11.so.6')
    x.XOpenDisplay.argtypes = [C.c_char_p]
    x.XOpenDisplay.restype = C.c_void_p
    x.XGetInputFocus.argtypes = [C.c_void_p, C.POINTER(C.c_ulong), C.POINTER(C.c_int)]
    x.XCloseDisplay.argtypes = [C.c_void_p]
    display = x.XOpenDisplay(None)
    assert display, 'Could not inspect the synthetic X11 session'
    try:
        focused, revert = C.c_ulong(), C.c_int()
        x.XGetInputFocus(display, C.byref(focused), C.byref(revert))
        owned = focused.value == wid
        if type_key:
            assert owned, 'Synthetic app does not own keyboard focus'
            x.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
            x.XKeysymToKeycode.restype = C.c_ubyte
            x.XFlush.argtypes = [C.c_void_p]
            xt = C.CDLL('libXtst.so.6')
            xt.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
            code = x.XKeysymToKeycode(display, ord('z'))
            assert code, 'Synthetic key has no keyboard mapping'
            for pressed in (1, 0):
                assert xt.XTestFakeKeyEvent(display, code, pressed, 0)
            x.XFlush(display)
        return owned
    finally:
        x.XCloseDisplay(display)


def window_states(window, workspace, record, output):
    from portfolio_app.launcher import request_instance
    wayland = False
    if sys.platform == 'linux':
        from qtpy.QtGui import QGuiApplication
        wayland = QGuiApplication.platformName().startswith('wayland')
    def state():
        if sys.platform == 'darwin':
            from webview.platforms.cocoa import BrowserView
            native = BrowserView.instances[window.uid].window
            return dict(minimized=bool(native.isMiniaturized()), active=bool(native.isKeyWindow()),
                        visible=bool(native.isVisible()))
        native = window.native
        return dict(minimized=native.isMinimized(),
                    active=native.isActiveWindow() if wayland else x11_keyboard(int(native.winId())),
                    visible=native.isVisible(), qt_active=native.isActiveWindow())
    last = {}
    def matches(**expected):
        last.update(gui(window, state))
        return all(last[k] == v for k, v in expected.items())
    def scene_visible(expected, phase):
        import re
        import subprocess
        from portfolio_app.desktop import system_environment
        assert os.environ.get('PORTFOLIO_TEST_WESTON_SCENE') == '1', 'Wayland compositor-side verification unavailable'
        result = subprocess.run(['weston-debug', 'scene-graph'], env=system_environment(),
                                capture_output=True, text=True, check=True, timeout=5)
        (output / f'wayland-scene-{phase}.txt').write_text(result.stdout, encoding='utf-8')
        present = bool(re.search(rf'View \d+ \(role xdg_toplevel, PID {os.getpid()},', result.stdout))
        return present == expected
    def desktop_hidden():
        if sys.platform != 'linux':
            return True
        if wayland:
            return scene_visible(False, 'hidden')
        import re
        import subprocess
        from portfolio_app.desktop import system_environment
        wid = gui(window, lambda: int(window.native.winId()))
        result = subprocess.run(['xprop', '-root', '_NET_CLIENT_LIST'],
                                env=system_environment(), capture_output=True, text=True,
                                check=True, timeout=5)
        (output / 'x11-hidden.txt').write_text(result.stdout, encoding='utf-8')
        return wid not in {int(value, 16) for value in re.findall(r'0x[0-9a-fA-F]+', result.stdout)}
    window.evaluate_js("""(() => {
        const input = document.createElement('input'); input.id = 'native-focus-preserved';
        input.value = 'Invented unsaved input'; document.body.append(input);
        window.__nativeFocusDocument = 'Invented document sentinel';
        const keyboard = document.createElement('input'); keyboard.id = 'native-focus-delivery';
        document.body.append(keyboard);
    })()""")
    key_count = 0
    def keyboard_delivery():
        nonlocal key_count
        if sys.platform != 'linux' or wayland:
            return
        # Qt can retain a stale inactive flag after hide/show even though the
        # server gives this window keyboard focus. Verify actual OS key delivery
        # to WebEngine, without directing an event to a widget or changing focus.
        window.evaluate_js("document.querySelector('#native-focus-delivery').focus()")
        gui(window, lambda: x11_keyboard(int(window.native.winId()), type_key=True))
        key_count += 1
        wait(lambda: window.evaluate_js("document.querySelector('#native-focus-delivery').value") == 'z' * key_count,
             timeout=5)
    try:
        if wayland:
            wait(lambda: scene_visible(True, 'initial'), timeout=15)
            window.minimize()
            wait(lambda: scene_visible(False, 'minimized'), timeout=15)
            assert matches(visible=True), 'Minimize unexpectedly hid/destroyed the client window'
            record('Wayland compositor confirms minimized window removed from visible scene')
            assert request_instance(workspace, 'focus')
            wait(lambda: scene_visible(True, 'restored') and matches(active=True, visible=True), timeout=15)
            record('repeat-launch focus restores and activates minimized native window')
        else:
            window.minimize()
            wait(lambda: matches(minimized=True), timeout=15)
            record('desktop confirms window minimized')
            assert request_instance(workspace, 'focus')
            wait(lambda: matches(minimized=False, active=True, visible=True), timeout=15)
            keyboard_delivery()
            record('repeat-launch focus restores and activates minimized native window')
        window.hide()
        # Client visibility changes before the desktop processes UnmapNotify.
        # Require actual removal before testing a subsequent user focus request.
        wait(lambda: matches(visible=False) and desktop_hidden(), timeout=15)
        assert request_instance(workspace, 'focus')
        wait(lambda: matches(visible=True, active=True), timeout=15)
        keyboard_delivery()
        record('repeat-launch focus shows and activates hidden native window')
        if key_count:
            record('X11 server keyboard focus and real key delivery verified after both restores')
        assert window.evaluate_js("document.querySelector('#native-focus-preserved')?.value") == 'Invented unsaved input'
        assert window.evaluate_js('window.__nativeFocusDocument') == 'Invented document sentinel'
        record('focus changes preserve the existing document and unsaved input')
    except TimeoutError as exc:
        if sys.platform == 'linux' and not wayland:
            import subprocess
            from portfolio_app.desktop import system_environment
            wid = gui(window, lambda: str(int(window.native.winId())))
            with (output / 'x11-focus.txt').open('w') as log:
                for args in [['-root', '_NET_ACTIVE_WINDOW', '_NET_CLIENT_LIST'],
                             ['-id', wid, '_NET_WM_STATE', 'WM_STATE', '_NET_WM_USER_TIME']]:
                    subprocess.run(['xprop', *args], stdout=log, stderr=log,
                                   env=system_environment(), timeout=5)
                log.write(f'X11 keyboard ownership: {gui(window, lambda: x11_keyboard(int(wid)))}\n')
                log.write(f'Owned window: {wid}; Qt state: {last}\n')
        raise AssertionError(f'Window state did not converge: {last}') from exc
    finally:
        window.evaluate_js("document.querySelector('#native-focus-preserved')?.remove(); document.querySelector('#native-focus-delivery')?.remove()")


class CocoaPanelSequence:
    """Advance native input only after the remote panel reaches its next state."""

    def __init__(self):
        self.phase = 0

    def advance(self, *, panel_ready, location_ready, selected):
        if self.phase == 0 and panel_ready:
            self.phase = 1
            return 'open-location'
        if self.phase == 1 and location_ready:
            self.phase = 2
            return 'type-path'
        if self.phase == 2 and location_ready:
            self.phase = 3
            return 'confirm-location'
        if self.phase == 3 and panel_ready and selected:
            self.phase = 4
            return 'confirm-panel'
        return None


def cocoa_path_input(path, key, appkit, *, hosted):
    """Paste one complete synthetic path on disposable hosts, without key floods."""
    key('', 0, appkit.NSEventModifierFlagCommand)
    if hosted:
        pasteboard = appkit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        assert pasteboard.setString_forType_(str(path), appkit.NSPasteboardTypeString)
        key('', 9, appkit.NSEventModifierFlagCommand)  # Cmd+V
    else:
        for char in str(path):
            key(char, 0)


def cocoa_interactions(window, root, record, output=None):
    """Send local native input to WebKit and its real modal file panels."""
    import AppKit as A
    import Foundation as F
    import Quartz as Q
    from webview.platforms.cocoa import BrowserView
    browser = BrowserView.instances[window.uid]
    app = A.NSApplication.sharedApplication()
    timers, events, errors, trace = [], [], [], []
    from portfolio_app.cocoa_diagnostics import PanelEvidence, enabled
    evidence = PanelEvidence(output / 'panels') if output is not None and enabled() else None
    def observe(label, panel, screenshot=False):
        if evidence is not None:
            try:
                evidence.record(label, A, panel, screenshot=screenshot)
            except Exception as exc:
                trace.append(['evidence-error', str(exc)])
    upload = root / 'invented-upload.txt'
    upload.write_text('Invented native upload', encoding='utf-8')
    download = root / 'invented-download.txt'
    original_download_delegate = BrowserView.DownloadDelegate
    download_events = []
    class SyntheticDownloadDelegate(original_download_delegate):
        def downloadDidFinish_(self, download):
            download_events.append('finished')
        def download_didFailWithError_resumeData_(self, download, error, data):
            download_events.append(str(error))
    BrowserView.DownloadDelegate = SyntheticDownloadDelegate

    hosted_input = (os.environ.get('PORTFOLIO_TEST_HOSTED_INPUT') == '1'
                    and os.environ.get('GITHUB_ACTIONS') == 'true'
                    and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted')

    def key(chars, code, modifiers=0):
        # Modern file panels live in another process. System input is allowed
        # only in explicitly opted-in, disposable GitHub-hosted test sessions,
        # and only while our synthetic app owns the foreground window.
        if hosted_input:
            front = A.NSWorkspace.sharedWorkspace().frontmostApplication()
            assert front and front.processIdentifier() == os.getpid(), 'Synthetic app lost foreground ownership'
        for pressed in [True, False]:
            event = Q.CGEventCreateKeyboardEvent(None, code, pressed)
            Q.CGEventSetFlags(event, modifiers)
            if chars:
                Q.CGEventKeyboardSetUnicodeString(event, len(chars), chars)
            if hosted_input:
                Q.CGEventPost(Q.kCGHIDEventTap, event)
            else:
                Q.CGEventPostToPid(os.getpid(), event)

    def click():
        view = browser.webview
        point = (100, 20 if view.isFlipped() else view.bounds().size.height - 20)
        location = view.convertPoint_toView_(point, None)
        for kind in [A.NSEventTypeLeftMouseDown, A.NSEventTypeLeftMouseUp]:
            event = A.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                kind, location, 0, time.monotonic(), browser.window.windowNumber(), None, 0, 1, 1.0)
            app.sendEvent_(event)

    def arm(path, saving):
        sequence, deadline = CocoaPanelSequence(), time.monotonic() + 20
        active_panel = [None]
        def tick(timer):
            try:
                panel = app.modalWindow()
                current = app.keyWindow()
                selected_url = active_panel[0].directoryURL() if saving and active_panel[0] else (
                    active_panel[0].URL() if active_panel[0] else None)
                selected_path = str(selected_url.path()) if selected_url else None
                trace.append([sequence.phase, str(current.className()) if current else None,
                              str(current.firstResponder().className()) if current and current.firstResponder() else None,
                              selected_path])
                if time.monotonic() > deadline:
                    timer.invalidate()
                    observe(f'{saving=}: timeout phase {sequence.phase}', current, screenshot=True)
                    errors.append(f'File panel timed out at phase {sequence.phase}')
                    if panel:
                        panel.cancel_(None)
                    return
                if active_panel[0] is None:
                    if not isinstance(panel, A.NSSavePanel):
                        return
                    active_panel[0] = panel
                    events.append('save' if saving else 'open')
                    if saving:
                        panel.setNameFieldStringValue_(path.name)
                panel_ready = current == active_panel[0]
                location_ready = bool(current and current != active_panel[0] and current != browser.window)
                selected = bool(selected_path and Path(selected_path).resolve() == (path.parent if saving else path).resolve())
                if (os.environ.get('PORTFOLIO_TEST_PANEL_TRACE_BEFORE') == '1'
                        and sequence.phase == 3 and panel_ready and selected):
                    observe(f'{saving=}: before confirmation', current)
                action = sequence.advance(panel_ready=panel_ready, location_ready=location_ready, selected=selected)
                if action == 'open-location':
                    key('', 5, A.NSEventModifierFlagCommand | A.NSEventModifierFlagShift)
                elif action == 'type-path':
                    cocoa_path_input(path.parent if saving else path, key, A, hosted=hosted_input)
                elif action in {'confirm-location', 'confirm-panel'}:
                    key('', 36)
                elif sequence.phase == 4 and panel is None:
                    selected = active_panel[0].URL()
                    trace.append(['selected', str(selected.path()) if selected else None])
                    timer.invalidate()
            except Exception as exc:
                errors.append(f'{type(exc).__name__}: {exc}')
                timer.invalidate()
                if app.modalWindow():
                    app.modalWindow().cancel_(None)
        def start():
            timer = F.NSTimer.timerWithTimeInterval_repeats_block_(.7, True, tick)
            F.NSRunLoop.mainRunLoop().addTimer_forMode_(timer, A.NSModalPanelRunLoopMode)
            F.NSRunLoop.mainRunLoop().addTimer_forMode_(timer, F.NSDefaultRunLoopMode)
            timers.append(timer)
        gui(window, start)

    def stop():
        for timer in timers:
            timer.invalidate()
        if app.modalWindow():
            app.modalWindow().cancel_(None)

    failures = []
    try:
        window.evaluate_js("""(() => {
            const input = document.createElement('input'); input.type = 'file';
            input.id = 'native-probe-input';
            input.style = 'position:fixed;top:0;left:0;width:250px;height:40px;z-index:999999';
            input.onchange = async () => { window.__probeUpload = await input.files[0].text(); };
            document.body.append(input);
        })()""")
        arm(upload, False)
        gui(window, click)
        wait(lambda: window.evaluate_js('window.__probeUpload') == 'Invented native upload', timeout=25)
        assert events == ['open'], (events, errors)
        record('native Cocoa open panel supplies exact uploaded bytes to WebKit')
    except Exception as exc:
        failures.append(f'open: panels={events}, errors={errors}, trace={trace}; {exc}')
    finally:
        gui(window, stop)
        window.evaluate_js("document.querySelector('#native-probe-input')?.remove()")
    events.clear()
    errors.clear()
    trace.clear()
    try:
        window.evaluate_js("""(() => {
            const link = document.createElement('a'); link.id = 'native-probe-download';
            link.href = URL.createObjectURL(new Blob(['Invented native download'], {type:'text/plain'}));
            link.download = 'invented-download.txt'; link.textContent = 'Download synthetic test';
            link.style = 'position:fixed;top:0;left:0;width:250px;height:40px;z-index:999999;background:white';
            document.body.append(link);
        })()""")
        arm(download, True)
        gui(window, click)
        wait(lambda: download.is_file() and download.read_text() == 'Invented native download', timeout=25)
        assert events == ['save'], (events, errors)
        record('native Cocoa save panel writes exact downloaded bytes')
    except Exception as exc:
        failures.append(f'save: panels={events}, errors={errors}, trace={trace}, download={download_events}; {exc}')
    finally:
        BrowserView.DownloadDelegate = original_download_delegate
        gui(window, stop)
        window.evaluate_js("document.querySelector('#native-probe-download')?.remove()")
    if failures:
        raise AssertionError(f'Cocoa file interaction failed: {failures}')
