"""Exact-version parsing and invalidation using only invented dated observations."""
from dataclasses import FrozenInstanceError, asdict, replace
from decimal import Decimal as D
import json
import os

import pytest

from portfolio_app.history_observations import Observation, ObservationStore, load_observations
from portfolio_app.holdings import DataError


def content(amount=10):
    store = ObservationStore((Observation('invented-price', 'price', 'SYNTH', '2024-01-01', D(amount), 'EUR'),))
    return json.dumps(asdict(store), default=str)


def test_repeated_loads_parse_once_and_preserve_immutable_evidence(tmp_path, monkeypatch):
    import portfolio_app.history_observations as module
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
