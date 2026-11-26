"""Exact-version parsing and invalidation using only invented dated observations."""
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from dataclasses import FrozenInstanceError, asdict, dataclass, replace
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
    monkeypatch.setattr(module, '_parsed_stores', OrderedDict())
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


def test_loading_shares_repeated_history_text_without_changing_evidence(tmp_path):
    import portfolio_app.performance.history_observations as module

    rows = [
        dict(id=f'invented-{i}', kind='price', subject=f'invented-asset-{i % 2}', date=day,
             amount=str(i + 10), currency='USD', source='Invented closing feed',
             listing='SYNTH', calendar='XNYS', exchange_timezone='America/New_York')
        for i, day in enumerate(('2024-02-01', '2024-02-01', '2024-02-02', '2024-02-02'))]
    raw = dict(version=1, observations=rows)
    path = tmp_path / module.FILE
    path.write_text(json.dumps(raw))
    loaded, _ = load_observations(tmp_path)
    assert loaded == module.from_dict(raw)
    first, second, third, fourth = loaded.observations
    for field in ('kind', 'currency', 'source', 'listing', 'calendar', 'exchange_timezone'):
        assert getattr(first, field) is getattr(fourth, field)
    assert first.date is second.date and first.subject is third.subject
    assert first.id != third.id and third.amount == D(12)

    rows[0]['listing'] = ['malformed']
    path.write_text(json.dumps(raw))
    with pytest.raises(DataError, match='Invalid listing provenance'):
        load_observations(tmp_path)


def test_revision_cache_releases_file_bytes_and_retains_two_recent_valid_stores(tmp_path, monkeypatch):
    from gc import collect
    from weakref import ref
    import portfolio_app.performance.history_observations as module

    monkeypatch.setattr(module, '_parsed_stores', OrderedDict())
    path = tmp_path / module.FILE
    read, released = Path.read_bytes, []
    class Payload(bytes):
        def __del__(self):
            released.append(True)
    monkeypatch.setattr(Path, 'read_bytes', lambda file: Payload(read(file)))

    def load(amount):
        path.write_text(content(amount))
        return ref(load_observations(tmp_path)[0])

    first, second = load(41), load(42)
    assert load(41)() is first() and first() is not None
    path.write_text('{invalid')
    with pytest.raises(DataError):
        load_observations(tmp_path)
    collect()
    assert len(released) == 4 and second() is not None
    locked_at_eviction = []
    on_eviction = ref(second(), lambda _: locked_at_eviction.append(module._parsed_stores_lock.locked()))
    third = load(43)
    collect()
    assert len(released) == 5
    assert first() is not None and third() is not None and second() is None
    assert on_eviction() is None and locked_at_eviction == [False]


def test_save_reuses_its_independently_validated_bytes_on_reload(tmp_path, monkeypatch):
    from hashlib import sha256
    from unittest.mock import Mock
    import portfolio_app.performance.history_observations as module

    monkeypatch.setattr(module, '_parsed_stores', OrderedDict())
    initial = module.from_dict(json.loads(content(45)))
    correction = replace(initial.observations[0], id='invented-correction', amount=D('46.50'),
                         source='Invented Börsenkurs', replaces=initial.observations[0].id)
    edited = module.add_observation(initial, correction)
    parse = Mock(wraps=module.from_dict)
    monkeypatch.setattr(module, 'from_dict', parse)
    module.save_observations(tmp_path, initial, None)
    first, revision = module.load_observations(tmp_path)
    assert first == initial and first is not initial and parse.call_count == 1
    before = (tmp_path / module.FILE).read_bytes()
    module.save_observations(tmp_path, edited, revision)
    second, changed = module.load_observations(tmp_path)
    assert second == edited and second is not edited and parse.call_count == 2
    assert second.active == (correction,) and changed == sha256((tmp_path / module.FILE).read_bytes()).hexdigest()
    assert next((tmp_path / '.backups').glob('*.json')).read_bytes() == before

    with pytest.raises(DataError, match='Data changed'):
        module.save_observations(tmp_path, replace(edited, reviews=(), observations=()), revision)
    assert parse.call_count == 3 and set(module._parsed_stores) == {revision, changed}
    assert module.load_observations(tmp_path)[0] is second and parse.call_count == 3
    # A direct editor may correct the same file without going through the saver.
    (tmp_path / module.FILE).write_text(content(47))
    assert module.load_observations(tmp_path)[0].observations[0].amount == 47 and parse.call_count == 4


