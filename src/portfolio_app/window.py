"""Opt-in desktop prototype. Importing this module never imports pywebview."""
import logging
import os
import signal
import sys
import threading
import time
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

LOG = logging.getLogger(__name__)
RUNTIME_URL = 'https://developer.microsoft.com/microsoft-edge/webview2/'
LOADING_HTML = '<!doctype html><title>Portfolio Breakdown</title><p>Starting Portfolio Breakdown…</p>'
# A future supplied splash may use this state change. No portfolio values leave the renderer.
VIEW_PROBE = """(() => {
 const exception = document.querySelector('[data-testid="stException"]');
 if (exception) return 'failed';
 const marker = document.querySelector('[data-portfolio-view-ready="true"]');
 const welcome = [...document.querySelectorAll('h1,h2,h3')].some(
   e => e.textContent === 'Welcome to Portfolio Breakdown');
 const overview = document.querySelector('[role="tab"][aria-selected="true"]');
 const chart = document.querySelector('.js-plotly-plot .main-svg');
 return marker || welcome || (overview && overview.textContent === 'Overview' && chart)
   ? 'first-view-rendered' : 'document-loaded';
})()"""


def navigation_action(url: str, origin: str) -> str:
    """Only the exact Streamlit origin remains in the window."""
    try:
        target, base = urlsplit(url), urlsplit(origin)
        if target.username or target.password:
            return 'block'
        if (target.scheme, target.hostname, target.port) == (base.scheme, base.hostname, base.port):
            return 'allow'
        if target.scheme in {'http', 'https'} and target.hostname:
            return 'external'
    except ValueError:
        pass
    return 'block'


def runtime_available() -> bool:
    import winreg
    suffix = r'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, suffix, 0, winreg.KEY_READ | view) as key:
                    version = str(winreg.QueryValueEx(key, 'pv')[0])
                    if any(int(part) > 0 for part in version.split('.')):
                        return True
            except (OSError, ValueError):
                pass
    return False


class WindowPresentation:
    def __init__(self):
        self.window = None
        self.webview = None
        self.focus_requested = threading.Event()
        self.state = 'starting'
        self.failures = []
        self.closed = threading.Event()
        self.renderer_ready = threading.Event()

    def prepare(self):
        from portfolio_app.holdings import DataError
        if sys.platform != 'win32':
            raise DataError('The experimental window supports Windows 11 x64 only. Use portfolio-app or --browser.')
        if not runtime_available():
            raise DataError(f'WebView2 Runtime is missing. Install Evergreen from {RUNTIME_URL} '
                            'or relaunch with --browser.')
        try:
            import webview
            self.webview = webview
        except Exception as exc:
            raise DataError('Window dependencies could not load. Run uv sync --extra window, '
                            'or relaunch with --browser.') from exc

    def child(self, command):
        from portfolio_app.window_process import contained_child
        return contained_child(command)

    def focus(self):
        self.focus_requested.set()

    def _dispatch(self, callback):
        from System import Action
        if self.window is not None and not self.closed.is_set():
            self.window.native.BeginInvoke(Action(callback))

    def _activate(self):
        from System.Windows.Forms import FormWindowState
        native = self.window.native
        if native.WindowState == FormWindowState.Minimized:
            native.WindowState = FormWindowState.Normal
        native.Show()
        native.Activate()

    def _install_navigation(self, origin):
        """Run on the WinForms thread before navigating to the app."""
        from portfolio_app.desktop import open_browser
        browser = self.window.native.browser
        control = self.window.native.webview
        def navigate(sender, args):
            uri = str(args.Uri)
            # NavigateToString uses about:blank for our startup surface only.
            if uri == 'about:blank' and self.state == 'starting':
                return
            action = navigation_action(uri, origin)
            if action != 'allow':
                args.Cancel = True
                if action == 'external':
                    open_browser(uri)
        def popup(sender, args):
            args.Handled = True
            uri = str(args.Uri)
            action = navigation_action(uri, origin)
            if action == 'external':
                open_browser(uri)
            elif action == 'allow':
                control.CoreWebView2.Navigate(uri)
        def initialized(sender=None, args=None):
            if args is not None and not args.IsSuccess:
                self.failures.append(RuntimeError('WebView2 initialization failed.'))
                self.renderer_ready.set()
                return
            try:
                core = control.CoreWebView2
                # Replace pywebview's unrestricted popup handler; keep downloads intact.
                core.NewWindowRequested -= browser.on_new_window_request
                core.NewWindowRequested += popup
                self.renderer_ready.set()
            except Exception as exc:
                self.failures.append(exc)
                self.renderer_ready.set()
        control.NavigationStarting += navigate
        if control.CoreWebView2 is not None:
            initialized()
        else:
            control.CoreWebView2InitializationCompleted += initialized

    def run(self, url, stopped, monitor):
        from portfolio_app.holdings import DataError
        from portfolio_app.settings import APP_NAME, state_path, icon_path
        webview = self.webview
        webview.settings.update(ALLOW_DOWNLOADS=True, ALLOW_FILE_URLS=False,
                                OPEN_EXTERNAL_LINKS_IN_BROWSER=False, REMOTE_DEBUGGING_PORT=None)
        self.window = webview.create_window(APP_NAME + ' — experimental window', html=LOADING_HTML,
                                           width=1280, height=900, min_size=(800, 600),
                                           text_select=True, zoomable=True, js_api=None)
        def before_show():
            try:
                self._install_navigation(url)
            except Exception as exc:
                self.failures.append(exc)
                self.renderer_ready.set()
        def closing():
            stopped.set()
        def initialized(renderer):
            if renderer != 'edgechromium':
                self.failures.append(RuntimeError('Only the Edge WebView2 renderer is supported.'))
                return False
        self.window.events.initialized += initialized
        self.window.events.before_show += before_show
        self.window.events.closing += closing
        self.window.events.closed += self.closed.set
        result = [0]
        finished = threading.Event()
        def on_ready():
            deadline = time.monotonic() + 30
            while not self.renderer_ready.wait(.1):
                if stopped.is_set():
                    return
                if time.monotonic() >= deadline:
                    raise DataError('The window renderer did not initialize within 30 seconds.')
            if self.failures:
                raise DataError('The window renderer could not initialize.')
            if not stopped.is_set():
                self.state = 'server-reachable'
                self.window.load_url(url)
        def supervise():
            try:
                result[0] = monitor(on_ready)
            except Exception as exc:
                LOG.exception('Window server supervision failed')
                self.failures.append(exc)
            finally:
                finished.set()
                if not self.closed.is_set():
                    self.window.destroy()
        def gui_worker():
            worker = threading.Thread(target=supervise, daemon=True)
            worker.start()
            try:
                while not finished.wait(.2) and not self.closed.is_set():
                    if self.focus_requested.is_set() and self.renderer_ready.is_set():
                        self.focus_requested.clear()
                        self._dispatch(self._activate)
            except Exception as exc:
                LOG.exception('Window focus failed')
                self.failures.append(exc)
                self.window.destroy()
            finally:
                stopped.set()
                worker.join(timeout=3)
        def loaded():
            if self.state != 'starting':
                try:
                    self.state = self.window.evaluate_js(VIEW_PROBE)
                    if self.state == 'failed':
                        LOG.error('Streamlit rendered an application exception; the view remains visible.')
                except Exception:
                    LOG.exception('Could not inspect the first rendered view')
        self.window.events.loaded += loaded
        # Readiness inspection is deliberately DOM-only; Streamlit may finish after loaded.
        def inspect_view():
            while not self.closed.wait(.5) and not finished.is_set():
                if self.state in {'server-reachable', 'document-loaded'}:
                    loaded()
        inspector = threading.Thread(target=inspect_view, daemon=True)
        try:
            inspector.start()
            profiles = state_path() / 'window'
            profiles.mkdir(parents=True, exist_ok=True)
            # Different workspaces may have windows concurrently. Never share a
            # WebView2 profile that another window will remove on close.
            with TemporaryDirectory(prefix='webview-', dir=profiles, ignore_cleanup_errors=True) as cache:
                webview.start(gui_worker, gui='edgechromium', debug=False, private_mode=True,
                              storage_path=cache, icon=str(icon_path('favicon.ico')))
        except Exception as exc:
            LOG.exception('Window renderer failed')
            self.failures.append(exc)
        finally:
            stopped.set()
            self.closed.set()
            finished.wait(3)
        if self.failures:
            raise DataError('The experimental window failed. Relaunch with --browser; see the private window log.') from self.failures[0]
        return result[0]


