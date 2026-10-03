"""Local process lifecycle, authenticated instance discovery and shutdown."""
from contextlib import contextmanager, ExitStack
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen
from typing import Callable, ContextManager, Protocol

from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock
from portfolio_app.settings import state_path


class Presentation(Protocol):
    """Optional local presentation; normal browser launches do not import a GUI."""

    def prepare(self) -> None: ...
    def child(self, command: list[str]) -> ContextManager[subprocess.Popen]: ...
    def focus(self) -> None: ...
    def run(self, url: str, stopped: threading.Event,
            monitor: Callable[[Callable[[], None]], int]) -> int: ...


def app_command() -> list[str]:
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable)
        if sys.platform == 'win32' and executable.stem == 'Portfolio Breakdown':
            # Windowed bootloaders discard stdio even when the parent supplies
            # handles. Run the console companion hidden with explicit log handles.
            executable = executable.with_name('portfolio-app.exe')
        return [str(executable)]
    executable = Path(sys.executable)
    if sys.platform == 'win32' and executable.name.lower() == 'pythonw.exe':
        executable = executable.with_name('python.exe')
    return [str(executable), '-m', 'portfolio_app.app']


def session_files(directory: Path) -> tuple[Path, Path]:
    key = sha256(os.path.normcase(str(directory.resolve())).encode()).hexdigest()[:24]
    root = state_path() / 'sessions'
    root.mkdir(parents=True, exist_ok=True)
    return root / f'{key}.lock', root / f'{key}.json'


def request_instance(directory: Path, action: str = 'status') -> dict | None:
    _, record = session_files(directory)
    try:
        info = json.loads(record.read_text(encoding='utf-8'))
        request = Request(f'http://127.0.0.1:{int(info["control_port"])}/{action}',
                          headers={'Authorization': 'Bearer ' + info['token']},
                          method='POST' if action in {'stop', 'focus'} else 'GET')
        with urlopen(request, timeout=2) as response:
            result = json.load(response)
        return result if result.get('instance') == info['token'] else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def stop_instance(directory: Path) -> bool:
    if request_instance(directory, 'stop') is None:
        return False
    lease, _ = session_files(directory)
    for _ in range(200):
        if request_instance(directory) is None:
            try:
                # The control endpoint closes before the child finishes exiting.
                # Recovery and restart are safe only after the lifetime lease ends.
                with write_lock(lease, blocking=False):
                    return True
            except (BlockingIOError, PermissionError):
                pass
        time.sleep(.1)
    raise DataError('The application did not stop in time. Check its launcher window or private log.')


def choose_port(explicit: int | None) -> int:
    for port in ([explicit] if explicit is not None else [8501, 0]):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
                return sock.getsockname()[1]
            except OSError:
                if explicit is not None:
                    raise DataError(f'Port {port} is occupied. Choose another --server.port.') from None
    raise DataError('No available local port.')


def ready(url: str) -> bool:
    try:
        with urlopen(url + '/_stcore/health', timeout=.5) as response:
            return response.status == 200
    except OSError:
        return False