def test_observation_document_matches_saved_bytes_without_deep_copying_scalar_rows(monkeypatch):
    import portfolio_app.performance.history_observations as module

    @dataclass(frozen=True)
    class NestedEvidence:
        entries: tuple

    @dataclass(frozen=True)
    class ExtendedRow(Observation):
        extension: str = 'invented'

    @dataclass(frozen=True)
    class ExtendedStore(ObservationStore):
        extra: tuple = ('invented',)

    row = Observation('invented', 'value', 'scope', '2024-01-02', D('1.2300'), 'EUR',
                      source='Invented "Börse"\nclose')
    object.__setattr__(row, 'display_only', 'excluded')
    nested = replace(row, id='nested', subject='other', basis=NestedEvidence(({'x': D('0.00')},)))
    extra = ExtendedRow(**(asdict(row) | {'id': 'extension', 'subject': 'third'}))
    review = module.CoverageReview('review', module.HistoryScope(('scope',)), row.date, row.date, '0' * 64)
    store = ObservationStore((row, nested, extra), (review,), (('cash', 'income'),))
    assert store.index.active == store.observations and review.scope.key
    copied = []

    def tracked_asdict(value):
        copied.append(value)
        return asdict(value)

    monkeypatch.setattr(module, 'asdict', tracked_asdict)
    for source in (ObservationStore(), store, ExtendedStore(store.observations)):
        expected = json.dumps(asdict(source), default=str, indent=2, allow_nan=False) + '\n'
        assert module._observation_document(source).encode() == expected.encode()
    assert all(not value.observations for value in copied if type(value) is ObservationStore)
    assert all(value is not row for value in copied)
    assert nested in copied and extra in copied


def test_unencodable_nested_evidence_preserves_saved_file_and_backup_state(tmp_path):
    import portfolio_app.performance.history_observations as module

    row = Observation('invented', 'value', 'scope', '2024-01-02', D(1), 'EUR')
    module.save_observations(tmp_path, ObservationStore((row,)), None)
    path = tmp_path / module.FILE
    original = path.read_bytes()
    _, revision = load_observations(tmp_path)
    changed = replace(row, basis={'invented': [float('nan')]})
    with pytest.raises(ValueError, match='Out of range float'):
        module.save_observations(tmp_path, ObservationStore((changed,)), revision)
    assert path.read_bytes() == original
    assert not (tmp_path / '.backups').exists()


def test_delayed_cache_publication_cannot_hide_a_newer_saved_revision(tmp_path, monkeypatch):
    import portfolio_app.performance.history_observations as module

    first = module.from_dict(json.loads(content(61)))
    second = module.from_dict(json.loads(content(62)))
    written, release = Event(), Event()
    write = module.save_document
    def pause_after_first_write(path, text, expected):
        write(path, text, expected)
        if expected is None:
            written.set()
            assert release.wait(10)
    monkeypatch.setattr(module, 'save_document', pause_after_first_write)
    with ThreadPoolExecutor(max_workers=1) as worker:
        saving = worker.submit(module.save_observations, tmp_path, first, None)
        try:
            assert written.wait(10)
            loaded, revision = module.load_observations(tmp_path)
            assert loaded == first
            module.save_observations(tmp_path, second, revision)
            newer, changed = module.load_observations(tmp_path)
        finally:
            release.set()
        saving.result(timeout=10)
    current, actual = module.load_observations(tmp_path)
    assert current is newer and current == second and actual == changed != revision


