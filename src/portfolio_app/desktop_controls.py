"""Opt-in synthetic native-panel and window-state checks, never normal startup."""
import os
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
        return dict(minimized=native.isMinimized(), active=native.isActiveWindow(), visible=native.isVisible())
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
        present = bool(re.search(rf'View \d+ \(role .*?, PID {os.getpid()},', result.stdout))
        return present == expected
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
            record('repeat-launch focus restores and activates minimized native window')
        window.hide()
        wait(lambda: matches(visible=False), timeout=15)
        assert request_instance(workspace, 'focus')
        wait(lambda: matches(visible=True, active=True), timeout=15)
        record('repeat-launch focus shows and activates hidden native window')
    except TimeoutError as exc:
        raise AssertionError(f'Window state did not converge: {last}') from exc


def cocoa_interactions(window, root, record):
    """Send local native input to WebKit and its real modal file panels."""
    import AppKit as A
    import Foundation as F
    import Quartz as Q
    from webview.platforms.cocoa import BrowserView
    browser = BrowserView.instances[window.uid]
    app = A.NSApplication.sharedApplication()
    timers, events, errors, trace = [], [], [], []
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

    def key(chars, code, modifiers=0):
        # Modern file panels live in another process. System input is allowed
        # only in explicitly opted-in, disposable GitHub-hosted test sessions,
        # and only while our synthetic app owns the foreground window.
        hosted_input = (os.environ.get('PORTFOLIO_TEST_HOSTED_INPUT') == '1'
                        and os.environ.get('GITHUB_ACTIONS') == 'true'
                        and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted')
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
        phase, deadline = [0], [time.monotonic() + 20]
        active_panel = [None]
        def tick(timer):
            try:
                panel = app.modalWindow()
                current = app.keyWindow()
                trace.append([phase[0], str(current.className()) if current else None,
                              str(current.firstResponder().className()) if current and current.firstResponder() else None])
                if time.monotonic() > deadline[0]:
                    timer.invalidate()
                    errors.append(f'File panel timed out at phase {phase[0]}')
                    if panel:
                        panel.cancel_(None)
                    return
                if phase[0] == 0:
                    if not isinstance(panel, A.NSSavePanel):
                        return
                    active_panel[0] = panel
                    events.append('save' if saving else 'open')
                    if saving:
                        panel.setNameFieldStringValue_(path.name)
                    key('', 5, A.NSEventModifierFlagCommand | A.NSEventModifierFlagShift)
                    phase[0] = 1
                elif phase[0] == 1:
                    for char in str(path.parent if saving else path):
                        key(char, 0)
                    phase[0] = 2
                elif phase[0] == 2:
                    key('', 36)
                    phase[0] = 3
                elif phase[0] == 3:
                    key('', 36)
                    phase[0] = 5
                elif phase[0] == 5 and panel is None:
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
