"""Exact-version parsing and invalidation using only invented dated observations."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict, replace
from decimal import Decimal as D
import json
import os
from pathlib import Path
from threading import Event

import pytest

from portfolio_app.performance.history_observations import Observation, ObservationStore, load_observations
from portfolio_app.portfolio.holdings import DataError


def content(amount=10):
    store = ObservationStore((Observation('invented-price', 'price', 'SYNTH', '2024-01-01', D(amount), 'EUR'),))
    return json.dumps(asdict(store), default=str)


def test_repeated_loads_parse_once_and_preserve_immutable_evidence(tmp_path, monkeypatch):
    import portfolio_app.performance.history_observations as module
    module._observations_from_bytes.cache_clear()
    path = tmp_path / module.FILE
    path.write_text(content())
    original, calls = module.from_dict, []
    def counted(raw):
        calls.append(True)
        return original(raw)
    monkeypatch.setattr(module, 'from_dict', counted)
    first, revision = load_observations(tmp_path)
    second, again = load_observations(tmp_path)
    assert first == second and revision == again and len(calls) == 1
    with pytest.raises(FrozenInstanceError):
        first.observations[0].amount = D(99)
    edited = replace(first, observations=(replace(first.observations[0], amount=D(99)),))
    assert edited.observations[0].amount == 99
    assert load_observations(tmp_path)[0].observations[0].amount == 10


def test_byte_changes_invalidate_even_with_identical_size_and_mtime(tmp_path):
    path = tmp_path / 'history-observations.json'
    path.write_text(content(10))
    before = path.stat()
    first, revision = load_observations(tmp_path)
    path.write_text(content(20))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    second, changed = load_observations(tmp_path)
    assert path.stat().st_size == before.st_size
    assert first.observations[0].amount == 10 and second.observations[0].amount == 20
    assert changed != revision
    path.write_text('{invalid')
    with pytest.raises(DataError, match='Invalid historical observations'):
        load_observations(tmp_path)
    path.unlink()
    assert load_observations(tmp_path) == (ObservationStore(), None)


def test_concurrent_readers_share_one_parse_without_blocking_a_corrected_version(tmp_path, monkeypatch):
    import portfolio_app.performance.history_observations as module
    path = tmp_path / module.FILE
    path.write_text(content(21))
    module._observations_from_bytes.cache_clear()
    parsing, second_snapshot, release = Event(), Event(), Event()
    parse, read = module.from_dict, Path.read_bytes
    calls = []

    def gated_parse(raw):
        amount = raw['observations'][0]['amount']
        calls.append(amount)
        if amount == '21':
            parsing.set()
            assert release.wait(10)
        return parse(raw)

    def snapshot(file):
        data = read(file)
        if file == path and parsing.is_set():
            second_snapshot.set()
        return data

    monkeypatch.setattr(module, 'from_dict', gated_parse)
    monkeypatch.setattr(Path, 'read_bytes', snapshot)
    with ThreadPoolExecutor(max_workers=3) as workers:
        first = workers.submit(load_observations, tmp_path)
        try:
            assert parsing.wait(10)
            second = workers.submit(load_observations, tmp_path)
            assert second_snapshot.wait(10)
            path.write_text(content(22))
            corrected, new_revision = workers.submit(load_observations, tmp_path).result(timeout=10)
        finally:
            release.set()
        original, revision = first.result()
        other, other_revision = second.result()

    assert calls.count('21') == calls.count('22') == 1
    assert original is other and revision == other_revision != new_revision
    assert original.observations[0].amount == 21 and corrected.observations[0].amount == 22


def test_overlapping_first_use_builds_the_shared_index_once(tmp_path, monkeypatch):
    import portfolio_app.performance.history_observations as module
    (tmp_path / module.FILE).write_text(content(31))
    store, _ = load_observations(tmp_path)
    assert 'index' not in vars(store)
    build = module._ObservationIndex
    entered, second_started, release = Event(), Event(), Event()
    builds = []

    def gated_init(index, source):
        builds.append(source)
        if source is store:
            entered.set()
            assert release.wait(10)
        original_init(index, source)

    def second_read():
        second_started.set()
        return store.index

    original_init = build.__init__
    monkeypatch.setattr(build, '__init__', gated_init)
    other = ObservationStore()
    with ThreadPoolExecutor(max_workers=3) as workers:
        first = workers.submit(lambda: store.index)
        try:
            assert entered.wait(10)
            second = workers.submit(second_read)
            assert second_started.wait(10)
            independent = workers.submit(lambda: other.index).result(timeout=10)
        finally:
            release.set()
        assert first.result() is second.result() is store.index
    assert independent is other.index and independent is not store.index
    assert builds.count(store) == builds.count(other) == 1
    assert store.index.active == store.observations