@contextmanager
def instance(directory: Path, url: str, stopped: threading.Event, *, demo: bool, focus: Callable[[], None] | None = None):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.send_error(403)
                return
            actions = {('GET', '/status'), ('POST', '/stop')}
            if focus is not None:
                actions.add(('POST', '/focus'))
            if (self.command, self.path) not in actions:
                self.send_error(404)
                return
            if self.path == '/stop':
                stopped.set()
            if self.path == '/focus':
                focus()
            body = json.dumps(dict(instance=token, url=url, ready=ready(url), demo=demo,
                                   presentation='window' if focus is not None else 'browser')).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = respond
        do_POST = respond

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _, record = session_files(directory)
    temporary = record.with_suffix('.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as handle:
            if os.name != 'nt':
                os.chmod(temporary, 0o600)
            json.dump(dict(control_port=server.server_port, token=token), handle)
        temporary.replace(record)
        yield
    finally:
        record.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def owned_child(command: list[str]):
    """Keep cleanup and waiting inside the workspace lease."""
    options = dict(creationflags=subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else {}
    child = subprocess.Popen(command, **options)
    try:
        yield child
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def monitor_server(child, url: str, stopped: threading.Event, on_ready: Callable[[], None]) -> int:
    deadline = time.monotonic() + 60
    while not stopped.is_set():
        if child.poll() is not None:
            raise DataError(f'Application server exited with code {child.returncode}.')
        if ready(url):
            break
        if time.monotonic() >= deadline:
            raise DataError('Application server did not start within 60 seconds.')
        stopped.wait(.1)
    if stopped.is_set():
        return 0
    print(f'Portfolio Breakdown: {url}', flush=True)
    on_ready()
    while child.poll() is None and not stopped.wait(.2):
        pass
    if not stopped.is_set() and child.returncode:
        raise DataError(f'Application server exited with code {child.returncode}.')
    return child.returncode or 0


def run_server(command: list[str], directory: Path, *, port: int, browser: bool, demo: bool,
               presentation: Presentation | None = None) -> int:
    """Supervise only our own child; never kill a process based on a stale PID."""
    url = f'http://127.0.0.1:{port}'
    stopped = threading.Event()
    previous = signal.signal(signal.SIGTERM, lambda *args: stopped.set())
    try:
        child_context = presentation.child(command) if presentation else owned_child(command)
        with child_context as child:
            with instance(directory, url, stopped, demo=demo,
                          focus=presentation.focus if presentation else None):
                def monitor(on_ready):
                    return monitor_server(child, url, stopped, on_ready)
                if presentation:
                    return presentation.run(url, stopped, monitor)
                def show_browser():
                    if browser:
                        from portfolio_app.desktop import open_browser
                        open_browser(url)
                return monitor(show_browser)
    finally:
        signal.signal(signal.SIGTERM, previous)


def open_existing(directory: Path, *, demo: bool, browser: bool, focus: bool = False) -> bool:
    info = request_instance(directory)
    if not info:
        return False
    if info['demo'] != demo:
        raise DataError('This workspace is already running in another mode. Stop it before changing modes.')
    if focus and info.get('presentation') == 'window':
        if request_instance(directory, 'focus') is None:
            raise DataError('The existing window could not be focused. Retry after it finishes closing.')
    elif browser:
        from portfolio_app.desktop import open_browser
        open_browser(info['url'])
    print(f'Already running: {info["url"]}')
    return True


def start_desktop(arguments: list[str], directory: Path, *, demo: bool, browser: bool) -> None:
    if open_existing(directory, demo=demo, browser=browser):
        return
    log = state_path() / 'launcher.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    options = dict(start_new_session=True) if os.name != 'nt' else dict(creationflags=subprocess.CREATE_NO_WINDOW)
    with log.open('a', encoding='utf-8') as output:
        command = app_command() + [arg for arg in arguments if arg != '--desktop'] + ['--foreground', '--no-browser']
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output, **options)
    deadline = time.monotonic() + 70
    while time.monotonic() < deadline:
        info = request_instance(directory)
        if info and info['ready']:
            if info['demo'] != demo:
                raise DataError('Another launch selected a different workspace mode.')
            if browser:
                from portfolio_app.desktop import open_browser
                open_browser(info['url'])
            print(info['url'])
            return
        if child.poll() is not None:
            raise DataError(f'Application could not start. See {log}')
        time.sleep(.2)
    raise DataError(f'Application startup timed out. See {log}')


@contextmanager
def workspace_lease(directory: Path):
    lease, _ = session_files(directory)
    with ExitStack() as stack:
        try:
            stack.enter_context(write_lock(lease, blocking=False))
        except (BlockingIOError, PermissionError) as exc:
            raise DataError('This workspace is already starting or in use. Retry after it finishes starting.') from exc
        yield


@contextmanager
def presentation_workspace(directory: Path, *, demo: bool, browser: bool, timeout: float = 5):
    """One lease winner; losing window launches discover/focus it, never spawn."""
    deadline = time.monotonic() + timeout
    with ExitStack() as stack:
        while True:
            if open_existing(directory, demo=demo, browser=browser, focus=True):
                yield False
                return
            try:
                stack.enter_context(workspace_lease(directory))
                break
            except DataError:
                if time.monotonic() >= deadline:
                    raise DataError('This workspace is still starting or closing. Retry shortly.') from None
                time.sleep(.1)
        yield True
