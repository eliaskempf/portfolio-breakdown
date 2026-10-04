"""Window policy and child-channel tests; all workspaces are invented."""
from io import BytesIO
import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from portfolio_app.holdings import DataError
from portfolio_app.window import WindowPresentation, navigation_action
from portfolio_app.window_process import contained_child, gated_command


@pytest.fixture(autouse=True)
def no_native_dispatch(monkeypatch):
    # Unit tests have no WinForms event loop; native dispatch is exercised in the GUI smoke.
    monkeypatch.setattr(WindowPresentation, '_dispatch', lambda *args: None)


@pytest.mark.parametrize('url,expected', [
    ('http://127.0.0.1:8519/', 'allow'),
    ('http://127.0.0.1:8519/media/example.csv', 'allow'),
    ('https://example.org/help', 'external'),
    ('http://127.0.0.1:8520/', 'external'),
    ('file:///example', 'block'), ('javascript:alert(1)', 'block'),
    ('data:text/html,example', 'block'), ('about:blank', 'block'),
    ('http://' + 'invented:placeholder@127.0.0.1:8519/', 'block'),
    ('http://127.0.0.1:bad/', 'block'),
])
def test_navigation_is_origin_scoped(url, expected):
    assert navigation_action(url, 'http://127.0.0.1:8519') == expected


