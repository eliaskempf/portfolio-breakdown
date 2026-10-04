"""Windows desktop presentation. Importing this module never imports pywebview."""
import json
import logging
import os
import signal
import sys
import threading
import time
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from portfolio_app.window_splash import startup_html

LOG = logging.getLogger(__name__)
RUNTIME_URL = 'https://developer.microsoft.com/microsoft-edge/webview2/'


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
        self.color_scheme = 'dark'
        self.show_intro = True
        self.startup_loaded = threading.Event()
        self.server_started = threading.Event()

    def prepare(self):
        from portfolio_app.holdings import DataError
        if sys.platform != 'win32':
            raise DataError('The desktop window requires Windows. Use portfolio-app or --browser.')
        if not runtime_available():
            raise DataError(f'WebView2 Runtime is missing. Install Evergreen from {RUNTIME_URL} '
                            'or relaunch with --browser.')
        try:
            import webview
            self.webview = webview
        except Exception as exc:
            raise DataError('Window dependencies could not load. Reinstall the application or run uv sync --locked, '
                            'or relaunch with --browser.') from exc

    def child(self, command):
        from portfolio_app.window_process import contained_child
        # Default only this presentation to dark; explicit Streamlit overrides win.
        separator = command.index('--') if '--' in command else len(command)
        options = command[:separator]
        for index, arg in enumerate(options):
            if arg == '--theme.base' and index + 1 < len(options):
                self.color_scheme = options[index + 1]
            elif arg.startswith('--theme.base='):
                self.color_scheme = arg.split('=', 1)[1]
        if not any(arg == '--theme.base' or arg.startswith('--theme.base=') for arg in command[:separator]):
            command = [*command[:separator], '--theme.base=dark', *command[separator:]]
        self.show_intro = '--skip-intro' not in command
        if self.show_intro:
            # The window plays the same asset immediately; do not replay it in Streamlit.
            command = [*command, *([] if '--' in command else ['--']), '--skip-intro']
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

    def _toggle_fullscreen(self):
        """Called on the GUI thread; returning to windowed mode stays maximized."""
        from System.Windows.Forms import FormWindowState, Screen
        native = self.window.native
        screen = Screen.FromControl(native)
        native.toggle_fullscreen()
        if native.is_fullscreen:
            # A maximized borderless Form can retain the resize-frame overscan.
            # Normal state with exact monitor bounds has no non-client inset.
            native.WindowState = FormWindowState.Normal
            native.Bounds = screen.Bounds
        else:
            native.WindowState = FormWindowState.Maximized
        self._sync_window_controls()

    def _sync_window_controls(self):
        native = self.window.native
        if native.webview.CoreWebView2 is not None:
            native.webview.CoreWebView2.ExecuteScriptAsync(
                f'window.PortfolioSplash?.setFullscreen({json.dumps(bool(native.is_fullscreen))})')

    def _install_window_controls(self):
        from System.Drawing import Color
        from System.Windows.Forms import Keys, Padding
        native = self.window.native
        native.Padding = Padding(0)
        native.BackColor = Color.FromArgb(17, 21, 28)
        native.webview.Margin = Padding(0)
        native.KeyPreview = True
        pressed = False
        def key_down(sender, args):
            nonlocal pressed
            if args.KeyCode == Keys.F11:
                args.Handled = True
                if not pressed:
                    pressed = True
                    self._toggle_fullscreen()
        def key_up(sender, args):
            nonlocal pressed
            if args.KeyCode == Keys.F11:
                pressed = False
        native.KeyDown += key_down
        native.KeyUp += key_up
        # WebView2 raises accelerator events on its control, bypassing Form.KeyPreview.
        native.webview.KeyDown += key_down
        native.webview.KeyUp += key_up

    def _install_navigation(self, origin):
        """Run on the WinForms thread before navigating to the app."""
        from portfolio_app.desktop import open_browser
        browser = self.window.native.browser
        control = self.window.native.webview
        self._install_window_controls()
        def navigate(sender, args):
            uri = str(args.Uri)
            if uri == 'about:blank':
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
                control.CoreWebView2.ExecuteScriptAsync(
                    f'document.querySelector("#portfolio-app").src = {json.dumps(uri)}')
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
                core.FrameNavigationStarting += navigate
                self._install_startup_document(core, origin)
                def message(sender, args):
                    payload = json.loads(str(args.WebMessageAsJson))
                    if str(args.Source) == origin + '/__portfolio_window__' and isinstance(payload, str):
                        if payload == 'portfolio:fullscreen':
                            self._toggle_fullscreen()
                            return
                        if payload == 'portfolio:exit':
                            self.window.native.Close()
                            return
                    browser.on_script_notify(sender, args)
                control.WebMessageReceived -= browser.on_script_notify
                control.WebMessageReceived += message
                self.renderer_ready.set()
            except Exception as exc:
                self.failures.append(exc)
                self.renderer_ready.set()
        control.NavigationStarting += navigate
        if control.CoreWebView2 is not None:
            initialized()
        else:
            control.CoreWebView2InitializationCompleted += initialized

    def _install_startup_document(self, core, origin):
        """Serve only the bundled shell inside WebView2, even before the server starts."""
        from System.IO import MemoryStream
        from System.Text import Encoding
        from Microsoft.Web.WebView2.Core import (CoreWebView2WebResourceContext,
                                                CoreWebView2WebResourceRequestSourceKinds)
        startup_url = origin + '/__portfolio_window__'
        html = startup_html(show_intro=self.show_intro)
        def serve(sender, args):
            if str(args.Request.Uri) == startup_url:
                stream = MemoryStream(Encoding.UTF8.GetBytes(html))
                args.Response = core.Environment.CreateWebResourceResponse(
                    stream, 200, 'OK', 'Content-Type: text/html; charset=utf-8\r\nCache-Control: no-store')
        core.AddWebResourceRequestedFilter(startup_url, CoreWebView2WebResourceContext.Document,
                                          CoreWebView2WebResourceRequestSourceKinds.Document)
        core.WebResourceRequested += serve

    def run(self, url, stopped, monitor):
        from portfolio_app.holdings import DataError
        from portfolio_app.settings import APP_NAME, state_path, icon_path
        webview = self.webview
        webview.settings.update(ALLOW_DOWNLOADS=True, ALLOW_FILE_URLS=False,
                                OPEN_EXTERNAL_LINKS_IN_BROWSER=False, REMOTE_DEBUGGING_PORT=None)
        self.app_url = f'{url}/?embed_options={self.color_scheme}_theme'
        self.window = webview.create_window(APP_NAME,
                                           url=url + '/__portfolio_window__',
                                           width=1280, height=900, min_size=(800, 600),
                                           fullscreen=False, maximized=True, background_color='#11151c',
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
            while not self.startup_loaded.wait(.1):
                if stopped.is_set():
                    return
                if time.monotonic() >= deadline:
                    raise DataError('The startup view did not load within 30 seconds.')
            if not stopped.is_set():
                self.state = 'server-reachable'
                self.server_started.set()
                loaded()
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
            self.startup_loaded.set()
            self._dispatch(self._sync_window_controls)
            if not self.server_started.is_set():
                return
            try:
                # Idempotent open also restores the frame after a window reload.
                self.window.evaluate_js(f'window.PortfolioSplash?.open({json.dumps(self.app_url)})')
                self.state = self.window.evaluate_js('window.PortfolioSplash?.state') or 'document-loaded'
                if self.state == 'failed':
                    LOG.error('Streamlit rendered an application exception; the view remains visible.')
            except Exception:
                LOG.exception('Could not inspect the first rendered view')
        self.window.events.loaded += loaded
        def inspect_view():
            while not self.closed.wait(.2) and not finished.is_set():
                if self.server_started.is_set() and self.state not in {'first-view-rendered', 'failed'}:
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
            reason = str(self.failures[0]) or type(self.failures[0]).__name__
            raise DataError(f'The application window failed: {reason} '
                            'Relaunch with --browser; see the private window log.') from self.failures[0]
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


def wait_for_parent_close(stream):
    """Watch EOF without a blocking Windows CRT read during native DLL imports."""
    if sys.platform != 'win32':
        stream.read()
        return
    import ctypes
    from ctypes import wintypes
    import msvcrt
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    api.PeekNamedPipe.restype = wintypes.BOOL
    pipe = msvcrt.get_osfhandle(stream.fileno())
    while api.PeekNamedPipe(pipe, None, 0, None, None, None):
        time.sleep(.1)
    error = ctypes.get_last_error()
    if error not in {109, 232}:  # ERROR_BROKEN_PIPE / ERROR_NO_DATA: writer closed.
        raise ctypes.WinError(error)


def server_child():
    """Private pipe gate and graceful-stop channel; never a network API."""
    stream = control_input()
    if stream is None or stream.readline() != b'start\n':
        return
    def wait_for_close():
        try:
            wait_for_parent_close(stream)
        except OSError:
            # A broken monitor cannot safely leave the server running unattended.
            LOG.exception('Could not monitor the parent shutdown pipe')
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
        startup_error('The application window could not start. Relaunch with --browser. '
                      'Diagnostics are in the application state directory under window.')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