def test_save_validates_persisted_field_types_before_publication(tmp_path):
    import portfolio_app.performance.history_observations as module

    # A tuple is hashable in memory but serializes to a list. The round-trip
    # validator must reject that invalid boundary before replacing the file.
    malformed = Observation('invented', 'value', 'scope', '2024-01-02', D(1), 'EUR',
                            side='before', boundary=('flow',))
    with pytest.raises(TypeError):
        module.save_observations(tmp_path, ObservationStore((malformed,)), None)
    assert not (tmp_path / module.FILE).exists()


def test_concurrent_readers_share_one_parse_without_blocking_a_corrected_version(tmp_path, monkeypatch):
    import portfolio_app.performance.history_observations as module
    path = tmp_path / module.FILE
    path.write_text(content(21))
    monkeypatch.setattr(module, '_parsed_stores', OrderedDict())
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


def test_validated_active_rows_are_shared_and_rebuilt_for_corrections():
    from portfolio_app.performance.history_observations import active_observations
    first = Observation('first', 'price', 'A', '2024-01-01', 10, 'EUR')
    other = replace(first, id='other', subject='B')
    original = ObservationStore((first, other))
    assert active_observations(original) is original.observations
    corrected = replace(first, id='corrected', replaces=first.id, amount=12)
    current = replace(original, observations=(*original.observations, corrected))
    active = active_observations(current)
    assert active == (other, corrected)
    assert active is active_observations(current) is current.index.active
    assert active_observations(original) == (first, other)
    assert tuple(asdict(current)) == ('observations', 'reviews', 'classifications', 'version')
    with pytest.raises(DataError, match='one active observation'):
        replace(current, observations=(*current.observations, replace(corrected, id='fork')))
    with pytest.raises(DataError, match='competing valuation'):
        replace(current, observations=(*current.observations, replace(first, id='competing')))


def test_closing_index_allocates_candidate_groups_only_for_ambiguous_dates():
    from datetime import date, timedelta
    from portfolio_app.performance.history_observations import lookup
    quotes = tuple(Observation(str(i), 'price', 'A', (date(2024, 1, 1) + timedelta(days=i)).isoformat(),
                               10, 'EUR') for i in range(300))
    alternate = replace(quotes[-1], id='alternate', currency='USD')
    third = replace(quotes[-1], id='third', currency='CHF')
    index = ObservationStore((*quotes, alternate, third)).index
    series = index.closing_prices['A']
    assert all(series[q.date] is q for q in quotes[:-1])
    assert lookup(index, 'price', 'A', quotes[-1].date) is None
    for row in (quotes[-1], alternate, third):
        assert lookup(index, 'price', 'A', row.date, currency=row.currency) is row


def test_exact_and_carried_lookups_share_the_corrected_close_candidates(monkeypatch):
    from portfolio_app.performance.history_observations import lookup
    from portfolio_app.performance import history_observations, history_valuation

    first = Observation('first', 'price', 'A', '2024-01-01', 10, 'EUR',
                        listing='invented', calendar='XNYS', exchange_timezone='America/New_York')
    corrected = replace(first, id='corrected', replaces=first.id, amount=12)
    usd = replace(first, id='usd', currency='USD', amount=14)
    boundary = replace(first, id='before-sale', side='before', boundary='sale')
    store = ObservationStore((first, usd, boundary, corrected))
    key = ('price', 'A', first.date)
    assert lookup(store, *key, currency='EUR') is corrected
    assert 'index' not in vars(store)
    for source in (store, store.index):
        assert lookup(source, *key) is None
        assert lookup(source, *key, currency='USD') is usd
        assert lookup(source, *key, currency='EUR') is corrected
        assert lookup(source, *key, currency='GBP') is None
        assert lookup(source, *key, side='before', boundary='sale') is boundary
        assert lookup(source, *key, side='after', boundary='sale') is None
    monkeypatch.setattr(history_valuation, 'closed_interval', lambda *args: True)
    assert history_valuation.price_at(store, ('A',), '2024-01-02') is None
    single = ObservationStore((first, corrected))
    assert history_valuation.price_at(single, ('A',), '2024-01-02') is corrected
    # Previously accepted files may contain a null close boundary. The canonical
    # exact lookup excludes it, but a carry still requires a single closing row.
    legacy = replace(usd, boundary=None)
    both = ObservationStore((first, corrected, legacy))
    assert lookup(both.index, *key) is corrected
    assert history_valuation.price_at(both, ('A',), '2024-01-02') is None

    def unexpected_scan(source):
        pytest.fail('A point lookup scanned a store that already has a validated index')

    monkeypatch.setattr(history_observations, 'active_observations', unexpected_scan)
    assert lookup(store, *key, currency='USD') is usd


