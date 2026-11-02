"""Exercise the synthetic preview counter without starting an application."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, local

from portfolio_app import atomic_file, locking
import test_refresh_stability_browser as refresh
from test_futures_freshness_browser import preview_script


def counter_code(root):
    # Execute the actual generated fixture code in separate ScriptRunner globals.
    script = preview_script(root)
    return compile(script[script.index('from portfolio_app.atomic_file import'):
                          script.index('render_app(root,')], '<preview-counter>', 'exec')


def test_counter_serializes_overlapping_reruns_and_readers(tmp_path, monkeypatch):
    counter = tmp_path/'app-renders.txt'
    counter.write_text('1')
    code = counter_code(tmp_path)
    staged = Event()
    release = Event()
    attempts = {'writer': Event(), 'reader': Event()}
    worker = local()
    original_stage = atomic_file.staged_text
    original_lock = locking.write_lock

    @contextmanager
    def gated_stage(path, write):
        with original_stage(path, write) as pending:
            if worker.role == 'first':
                staged.set()
                assert release.wait(5), 'publication gate was not released'
            yield pending

    @contextmanager
    def observed_lock(path, **kwargs):
        if worker.role in attempts:
            attempts[worker.role].set()
        with original_lock(path, **kwargs):
            yield

    monkeypatch.setattr(atomic_file, 'staged_text', gated_stage)
    monkeypatch.setattr(locking, 'write_lock', observed_lock)
    monkeypatch.setattr(refresh, 'write_lock', observed_lock)

    def increment(role):
        worker.role = role
        exec(code, {'root': tmp_path})

    def read():
        worker.role = 'reader'
        return refresh.renders(tmp_path)

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(increment, 'first')
        try:
            assert staged.wait(5), 'first writer did not stage its increment'
            second = pool.submit(increment, 'writer')
            reader = pool.submit(read)
            assert all(event.wait(5) for event in attempts.values())
            assert not second.done()
            assert not reader.done()
            assert counter.read_text() == '1'
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
        assert reader.result(timeout=5) in (2, 3)
    assert counter.read_text() == '3'


def test_counter_keeps_every_concurrent_increment(tmp_path):
    code = counter_code(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [pool.submit(exec, code, {'root': tmp_path}) for _ in range(64)]
        for result in results:
            result.result(timeout=10)
    assert refresh.renders(tmp_path) == 64