def control_input():
    if sys.stdin is not None:
        return sys.stdin.buffer
    # A windowed PyInstaller executable can have None Python streams even when
    # Popen supplied a valid inherited standard-input pipe.
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        import msvcrt
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.GetStdHandle.argtypes = [wintypes.DWORD]
        api.GetStdHandle.restype = wintypes.HANDLE
        handle = api.GetStdHandle(-10 & 0xffffffff)
        if handle and handle != ctypes.c_void_p(-1).value:
            return os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), 'rb')
    return None


def server_child():
    """Private pipe gate and graceful-stop channel; never a network API."""
    stream = control_input()
    if stream is None or stream.readline() != b'start\n':
        return
    def wait_for_close():
        stream.read()  # Parent close/crash -> EOF; no token or PID file involved.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            handler = signal.getsignal(signal.SIGTERM)
            if callable(handler) and getattr(handler, '__module__', '') == 'streamlit.web.bootstrap':
                handler(signal.SIGTERM, None)
                return
            time.sleep(.05)
        os._exit(1)  # Startup stuck before Streamlit installed its shutdown handler.
    threading.Thread(target=wait_for_close, daemon=True).start()
    from portfolio_app.app import main as app_main
    sys.argv = [sys.argv[0], '--internal-streamlit', *sys.argv[2:]]
    app_main()


def configure_logging():
    from portfolio_app.settings import state_path
    from logging.handlers import RotatingFileHandler
    directory = state_path() / 'window'
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        directory.chmod(0o700)
    # Separate process logs avoid rollover races with repeat launches and the server.
    log = directory / f'window-{os.getpid()}.log'
    handler = RotatingFileHandler(log, maxBytes=2_000_000, backupCount=1, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    # PyInstaller windowed mode has None streams; preserve actual terminal streams.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, log.open('a', encoding='utf-8', buffering=1))
    return log


def main():
    # Internal frozen dispatch must never recursively create another webview.
    if sys.argv[1:2] == ['--internal-window-server']:
        configure_logging()
        server_child()
        return
    configure_logging()
    from portfolio_app.app import main as app_main
    if '--browser' in sys.argv:
        sys.argv.remove('--browser')
        # Detached frozen relaunch must retain the browser dispatch flag.
        if getattr(sys, 'frozen', False) and '--foreground' not in sys.argv:
            # app_main forwards unknown options to the detached child, where this
            # entrypoint consumes --browser before Streamlit sees it.
            sys.argv.append('--browser')
            app_main()
        else:
            app_main()
        return
    try:
        app_main(presentation=WindowPresentation())
    except Exception:
        LOG.exception('Window startup failed')
        from portfolio_app.desktop import startup_error
        startup_error('The experimental window could not start. Relaunch with --browser. '
                      'Diagnostics are in the application state directory under window.')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
