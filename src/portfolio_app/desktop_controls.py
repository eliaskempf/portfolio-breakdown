"""Opt-in synthetic native-panel and window-state checks, never normal startup."""
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


def window_states(window, workspace, record):
    from portfolio_app.launcher import request_instance
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
    try:
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
    from webview.platforms.cocoa import BrowserView
    browser = BrowserView.instances[window.uid]
    app = A.NSApplication.sharedApplication()
    timers, events, errors, trace = [], [], [], []
    upload = root / 'invented-upload.txt'
    upload.write_text('Invented native upload', encoding='utf-8')
    download = root / 'invented-download.txt'

    def key(chars, code, modifiers=0):
        number = app.keyWindow().windowNumber()
        for kind in [A.NSEventTypeKeyDown, A.NSEventTypeKeyUp]:
            event = A.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                kind, (0, 0), modifiers, time.monotonic(), number, None, chars, chars, False, code)
            app.sendEvent_(event)

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
                    events.append('save' if saving else 'open')
                    if saving:
                        panel.setDirectoryURL_(F.NSURL.fileURLWithPath_(str(path.parent)))
                        panel.setNameFieldStringValue_(path.name)
                        phase[0] = 4
                    else:
                        key('G', 5, A.NSEventModifierFlagCommand | A.NSEventModifierFlagShift)
                        phase[0] = 1
                elif phase[0] == 1:
                    for char in str(path):
                        key(char, 0)
                    phase[0] = 2
                elif phase[0] == 2:
                    key('\r', 36)
                    phase[0] = 3
                elif phase[0] == 3:
                    key('\r', 36)
                    phase[0] = 5
                elif phase[0] == 4:
                    panel.ok_(None)
                    phase[0] = 5
                elif phase[0] == 5 and panel is None:
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
        window.evaluate_js("document.querySelector('#native-probe-input').remove()")
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
        assert events == ['open', 'save'], (events, errors)
        record('native Cocoa save panel writes exact downloaded bytes')
        window.evaluate_js("document.querySelector('#native-probe-download').remove()")
    except Exception as exc:
        raise AssertionError(f'Cocoa file interaction failed: panels={events}, errors={errors}, trace={trace}; {exc}') from exc
    finally:
        def stop():
            for timer in timers:
                timer.invalidate()
            if app.modalWindow():
                app.modalWindow().cancel_(None)
        gui(window, stop)
