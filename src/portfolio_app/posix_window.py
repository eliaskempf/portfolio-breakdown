"""Experimental Cocoa/Qt presentation; Windows adapter remains independent."""
import logging
import os
import sys
import threading
from tempfile import TemporaryDirectory

from portfolio_app.holdings import DataError
from portfolio_app.window import navigation_action

LOG = logging.getLogger(__name__)


def navigation_allowed(url, origin, *, main_frame=True):
    # Streamlit components use blank/srcdoc child frames. Downloads can use a
    # blob created by our own document, never one belonging to another origin.
    if not main_frame and url in {'about:blank', 'about:srcdoc'}:
        return 'allow'
    if url.startswith('blob:' + origin + '/'):
        return 'allow'
    return navigation_action(url, origin)


def install_navigation(renderer, origin):
    """Install policy before creating the first native view (pywebview 6.2.1)."""
    from portfolio_app.desktop import open_browser
    def allow(url, main_frame=True):
        action = navigation_allowed(url, origin, main_frame=main_frame)
        if action == 'external':
            open_browser(url)
        return action == 'allow'
    if renderer == 'qtwebengine':
        from webview.platforms.qt import BrowserView
        def navigate(page, url, kind, is_main_frame):
            return allow(url.toString(), is_main_frame)
        def popup(page, url, kind, is_main_frame):
            target = url.toString()
            if allow(target):
                page._parent.load_url(target)
            return False
        def download_requested(browser, download):
            from qtpy.QtWidgets import QFileDialog
            try:
                save_qt_download(download, lambda name: QFileDialog.getSaveFileName(
                    browser, browser.localization['global.saveFile'], name)[0])
            except Exception:
                LOG.exception('Could not save the requested download')
                download.cancel()
        BrowserView.on_download_requested = download_requested
        BrowserView.WebPage.acceptNavigationRequest = navigate
        BrowserView.NavigationHandler.acceptNavigationRequest = popup
    elif renderer == 'wkwebview':
        from webview.platforms.cocoa import BrowserView
        from objc import super as objc_super
        class PortfolioNavigationDelegate(BrowserView.BrowserDelegate):
            def webView_decidePolicyForNavigationAction_decisionHandler_(self, view, action, handler):
                target = str(action.request().URL().absoluteString())
                frame = action.targetFrame()
                if allow(target, frame is None or frame.isMainFrame()):
                    objc_super(PortfolioNavigationDelegate, self).webView_decidePolicyForNavigationAction_decisionHandler_(view, action, handler)
                else:
                    handler(0)
            def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(self, view, config, action, features):
                if allow(str(action.request().URL().absoluteString())):
                    view.loadRequest_(action.request())
                return None
        BrowserView.BrowserDelegate = PortfolioNavigationDelegate
    else:
        raise DataError(f'Unsupported desktop renderer: {renderer}')


def save_qt_download(download, choose):
    """Qt 6 split Qt 5's setPath into directory and filename setters."""
    from pathlib import Path
    selected = choose(Path(download.suggestedFileName()).name)
    if not selected:
        download.cancel()
        return
    target = Path(selected)
    download.setDownloadDirectory(str(target.parent))
    download.setDownloadFileName(target.name)
    download.accept()


class PosixWindowPresentation:
    def __init__(self):
        self.window = None
        self.webview = None
        self.focus_requested = threading.Event()
        self.closed = threading.Event()
        self.closing = threading.Event()
        self.failures = []
        self.state = 'starting'

    def prepare(self):
        if sys.platform not in {'linux', 'darwin'}:
            raise DataError('This experimental window supports Linux and macOS only.')
        if sys.platform == 'linux':
            if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
                raise DataError('No desktop display is available. Use --browser or run in a desktop session.')
            os.environ['QT_API'] = 'pyqt6'
        try:
            import webview
            self.webview = webview
        except ImportError as exc:
            raise DataError('Install the window extra, or relaunch with --browser.') from exc

    def child(self, command):
        from portfolio_app.posix_window_process import contained_child
        return contained_child(command)

    def focus(self):
        self.focus_requested.set()

    def run(self, url, stopped, monitor):
        from portfolio_app.settings import APP_NAME, state_path, icon_path
        webview = self.webview
        webview.settings.update(ALLOW_DOWNLOADS=True, ALLOW_FILE_URLS=False,
                                OPEN_EXTERNAL_LINKS_IN_BROWSER=False, REMOTE_DEBUGGING_PORT=None)
        # A direct document preserves Streamlit's native upload/download paths.
        self.window = webview.create_window(APP_NAME, url=url, width=1280, height=900,
                                           min_size=(800, 600), background_color='#11151c',
                                           text_select=True, zoomable=True, js_api=None)
        def initialized(renderer):
            try:
                install_navigation(renderer, url)
            except Exception as exc:
                self.failures.append(exc)
                stopped.set()
                return False
        def closing():
            self.closing.set()
            stopped.set()
        self.window.events.initialized += initialized
        self.window.events.closing += closing
        self.window.events.closed += self.closed.set
        self.window.events.loaded += lambda: setattr(self, 'state', 'document-loaded')
        result = [0]
        finished = threading.Event()
        def ready():
            # Initial navigation may precede the server; reload once it is ready.
            if not stopped.is_set():
                self.window.load_url(url)
        def supervise():
            try:
                result[0] = monitor(ready)
            except Exception as exc:
                LOG.exception('Desktop server supervision failed')
                self.failures.append(exc)
            finally:
                finished.set()
                if not self.closing.is_set() and not self.closed.is_set():
                    self.window.destroy()
        def worker():
            thread = threading.Thread(target=supervise, daemon=True)
            thread.start()
            while not finished.wait(.2) and not self.closed.is_set():
                if self.focus_requested.is_set():
                    self.focus_requested.clear()
                    self.window.restore()
                    self.window.show()
            stopped.set()
            thread.join(timeout=3)
        try:
            profiles = state_path() / 'window'
            profiles.mkdir(parents=True, exist_ok=True, mode=0o700)
            with TemporaryDirectory(prefix='webview-', dir=profiles, ignore_cleanup_errors=True) as cache:
                webview.start(worker, gui='qt' if sys.platform == 'linux' else 'cocoa',
                              private_mode=True, storage_path=cache, debug=False,
                              icon=str(icon_path()))
        except Exception as exc:
            self.failures.append(exc)
        finally:
            stopped.set()
            self.closed.set()
            finished.wait(3)
        if self.failures:
            raise DataError(f'Experimental window failed: {self.failures[0]}. Use --browser.') from self.failures[0]
        return result[0]
