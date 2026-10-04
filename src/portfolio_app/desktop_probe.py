"""Opt-in native self-test; always creates its own synthetic, offline workspace.

No network control/debugging interface or user-supplied JavaScript is exposed.
The packaged executable runs these fixed checks in the actual embedded renderer.
"""
import json
import os
from pathlib import Path
import sys
import threading
import time
from tempfile import TemporaryDirectory


def wait(check, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.2)
    raise TimeoutError('Native self-test condition did not complete')


def main(output: Path, mode='render'):
    if mode not in {'render', 'early-close'}:
        raise ValueError('Unknown native self-test mode')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'mode': mode, 'platform': sys.platform, 'checks': [], 'status': 'failed',
              'gaps': ['native upload/save dialogs', 'desktop focus policy', 'Gatekeeper (macOS)',
                       'Wayland (Linux)']}
    import webview
    from portfolio_app.window import main as window_main
    from portfolio_app.launcher import request_instance
    from portfolio_app import desktop
    original_open = desktop.open_browser
    external_urls = []
    desktop.open_browser = external_urls.append
    original_create = webview.create_window
    original_argv = sys.argv
    previous_state = os.environ.get('PORTFOLIO_STATE_DIR')
    errors = []
    worker_done = threading.Event()
    def record(name):
        report['checks'].append(name)
    with TemporaryDirectory(prefix='portfolio-native-synthetic-') as temporary:
        root = Path(temporary)
        workspace = root / 'workspace'
        workspace.mkdir()
        marker = workspace / 'synthetic-preserved.txt'
        marker.write_text('Invented preservation sentinel', encoding='utf-8')
        os.environ['PORTFOLIO_STATE_DIR'] = str(root / 'state')
        sys.argv = [original_argv[0], '--data-dir', str(workspace), '--offline-demo', '--demo', '--skip-intro']
        def inspect(window):
            try:
                record('native window shown')
                if mode == 'early-close':
                    return
                wait(lambda: window.evaluate_js("Boolean(document.querySelector('.js-plotly-plot'))"))
                record('default overview chart rendered in native webview')
                for tab in ['Exposure', 'Positions', 'Rebalance', 'Overview']:
                    click = f"""(() => {{ const tab = [...document.querySelectorAll('[role=tab]')]
                        .find(el => el.textContent.trim() === {json.dumps(tab)});
                        if (!tab) return false; tab.click(); return true; }})()"""
                    assert window.evaluate_js(click), f'Missing tab: {tab}'
                    time.sleep(1)
                    wait(lambda: window.evaluate_js("document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'"))
                    assert not window.evaluate_js("Boolean(document.querySelector('[data-testid=stException]'))")
                    record(f'{tab} navigation without application exceptions')
                if sys.platform == 'linux':
                    qt_interactions(window, root, output, record)
                    report['gaps'].remove('native upload/save dialogs')
                elif sys.platform == 'darwin':
                    cocoa_snapshot(window, output / 'native-overview.png')
                    record('native WebKit snapshot captured')
                # Native top-level navigation must invoke the system-browser
                # adapter and leave the app loaded. Do not open a real website.
                window.evaluate_js("location.href = 'https://example.invalid/synthetic-external'")
                wait(lambda: external_urls == ['https://example.invalid/synthetic-external'])
                record('external navigation routed to system-browser adapter')
                status = request_instance(workspace)
                assert status and status['ready'] and status['presentation'] == 'window'
                assert request_instance(workspace, 'focus')
                record('authenticated repeated-launch focus request accepted')
                # Demonstrate that the actual native renderer executes policy;
                # a file navigation must be refused and retain the app document.
                original_url = window.get_current_url()
                window.evaluate_js("location.href = 'file:///synthetic-forbidden.html'")
                time.sleep(.5)
                assert window.get_current_url() == original_url
                record('native file navigation blocked')
                report['status'] = 'passed'
            except Exception as exc:
                errors.append(f'{type(exc).__name__}: {exc}')
            finally:
                window.destroy()
                worker_done.set()
        def create(*args, **kwargs):
            window = original_create(*args, **kwargs)
            if sys.platform == 'linux':
                window.events.before_show += lambda: qt_dispatch(window)
            window.events.shown += lambda: threading.Thread(target=inspect, args=(window,), daemon=True).start()
            return window
        webview.create_window = create
        try:
            try:
                window_main()
            except SystemExit as exc:
                if exc.code not in {None, 0}:
                    errors.append(f'Application exit: {exc.code}')
            assert worker_done.wait(5), 'Native inspection did not finish'
            assert request_instance(workspace) is None, 'Managed server remained after window close'
            assert marker.read_text(encoding='utf-8') == 'Invented preservation sentinel'
            record('window close stops managed server and preserves workspace')
            if not errors:
                report['status'] = 'passed'
        except Exception as exc:
            errors.append(f'{type(exc).__name__}: {exc}')
        finally:
            webview.create_window = original_create
            desktop.open_browser = original_open
            sys.argv = original_argv
            if previous_state is None:
                os.environ.pop('PORTFOLIO_STATE_DIR', None)
            else:
                os.environ['PORTFOLIO_STATE_DIR'] = previous_state
            report['errors'] = errors
            (output / 'native-result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if errors:
        raise SystemExit(1)


def qt_dispatch(window):
    """Construct the test dispatcher on the GUI thread before the window shows."""
    from qtpy.QtCore import QObject, Signal, Slot
    class Dispatcher(QObject):
        call = Signal(object)
        def __init__(self):
            super().__init__(window.native)
            self.call.connect(self.invoke)
        @Slot(object)
        def invoke(self, callback):
            callback()
    window._probe_dispatcher = Dispatcher()


def qt_interactions(window, root, output, record):
    """Click real web inputs and operate actual Qt dialogs on the GUI thread."""
    from qtpy.QtCore import QTimer, QPoint, Qt, QMetaObject
    from qtpy.QtWidgets import QApplication, QFileDialog, QLineEdit
    from PyQt6.QtTest import QTest
    def gui(callback):
        finished = threading.Event()
        errors = []
        def invoke():
            try:
                callback()
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()
        window._probe_dispatcher.call.emit(invoke)
        assert finished.wait(15), 'GUI operation timed out'
        if errors:
            raise errors[0]
    gui(lambda: window.native.grab().save(str(output / 'native-overview.png')))
    expected = root / 'invented-upload.txt'
    expected.write_text('Invented native upload')
    chosen = []
    timer = [None]
    def arm(path):
        def start():
            timer[0] = QTimer(window.native)
            def choose():
                dialog = QApplication.activeModalWidget()
                if isinstance(dialog, QFileDialog):
                    name = dialog.findChild(QLineEdit, 'fileNameEdit')
                    if name is not None:
                        name.setText(str(path))
                        chosen.append(str(path))
                        timer[0].stop()
                        QMetaObject.invokeMethod(dialog, 'accept', Qt.ConnectionType.QueuedConnection)
            timer[0].timeout.connect(choose)
            timer[0].start(100)
        gui(start)
    def click():
        gui(lambda: QTest.mouseClick(window.native.webview.focusProxy(), Qt.MouseButton.LeftButton,
                                     pos=QPoint(100, 20)))
    try:
        window.evaluate_js("""(() => {
            const input = document.createElement('input'); input.type = 'file';
            input.id = 'native-probe-input';
            input.style = 'position:fixed;top:0;left:0;width:250px;height:40px;z-index:999999';
            input.onchange = async () => { window.__probeUpload = await input.files[0].text(); };
            document.body.append(input);
        })()""")
        arm(expected)
        click()
        wait(lambda: window.evaluate_js('window.__probeUpload') == 'Invented native upload', timeout=20)
        assert chosen == [str(expected)]
        record('native file picker supplies uploaded bytes to web input')
        window.evaluate_js("document.querySelector('#native-probe-input').remove()")
        download = root / 'invented-download.txt'
        window.evaluate_js("""(() => {
            const link = document.createElement('a'); link.id = 'native-probe-download';
            link.href = URL.createObjectURL(new Blob(['Invented native download'], {type:'text/plain'}));
            link.download = 'invented-download.txt'; link.textContent = 'Download synthetic test';
            link.style = 'position:fixed;top:0;left:0;width:250px;height:40px;z-index:999999;background:white';
            document.body.append(link);
        })()""")
        arm(download)
        click()
        wait(lambda: download.is_file() and download.read_text() == 'Invented native download', timeout=20)
        record('native save dialog writes exact downloaded bytes')
        window.evaluate_js("document.querySelector('#native-probe-download').remove()")
    finally:
        if timer[0] is not None:
            gui(timer[0].stop)


def cocoa_snapshot(window, destination):
    """Snapshot our synthetic WKWebView, without recording the user's desktop."""
    from AppKit import NSBitmapImageRep, NSBitmapImageFileTypePNG
    from PyObjCTools import AppHelper
    from webview.platforms.cocoa import BrowserView
    completed = threading.Event()
    errors = []
    def received(image, error):
        try:
            if error is not None or image is None:
                raise RuntimeError('WebKit did not produce a native snapshot')
            bitmap = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
            png = bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
            if not png.writeToFile_atomically_(str(destination), True):
                raise OSError('Could not save synthetic native snapshot')
        except Exception as exc:
            errors.append(exc)
        finally:
            completed.set()
    def capture():
        try:
            BrowserView.instances[window.uid].webview.takeSnapshotWithConfiguration_completionHandler_(None, received)
        except Exception as exc:
            errors.append(exc)
            completed.set()
    AppHelper.callAfter(capture)
    assert completed.wait(30), 'Native WebKit snapshot timed out'
    if errors:
        raise errors[0]
