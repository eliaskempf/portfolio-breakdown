"""Owned synthetic browser resources and evidence, using public Playwright APIs.

The pytest owner outlives setup/call/teardown. Sessions capture escaping errors
before Playwright closes; pytest decides whether to retain the staged evidence.
"""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace

OWNER = ContextVar('synthetic_browser_owner', default=None)


class Evidence:
    def __init__(self, nodeid, destination=None):
        self.nodeid = nodeid
        self.destination = Path(destination) / sha256(nodeid.encode()).hexdigest()[:16] if destination else None
        self.temporary = TemporaryDirectory(prefix='portfolio-test-evidence-')
        self.root = Path(self.temporary.name)
        self.pool = None
        self.contexts = []
        self.processes = []
        self.logs = []
        self.failures = []
        self.events = deque(maxlen=200)
        self.captured = set()

    def event(self, kind, **details):
        self.events.append(dict(seconds=round(time.monotonic(), 6), kind=kind, **details))

    def context(self, context, trace=True):
        self.contexts.append((context, trace and bool(self.destination)))
        if trace and self.destination:
            context.tracing.start(screenshots=True, snapshots=True, sources=False)
        def page_created(page):
            rate = float(os.environ.get('PORTFOLIO_TEST_CPU_RATE', '1'))
            if rate != 1:
                context.new_cdp_session(page).send('Emulation.setCPUThrottlingRate', {'rate': rate})
            page.on('pageerror', lambda error: self.event('pageerror', error=str(error)))
            page.on('crash', lambda: self.event('page-crash'))
            def socket_created(socket):
                # Record message kinds/scopes, never portfolio/widget values.
                from streamlit.proto.BackMsg_pb2 import BackMsg
                from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
                def frame(payload, sent):
                    try:
                        msg = (BackMsg if sent else ForwardMsg).FromString(payload)
                        kind = msg.WhichOneof('type')
                        details = {}
                        if kind == 'rerun_script':
                            details = dict(scope=msg.rerun_script.fragment_id, automatic=msg.rerun_script.is_auto_rerun)
                        if kind == 'script_finished':
                            details = dict(status=msg.script_finished)
                        if kind == 'new_session':
                            details = dict(scopes=list(msg.new_session.fragment_ids_this_run),
                                           run=msg.new_session.script_run_id)
                        self.event('sent' if sent else 'received', message=kind, **details)
                    except Exception:
                        pass
                socket.on('framesent', lambda payload: frame(payload, True))
                socket.on('framereceived', lambda payload: frame(payload, False))
            page.on('websocket', socket_created)
        context.on('page', page_created)

    def capture(self):
        try:
            self._capture()
        except Exception as exc:
            self.event("capture-error", error=str(exc))

    def _capture(self):
        if not self.destination:
            return
        self.capture_logs()
        for index, (context, traced) in enumerate(self.contexts):
            if index in self.captured:
                continue
            self.captured.add(index)
            errors = []
            for number, page in enumerate(context.pages):
                if page.is_closed():
                    continue
                for action in (
                    lambda: page.screenshot(path=str(self.root / f'page-{index}-{number}.png'), timeout=3000),
                    lambda: (self.root / f'page-{index}-{number}.html').write_text(page.content(), encoding='utf-8'),
                ):
                    try:
                        action()
                    except Exception as exc:
                        errors.append(str(exc))
            if traced:
                try:
                    context.tracing.stop(path=str(self.root / f'trace-{index}.zip'))
                except Exception as exc:
                    errors.append(str(exc))
            if errors:
                (self.root / f'capture-{index}.txt').write_text('\n'.join(errors), encoding='utf-8')
        (self.root / 'lifecycle.json').write_text(json.dumps(list(self.events), indent=2), encoding='utf-8')

    def capture_logs(self):
        for index, path in enumerate(self.logs):
            try:
                if path.is_file():
                    shutil.copy2(path, self.root / f'supplied-server-{index}.log')
            except OSError as exc:
                self.event('log-capture-error', error=str(exc))

    def close(self):
        for process, handle in self.processes:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
            except Exception as exc:
                self.event('cleanup-error', pid=process.pid, error=str(exc))
            finally:
                if handle:
                    handle.close()
        if self.failures and self.destination:
            self.capture_logs()
            (self.root / 'lifecycle.json').write_text(json.dumps(list(self.events), indent=2), encoding='utf-8')
            self.destination.mkdir(parents=True, exist_ok=True)
            for path in self.root.iterdir():
                if path.is_file():
                    shutil.copy2(path, self.destination / path.name)
            (self.destination / 'failure.json').write_text(json.dumps(dict(
                nodeid=self.nodeid, failures=self.failures,
                reproduce=['uv', 'run', 'pytest', self.nodeid, '-x', '-v'],
                processes=[dict(pid=p.pid, returncode=p.poll()) for p, _ in self.processes]), indent=2), encoding='utf-8')
        self.temporary.cleanup()


