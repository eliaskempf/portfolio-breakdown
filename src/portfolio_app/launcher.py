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

from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock
from portfolio_app.settings import state_path


def app_command() -> list[str]:
    return [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'portfolio_app.app']


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
                          method='POST' if action == 'stop' else 'GET')
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
def instance(directory: Path, url: str, stopped: threading.Event, *, demo: bool):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.send_error(403)
                return
            if (self.command, self.path) not in {('GET', '/status'), ('POST', '/stop')}:
                self.send_error(404)
                return
            if self.path == '/stop':
                stopped.set()
            body = json.dumps(dict(instance=token, url=url, ready=ready(url), demo=demo)).encode()
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


def run_server(command: list[str], directory: Path, *, port: int, browser: bool, demo: bool) -> int:
    """Supervise only our own child; never kill a process based on a stale PID."""
    url = f'http://127.0.0.1:{port}'
    stopped = threading.Event()
    previous = signal.signal(signal.SIGTERM, lambda *args: stopped.set())
    child = subprocess.Popen(command)
    try:
        with instance(directory, url, stopped, demo=demo):
            deadline = time.monotonic() + 60
            while not ready(url):
                if child.poll() is not None:
                    raise DataError(f'Application server exited with code {child.returncode}.')
                if stopped.wait(.1):
                    return 0
                if time.monotonic() >= deadline:
                    raise DataError('Application server did not start within 60 seconds.')
            print(f'Portfolio Breakdown: {url}', flush=True)
            if browser:
                from portfolio_app.desktop import open_browser
                open_browser(url)
            while child.poll() is None and not stopped.wait(.2):
                pass
            return child.returncode or 0
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        signal.signal(signal.SIGTERM, previous)


def open_existing(directory: Path, *, demo: bool, browser: bool) -> bool:
    info = request_instance(directory)
    if not info:
        return False
    if info['demo'] != demo:
        raise DataError('This workspace is already running in another mode. Stop it before changing modes.')
    if browser:
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