def test_indexed_boundary_lookup_only_inspects_the_requested_flow(monkeypatch):
    from portfolio_app.performance.history_observations import lookup

    rows = tuple(Observation(str(i), 'value', 'scope', '2024-01-01', i, 'EUR',
                             side='before', boundary=f'flow-{i}') for i in range(100))
    index = ObservationStore(rows).index
    checked = []
    original = Observation.__getattribute__

    def observed(row, name):
        if name == 'boundary':
            checked.append(row)
        return original(row, name)

    monkeypatch.setattr(Observation, '__getattribute__', observed)
    for i, row in enumerate(rows):
        assert lookup(index, 'value', 'scope', '2024-01-01', side='before', boundary=f'flow-{i}') is row
    assert len(checked) <= len(rows)


def test_append_reuses_only_unchanged_immutable_persisted_rows(tmp_path, monkeypatch):
    import portfolio_app.performance.history_observations as module

    row = Observation('mark', 'value', 'synthetic', '2024-01-01', D('10.000'), 'EUR')
    mutable = replace(row, id='legacy', date='2024-01-02', basis={'legacy': ['input']})
    module.save_observations(tmp_path, ObservationStore((row, mutable)), None)
    previous, revision = load_observations(tmp_path)
    replacement = replace(row, id='adjusted', amount=D('12.3400'), replaces='mark')
    update = module.add_observation(previous, replacement)
    initialized = []
    original = Observation.__post_init__
    def record(self):
        initialized.append(self.id)
        return original(self)
    monkeypatch.setattr(Observation, '__post_init__', record)
    module.save_observations(tmp_path, update, revision)
    saved, changed = load_observations(tmp_path)
    assert initialized == ['legacy', 'adjusted']
    assert saved == update and changed != revision
    assert saved.observations[0] is previous.observations[0]
    assert saved.observations[1].basis is not previous.observations[1].basis
    assert saved.active[-1] is not replacement
    assert saved.active[-1].amount.as_tuple() == D('12.3400').as_tuple()
    previous.observations[1].basis['legacy'].clear()
    assert saved.observations[1].basis == {'legacy': ['input']}


def test_append_validates_persisted_amounts_under_the_saving_decimal_context(tmp_path):
    from decimal import Inexact, localcontext
    import portfolio_app.performance.history_observations as module

    row = Observation('mark', 'price', 'synthetic', '2024-01-01', D('123.45'), 'EUR')
    module.save_observations(tmp_path, ObservationStore((row,)), None)
    previous, revision = load_observations(tmp_path)
    update = module.add_observation(previous, replace(row, id='adjusted', amount=D(100), replaces='mark'))
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        with pytest.raises(Inexact):
            module.save_observations(tmp_path, update, revision)
    assert load_observations(tmp_path) == (previous, revision)
    assert not (tmp_path / '.backups').exists()


def test_shared_prefix_is_checked_against_encoded_fields_before_writing(tmp_path, monkeypatch):
    import portfolio_app.performance.history_observations as module

    row = Observation('mark', 'price', 'synthetic', '2024-01-01', D(10), 'EUR')
    module.save_observations(tmp_path, ObservationStore((row,)), None)
    previous, revision = load_observations(tmp_path)
    update = module.add_observation(previous, replace(row, id='adjusted', amount=D(12), replaces='mark'))
    document = json.loads(module._observation_document(update))
    document['observations'][0]['basis'] = 'incompatible'
    monkeypatch.setattr(module, '_observation_document', lambda store: json.dumps(document))
    with pytest.raises(DataError, match='units traded'):
        module.save_observations(tmp_path, update, revision)
    assert load_observations(tmp_path) == (previous, revision)
    assert not (tmp_path / '.backups').exists()