class BrowserPool:
    """Reuse processes briefly; bound trace storage retained until browser exit."""
    def __init__(self, max_uses=8):
        self.runner = None
        self.available = {}
        self.uses = {}
        self.max_uses = max_uses

    def borrow(self, options):
        from playwright.sync_api import sync_playwright
        if self.runner is None:
            self.runner = sync_playwright().start()
        key = json.dumps(options, sort_keys=True)
        browser = self.available.pop(key, None)
        if browser is None or not browser.is_connected():
            browser = self.runner.chromium.launch(**options)
        self.uses[browser] = self.uses.get(browser, 0) + 1
        return key, browser

    def release(self, key, browser):
        for context in browser.contexts:
            context.close()
        old = self.available.pop(key, None)
        if old:
            if old.is_connected():
                old.close()
            self.uses.pop(old, None)
        if self.uses.get(browser, 0) >= self.max_uses:
            browser.close()
            self.uses.pop(browser, None)
        if browser.is_connected():
            if len(self.available) >= 2:
                _, oldest = self.available.popitem()
                oldest.close()
                self.uses.pop(oldest, None)
            self.available[key] = browser
        else:
            self.uses.pop(browser, None)

    def close(self):
        for browser in self.available.values():
            if browser.is_connected():
                browser.close()
        if self.runner:
            self.runner.stop()


class Browser:
    def __init__(self, browser, owner, trace, release=None):
        self.raw, self.owner, self.trace = browser, owner, trace
        self.release = release
        self.closed = False

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def new_context(self, **kwargs):
        context = self.raw.new_context(**kwargs)
        self.owner.context(context, self.trace)
        return context

    def new_page(self, **kwargs):
        return self.new_context(**kwargs).new_page()

    def is_connected(self):
        return not self.closed and self.raw.is_connected()

    def close(self, **kwargs):
        if self.closed:
            return
        self.owner.capture()
        self.closed = True
        if self.release:
            self.release()
        else:
            self.raw.close(**kwargs)


@contextmanager
def browser_session(*, trace=True):
    from playwright.sync_api import sync_playwright
    owner = OWNER.get()
    standalone = owner is None
    if standalone:
        owner = Evidence('standalone')
    browsers = []
    @contextmanager
    def engine():
        if owner.pool:
            yield None
        else:
            with sync_playwright() as runner:
                yield runner
    with engine() as runner:
        def launch(**kwargs):
            if runner is None:
                key, raw = owner.pool.borrow(kwargs)
                browser = Browser(raw, owner, trace, release=lambda: owner.pool.release(key, raw))
            else:
                browser = Browser(runner.chromium.launch(**kwargs), owner, trace)
            browsers.append(browser)
            return browser
        try:
            yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        except BaseException:
            owner.capture()
            raise
        finally:
            for browser in browsers:
                if browser.is_connected():
                    browser.close()
            if standalone:
                owner.close()


def start_server(*args, **kwargs):
    """Retain synthetic child logs even if failure occurs before fixture yield."""
    owner = OWNER.get()
    handle = None
    if owner:
        for stream in ('stdout', 'stderr'):
            path = getattr(kwargs.get(stream), 'name', None)
            if isinstance(path, str) and Path(path) not in owner.logs:
                owner.logs.append(Path(path))
        if kwargs.get('stdout') in (None, subprocess.DEVNULL):
            handle = (owner.root / f'server-{len(owner.processes)}.log').open('w', encoding='utf-8')
            kwargs['stdout'] = handle
        if kwargs.get('stderr') in (None, subprocess.DEVNULL):
            kwargs['stderr'] = subprocess.STDOUT
    try:
        process = subprocess.Popen(*args, **kwargs)
    except BaseException:
        if handle:
            handle.close()
        raise
    if owner:
        owner.processes.append((process, handle))
        owner.event('process-start', pid=process.pid)
    return process