def test_missing_platform_fails_before_gui_import(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    with pytest.raises(DataError, match='requires Windows'):
        WindowPresentation().prepare()


def test_missing_runtime_has_actionable_browser_fallback(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr('portfolio_app.window.runtime_available', lambda: False)
    with pytest.raises(DataError, match='--browser'):
        WindowPresentation().prepare()


def test_gated_command_preserves_arguments(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    assert gated_command(['python', '-m', 'streamlit', 'run', 'example ü.py']) == [
        'python', '-m', 'portfolio_app.window', '--internal-window-server', 'run', 'example ü.py']
    monkeypatch.setattr(sys, 'frozen', True)
    assert gated_command(['app.exe', '--internal-streamlit', 'run', 'example.py']) == [
        'app.exe', '--internal-window-server', 'run', 'example.py']
    with pytest.raises(DataError):
        gated_command(['app.exe', '--something-else'])


@pytest.mark.parametrize('override', [[], ['--theme.base=light'], ['--theme.base', 'light']])
def test_window_defaults_to_dark_without_overriding_explicit_theme(monkeypatch, override):
    captured = []
    monkeypatch.setattr('portfolio_app.window_process.contained_child', captured.append)
    command = ['python', '-m', 'streamlit', 'run', 'synthetic.py', *override, '--', '--demo']
    presentation = WindowPresentation()
    presentation.child(command)
    assert presentation.color_scheme == ('light' if override else 'dark')
    expected = override or ['--theme.base=dark']
    assert captured == [['python', '-m', 'streamlit', 'run', 'synthetic.py', *expected, '--', '--demo', '--skip-intro']]
    assert presentation.show_intro
    assert command == ['python', '-m', 'streamlit', 'run', 'synthetic.py', *override, '--', '--demo']


def test_skip_intro_skips_both_window_and_streamlit(monkeypatch):
    captured = []
    monkeypatch.setattr('portfolio_app.window_process.contained_child', captured.append)
    presentation = WindowPresentation()
    presentation.child(['python', '-m', 'streamlit', 'run', 'synthetic.py', '--', '--skip-intro'])
    assert not presentation.show_intro
    assert captured[0].count('--skip-intro') == 1


def test_assignment_failure_never_releases_start_gate(monkeypatch):
    events = []
    class Pipe(BytesIO):
        def close(self):
            assert self.getvalue() == b''
            events.append('pipe-closed')
            super().close()
    class FakeJob:
        def assign(self, child):
            raise OSError('Synthetic containment failure')
        def drain(self):
            events.append('drained')
        def close(self):
            events.append('job-closed')
    child = SimpleNamespace(stdin=Pipe(), wait=lambda timeout: events.append('waited'))
    with pytest.raises(OSError, match='containment'):
        with contained_child([sys.executable, '-m', 'streamlit'], job_factory=FakeJob,
                             popen=lambda *a, **k: child):
            pytest.fail('Uncontained server must not start')
    assert events == ['pipe-closed', 'waited', 'drained', 'job-closed']


def test_real_pipe_child_gracefully_stops_and_workspace_is_preserved(tmp_path):
    """Runs the exact gated server on Linux too; only the Job API is substituted."""
    from portfolio_app.launcher import ready, choose_port
    class NoJob:
        def assign(self, child):
            pass
        def drain(self):
            pass
        def close(self):
            pass
    script = tmp_path / 'synthetic_app.py'
    script.write_text('import streamlit as st\nst.write("Synthetic window server")\n', encoding='utf-8')
    preserved = tmp_path / 'invented-holdings.txt'
    preserved.write_text('Synthetic saved content', encoding='utf-8')
    port = choose_port(0)
    command = [sys.executable, '-m', 'streamlit', 'run', str(script),
               '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
               '--browser.gatherUsageStats=false']
    # pytest capture streams do not expose file descriptors; redirect to a temp file.
    with (tmp_path / 'synthetic.log').open('w', encoding='utf-8') as output:
        def spawn(*args, **kwargs):
            kwargs.update(stdout=output, stderr=output, cwd=tmp_path)
            return subprocess.Popen(*args, **kwargs)
        with contained_child(command, job_factory=NoJob, popen=spawn) as child:
            deadline = time.monotonic() + 30
            while not ready(f'http://127.0.0.1:{port}'):
                assert child.poll() is None, (tmp_path / 'synthetic.log').read_text()
                assert time.monotonic() < deadline
                time.sleep(.1)
        assert child.returncode == 0, (tmp_path / 'synthetic.log').read_text()
    assert preserved.read_text() == 'Synthetic saved content'
    assert not ready(f'http://127.0.0.1:{port}')


def test_child_gate_eof_never_imports_streamlit(monkeypatch):
    from portfolio_app.window import server_child
    monkeypatch.setattr('portfolio_app.window.control_input', lambda: BytesIO())
    server_child()


def test_browser_fallback_delegates_without_loading_webview(monkeypatch):
    from portfolio_app.window import main
    calls = []
    monkeypatch.setattr('portfolio_app.window.configure_logging', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['portfolio-window', '--browser', '--offline-demo'])
    monkeypatch.setattr('portfolio_app.app.main', lambda **kwargs: calls.append((kwargs, list(sys.argv))))
    main()
    assert calls == [({}, ['portfolio-window', '--offline-demo'])]


def test_job_platform_guard_and_native_child_cleanup(tmp_path):
    from portfolio_app.window_process import Job
    if sys.platform != 'win32':
        with pytest.raises(DataError, match='requires Windows'):
            Job()
        return  # Platform refusal only; this is not native containment acceptance.
    job = Job()
    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
    try:
        job.assign(child)
        assert child.poll() is None
        job.close()
        child.wait(timeout=5)
        # Kill-on-close can return zero on Windows; termination is the contract.
        assert child.returncode is not None
    finally:
        job.close()
        child.stdin.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


class Event:
    def __init__(self):
        self.handlers = []
    def __iadd__(self, function):
        self.handlers.append(function)
        return self
    def fire(self, *args):
        return any(function(*args) is False for function in self.handlers)


class FakeWebview:
    def __init__(self, renderer='edgechromium'):
        self.renderer = renderer
        self.settings = {}
        self.destroyed = threading.Event()
        self.window = SimpleNamespace(
            events=SimpleNamespace(**{name: Event() for name in ('before_show', 'initialized', 'closing', 'closed', 'loaded')}),
            load_url=self.load_url, destroy=self.destroy,
            evaluate_js=self.evaluate_js)
        self.urls = []
    def evaluate_js(self, js):
        if 'PortfolioSplash?.open(' in js:
            url = json.loads(js.split('open(', 1)[1][:-1])
            if url not in self.urls:
                self.urls.append(url)
            return True
        return 'first-view-rendered'
    def create_window(self, *args, **kwargs):
        assert kwargs['js_api'] is None
        assert kwargs['fullscreen'] is False
        assert kwargs['maximized'] is True
        assert kwargs['background_color'] == '#11151c'
        return self.window
    def load_url(self, url):
        self.urls.append(url)
        self.window.events.loaded.fire()
    def destroy(self):
        self.window.events.closed.fire()
        self.destroyed.set()
    def start(self, worker, **kwargs):
        self.profile = kwargs['storage_path']
        assert kwargs['gui'] == 'edgechromium'
        assert kwargs['private_mode'] and not kwargs['debug']
        if self.window.events.initialized.fire(self.renderer):
            return
        self.window.events.before_show.fire()
        self.window.events.loaded.fire()
        thread = threading.Thread(target=worker)
        thread.start()
        assert self.destroyed.wait(5)
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize('failure', [False, True])
def test_window_monitor_completion_closes_window_and_reports_failures(tmp_path, monkeypatch, failure):
    presentation = WindowPresentation()
    fake = FakeWebview()
    presentation.webview = fake
    monkeypatch.setattr(presentation, '_install_navigation', lambda url: presentation.renderer_ready.set())
    stopped = threading.Event()
    def monitor(on_ready):
        on_ready()
        if failure:
            raise DataError('Invented server crash')
        return 0
    if failure:
        with pytest.raises(DataError, match='--browser'):
            presentation.run('http://127.0.0.1:1', stopped, monitor)
    else:
        assert presentation.run('http://127.0.0.1:1', stopped, monitor) == 0
        assert presentation.state == 'first-view-rendered'
    assert fake.urls == ['http://127.0.0.1:1/?embed_options=dark_theme']
    assert stopped.is_set() and fake.destroyed.is_set()


def test_mshtml_fallback_is_refused(monkeypatch):
    presentation = WindowPresentation()
    presentation.webview = FakeWebview(renderer='mshtml')
    with pytest.raises(DataError, match='application window failed'):
        presentation.run('http://127.0.0.1:1', threading.Event(), lambda callback: pytest.fail('No legacy renderer'))
    assert not presentation.webview.urls


@pytest.mark.parametrize('close_during_intro', [False, True])
def test_app_loads_behind_animation_unless_window_closed(monkeypatch, close_during_intro):
    presentation = WindowPresentation()
    fake = FakeWebview()
    presentation.webview = fake
    monkeypatch.setattr(presentation, '_install_navigation', lambda url: presentation.renderer_ready.set())
    def monitor(on_ready):
        if close_during_intro:
            fake.window.events.closing.fire()
            fake.destroy()
        on_ready()
        return 0
    assert presentation.run('http://127.0.0.1:1', threading.Event(), monitor) == 0
    assert bool(fake.urls) is not close_during_intro



def test_closing_window_stops_monitor(monkeypatch):
    presentation = WindowPresentation()
    fake = FakeWebview()
    presentation.webview = fake
    monkeypatch.setattr(presentation, '_install_navigation', lambda url: presentation.renderer_ready.set())
    stopped = threading.Event()
    def monitor(on_ready):
        on_ready()
        fake.window.events.closing.fire()
        fake.destroy()
        assert stopped.wait(1)
        return 0
    assert presentation.run('http://127.0.0.1:1', stopped, monitor) == 0
    assert fake.destroyed.is_set()


def test_native_close_in_progress_is_not_destroyed_again(monkeypatch):
    presentation = WindowPresentation()
    fake = FakeWebview()
    presentation.webview = fake
    monkeypatch.setattr(presentation, '_install_navigation', lambda url: presentation.renderer_ready.set())
    duplicate_closes = []
    # WinForms has begun closing, but its closed callback has not fired yet.
    fake.window.destroy = lambda: duplicate_closes.append(True)
    def start(worker, **kwargs):
        fake.window.events.before_show.fire()
        fake.window.events.loaded.fire()
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert not duplicate_closes
        fake.destroy()  # The original native close now completes.
    fake.start = start
    def monitor(on_ready):
        on_ready()
        fake.window.events.closing.fire()
        return 0
    assert presentation.run('http://127.0.0.1:1', threading.Event(), monitor) == 0
    assert not duplicate_closes


def test_frozen_browser_relaunch_keeps_dispatch_flag(monkeypatch):
    from portfolio_app.window import main
    calls = []
    monkeypatch.setattr('portfolio_app.window.configure_logging', lambda: None)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'argv', ['portfolio-window.exe', '--browser', '--offline-demo'])
    monkeypatch.setattr('portfolio_app.app.main', lambda **kwargs: calls.append((kwargs, list(sys.argv))))
    main()
    assert calls == [({}, ['portfolio-window.exe', '--offline-demo', '--browser'])]


def test_native_navigation_and_popup_handlers_keep_window_on_app(monkeypatch):
    monkeypatch.setattr(WindowPresentation, '_install_window_controls', lambda self: None)
    monkeypatch.setattr(WindowPresentation, '_install_startup_document', lambda *args: None)
    opened = []
    monkeypatch.setattr('portfolio_app.desktop.open_browser', opened.append)
    class NativeEvent(Event):
        def __isub__(self, function):
            self.handlers.remove(function)
            return self
    prior_popup = lambda *args: pytest.fail('Unrestricted popup handler must be removed')
    popup_event = NativeEvent()
    popup_event += prior_popup
    navigated = []
    messages = []
    prior_message = lambda sender, args: messages.append(args)
    message_event = NativeEvent()
    message_event += prior_message
    core = SimpleNamespace(NewWindowRequested=popup_event, FrameNavigationStarting=NativeEvent(), ExecuteScriptAsync=navigated.append)
    control = SimpleNamespace(CoreWebView2=core, NavigationStarting=NativeEvent(), WebMessageReceived=message_event)
    presentation = WindowPresentation()
    presentation.window = SimpleNamespace(html='<p>Synthetic startup</p>', native=SimpleNamespace(
        browser=SimpleNamespace(on_new_window_request=prior_popup, on_script_notify=prior_message), webview=control))
    presentation._install_navigation('http://127.0.0.1:8519')
    assert presentation.renderer_ready.is_set()
    startup_uri = 'http://127.0.0.1:8519/__portfolio_window__'
    for state, uri, cancelled in [('starting', startup_uri, False),
                                   ('starting', 'data:text/html,unexpected', True),
                                   ('document-loaded', startup_uri, False)]:
        presentation.state = state
        args = SimpleNamespace(Uri=uri, Cancel=False)
        control.NavigationStarting.fire(None, args)
        assert args.Cancel is cancelled
    for uri, cancelled in [('http://127.0.0.1:8519/', False), ('https://example.org/help', True),
                           ('file:///invented', True), ('javascript:void(0)', True)]:
        args = SimpleNamespace(Uri=uri, Cancel=False)
        control.NavigationStarting.fire(None, args)
        assert args.Cancel is cancelled
    for uri in ['https://example.org/provider', 'file:///invented', 'http://127.0.0.1:8519/example']:
        args = SimpleNamespace(Uri=uri, Handled=False)
        popup_event.fire(None, args)
        assert args.Handled
    assert opened == ['https://example.org/help', 'https://example.org/provider']
    assert navigated == ['document.querySelector("#portfolio-app").src = "http://127.0.0.1:8519/example"']
    toggled = []
    monkeypatch.setattr(presentation, '_toggle_fullscreen', lambda: toggled.append(True))
    for source in ['https://example.org', 'http://127.0.0.1:8519/', startup_uri]:
        message_event.fire(None, SimpleNamespace(Source=source, WebMessageAsJson='"portfolio:fullscreen"'))
    assert toggled == [True]
    assert len(messages) == 2


@pytest.mark.parametrize('state,expected', [('minimized', 'normal'), ('maximized', 'maximized')])
def test_focus_restores_minimized_window_without_shrinking_maximized_window(monkeypatch, state, expected):
    states = SimpleNamespace(Minimized='minimized', Normal='normal')
    monkeypatch.setitem(sys.modules, 'System.Windows.Forms', SimpleNamespace(FormWindowState=states))
    calls = []
    native = SimpleNamespace(WindowState=state, Show=lambda: calls.append('show'),
                             Activate=lambda: calls.append('activate'))
    presentation = WindowPresentation()
    presentation.window = SimpleNamespace(native=native)
    presentation._activate()
    assert native.WindowState == expected
    assert calls == ['show', 'activate']


def test_fullscreen_toggle_restores_maximized_frame_and_borderless_exit(monkeypatch):
    monkeypatch.setitem(sys.modules, 'System.Windows.Forms',
                        SimpleNamespace(FormWindowState=SimpleNamespace(Maximized='maximized', Normal='normal'),
                                        Screen=SimpleNamespace(FromControl=lambda native: SimpleNamespace(Bounds='monitor'))))
    native = SimpleNamespace(is_fullscreen=False, WindowState='maximized')
    def toggle():
        native.is_fullscreen = not native.is_fullscreen
        native.WindowState = 'normal'  # Backend restoration must not shrink the window.
    native.toggle_fullscreen = toggle
    presentation = WindowPresentation()
    presentation.window = SimpleNamespace(native=native)
    modes = []
    monkeypatch.setattr(presentation, '_sync_window_controls', lambda: modes.append(native.is_fullscreen))
    presentation._toggle_fullscreen()
    assert native.is_fullscreen and native.Bounds == 'monitor'
    assert native.WindowState == 'normal'
    presentation._toggle_fullscreen()
    assert not native.is_fullscreen
    assert native.WindowState == 'maximized'
    assert modes == [True, False]


def test_windows_use_distinct_temporary_profiles(monkeypatch):
    profiles = []
    for _ in range(2):
        presentation = WindowPresentation()
        fake = FakeWebview()
        presentation.webview = fake
        monkeypatch.setattr(presentation, '_install_navigation', lambda url: presentation.renderer_ready.set())
        presentation.run('http://127.0.0.1:1', threading.Event(), lambda ready: ready() or 0)
        profiles.append(fake.profile)
    assert profiles[0] != profiles[1]


@pytest.mark.parametrize('error_code', [109, 232, 6])
def test_windows_parent_monitor_never_blocks_in_crt_read(monkeypatch, error_code):
    import ctypes
    from portfolio_app.window import wait_for_parent_close
    sleeps = []
    responses = iter([True, True, False])
    def peek(*args):
        return next(responses)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(get_osfhandle=lambda fd: fd))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **kw: SimpleNamespace(PeekNamedPipe=peek), raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: error_code, raising=False)
    monkeypatch.setattr(ctypes, 'WinError', lambda error: OSError(error, 'Synthetic pipe error'), raising=False)
    monkeypatch.setattr('portfolio_app.window.time.sleep', sleeps.append)
    stream = SimpleNamespace(fileno=lambda: 123, read=lambda: pytest.fail('Blocking read would stall native imports'))
    if error_code == 6:
        with pytest.raises(OSError):
            wait_for_parent_close(stream)
    else:
        wait_for_parent_close(stream)
    assert sleeps == [.1, .1]
