"""Complete archives use only deliberately invented temporary portfolios."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from io import BytesIO
import json
from pathlib import Path
import stat
from threading import Event
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
import yaml

from portfolio_app.backup import (create_backup, inspect_backup, commit_restore,
                                  restore_destination, safe_path)
from portfolio_app.demo import create_demo_data
from portfolio_app.holdings import DataError
from portfolio_app.portfolio_settings import save_settings
from portfolio_app.positions import read_snapshot, save_position
from portfolio_app.storage import save_document
from portfolio_app.workspace import inventory
from portfolio_app.workspace_lock import workspace_lock, write_context
from portfolio_app.workspace_selection import Selection, save_selection


def mutate_archive(content, change):
    with ZipFile(BytesIO(content)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    change(files)
    result = BytesIO()
    with ZipFile(result, 'w', ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return result.getvalue()


@pytest.mark.parametrize('currency', ['EUR', 'USD', 'GBP'])
def test_complete_roundtrip(tmp_path, currency):
    from portfolio_app.cost_basis import FIELD, component, encode_components, supplied_conversion
    source = create_demo_data(tmp_path / 'invented ü')
    save_settings(source, currency, None)
    snapshot = read_snapshot(source / 'holdings.csv')
    row = snapshot.holdings.iloc[0].to_dict()
    parts = [component(row['shares'], 12345, 'USD', '2026-09-04')]
    supplied_conversion(parts[0], 'EUR', rate='0.91')
    save_position(source / 'holdings.csv', {FIELD: encode_components(parts, row)},
                  position_id=snapshot.holdings.position_id.iloc[0], expected_revision=snapshot.revision)
    (source / '.cache').mkdir()
    (source / '.cache' / 'invented.json').write_text('{"invented": true}')
    (source / '.cache' / 'incomplete.tmp').write_text('not persisted')
    # Include a signed substitute basket using public-style invented metadata.
    manifest = next((source / 'etfs').glob('*.yaml'))
    raw = yaml.safe_load(manifest.read_text())
    raw['basket_file'] = 'invented-basket.csv'
    (manifest.parent / raw['basket_file']).write_text('constituent_id,name,ticker,isin,weight\ninvented-bond,Invented bond,,,0.8\ninvented-offset,Invented offset,,,-0.1\n')
    manifest.write_text(yaml.safe_dump(raw))
    (source / 'company-identities.yaml').write_text('instrument:invented: invented-company\n')
    (source / 'company-names.yaml').write_text('invented-company: Invented issuer\n')
    (source / 'company-merges.yaml').write_text('disabled: [direct:invented]\n')
    (source / 'fund-fees.json').write_text(json.dumps({'ticker:SYNTH': dict(rate=.002, source='Invented override', verified_on='2026-09-04')}))
    before = inventory(source)
    content = create_backup(source)
    draft = inspect_backup(content)
    try:
        assert draft.summary['reporting_currency'] == currency
        assert draft.summary['positions'] == len(snapshot.holdings)
        target = commit_restore(draft, tmp_path / 'restored é', source)
        assert inventory(target) == before == inventory(source)
        assert not list(target.rglob('*.tmp'))
        assert draft.summary['categories'] > 0 and draft.summary['etf_snapshots'] > 0
    finally:
        draft.close()


def test_empty_workspace_and_cancel(tmp_path):
    source = tmp_path / 'empty'
    source.mkdir()
    draft = inspect_backup(create_backup(source))
    staging = draft.workspace
    assert draft.summary['positions'] == 0
    draft.close()
    assert not staging.exists()
    assert inventory(source) == {}
    assert not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/drive', 'C:relative', '\\\\server\\share',
    'x\\..\\escape', 'a//b', './a', 'a/../b', 'a\x00b', 'a:b', 'CON', 'aux.csv', 'a/LPT1.txt',
    'name.', 'space ', 'folder/NUL.bin', 'x\nname', 'CON .txt', 'folder/aux .csv', 'CONIN$', 'CONOUT$'])
def test_unsafe_paths(name):
    with pytest.raises(DataError):
        safe_path(name)


@pytest.mark.parametrize('case', ['version', 'missing', 'extra', 'checksum', 'schema', 'traversal', 'collision', 'duplicate-json', 'absolute-etf'])
def test_invalid_archives_fail_before_destination(tmp_path, case):
    source = create_demo_data(tmp_path / 'source')
    content = create_backup(source)
    def change(files):
        manifest = json.loads(files['manifest.json'])
        first = next(iter(manifest['files']))
        if case == 'version': manifest['version'] = 99
        if case == 'missing': files.pop('workspace/' + first)
        if case == 'extra': files['extra'] = b'unlisted'
        if case == 'checksum': manifest['files'][first]['sha256'] = '0' * 64
        if case == 'schema':
            from hashlib import sha256
            content = b'invalid holdings'
            files['workspace/holdings.csv'] = content
            manifest['files']['holdings.csv'] = dict(size=len(content), sha256=sha256(content).hexdigest())
        if case == 'traversal': files['../escaped'] = b'unsafe'
        if case == 'collision': files['workspace/HOLDINGS.csv'] = files['workspace/holdings.csv']
        if case == 'absolute-etf':
            from hashlib import sha256
            name = next(n for n in files if n.startswith('workspace/etfs/') and n.endswith('.yaml'))
            raw = yaml.safe_load(files[name]); raw['holdings_file'] = str(source / 'holdings.csv')
            data = yaml.safe_dump(raw).encode(); files[name] = data
            manifest['files'][name.removeprefix('workspace/')] = dict(size=len(data), sha256=sha256(data).hexdigest())
        files['manifest.json'] = json.dumps(manifest).encode()
        if case == 'duplicate-json': files['manifest.json'] = b'{"version":1,"version":2}'
    with pytest.raises(DataError):
        inspect_backup(mutate_archive(content, change))
    assert not (tmp_path / 'escaped').exists()


def test_special_duplicate_truncated_and_size_limits(tmp_path, monkeypatch):
    from portfolio_app import backup
    source = create_demo_data(tmp_path / 'source')
    content = create_backup(source)
    for broken in (b'not zip', content[:40], content[:-30]):
        with pytest.raises(DataError): inspect_backup(broken)
    for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFDIR):
        stream = BytesIO()
        with ZipFile(stream, 'w') as archive:
            info = ZipInfo('link'); info.external_attr = (mode | 0o600) << 16
            archive.writestr(info, b'target')
        with pytest.raises(DataError): inspect_backup(stream.getvalue())
    stream = BytesIO(content)
    with ZipFile(stream, 'a') as archive:
        with pytest.warns(UserWarning): archive.writestr('manifest.json', '{}')
    with pytest.raises(DataError): inspect_backup(stream.getvalue())
    monkeypatch.setattr(backup, 'MAX_EXPANDED', 10)
    with pytest.raises(DataError, match='size limit'): inspect_backup(content)
    monkeypatch.setattr(backup, 'MAX_ARCHIVE', 10)
    with pytest.raises(DataError, match='upload limit'): inspect_backup(content)


def test_refuses_existing_nested_link_and_staging_changes(tmp_path):
    source = create_demo_data(tmp_path / 'source')
    draft = inspect_backup(create_backup(source))
    try:
        for target in (source, source / 'nested', tmp_path):
            with pytest.raises(DataError): commit_restore(draft, target, source)
        (draft.workspace / 'changed.txt').write_text('invented external change')
        with pytest.raises(DataError, match='changed'): commit_restore(draft, tmp_path / 'new', source)
        assert not (tmp_path / 'new').exists()
    finally:
        draft.close()


def test_snapshot_waits_for_whole_currency_transaction(tmp_path, monkeypatch):
    source = create_demo_data(tmp_path / 'source')
    halfway, finish, backup_started = Event(), Event(), Event()
    from portfolio_app import backup
    original = backup.inventory
    def observe(path):
        backup_started.set()
        return original(path)
    monkeypatch.setattr(backup, 'inventory', observe)
    def writer():
        with workspace_lock(source):
            snapshot = read_snapshot(source / 'holdings.csv')
            save_position(source / 'holdings.csv', {'shares': '321'}, position_id=snapshot.holdings.position_id.iloc[0], expected_revision=snapshot.revision)
            halfway.set()
            assert finish.wait(5)
            save_settings(source, 'GBP', None)
    with ThreadPoolExecutor(2) as pool:
        writing = pool.submit(writer)
        assert halfway.wait(5)
        capturing = pool.submit(create_backup, source)
        assert not backup_started.wait(.1)
        finish.set(); writing.result(5)
        draft = inspect_backup(capturing.result(5))
    try:
        assert draft.summary['reporting_currency'] == 'GBP'
        assert read_snapshot(draft.workspace / 'holdings.csv').holdings.shares.iloc[0] == 321
    finally:
        draft.close()


def test_external_change_aborts_snapshot(tmp_path, monkeypatch):
    import portfolio_app.backup as backup
    source = create_demo_data(tmp_path / 'source')
    original = backup._copy_file
    def copying(a, b):
        original(a, b)
        (source / 'external.txt').write_text('invented change')
    monkeypatch.setattr(backup, '_copy_file', copying)
    with pytest.raises(DataError, match='changed during backup'): create_backup(source)


def test_selection_generation_fences_background_writes(tmp_path):
    launch = create_demo_data(tmp_path / 'launch')
    target = create_demo_data(tmp_path / 'restored')
    context = write_context.set((launch, Selection(launch)))
    worker_context = copy_context()
    write_context.reset(context)
    before = inventory(launch)
    save_selection(launch, Selection(target, 1))
    with pytest.raises(DataError, match='active portfolio changed'):
        worker_context.run(save_document, launch / 'portfolio.yaml', 'new', None)
    assert inventory(launch) == before


def _process_writer(directory, ready, finish):
    with workspace_lock(Path(directory)):
        (Path(directory) / 'first.txt').write_text('invented new generation')
        ready.set()
        assert finish.wait(10)
        (Path(directory) / 'second.txt').write_text('invented new generation')


def test_cross_process_barrier(tmp_path):
    import multiprocessing
    source = create_demo_data(tmp_path / 'source')
    for name in ('first.txt', 'second.txt'):
        (source / name).write_text('invented old generation')
    ctx = multiprocessing.get_context('spawn')
    ready, finish = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_process_writer, args=(str(source), ready, finish))
    process.start()
    try:
        assert ready.wait(10)
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(create_backup, source)
            assert not future.done()
            finish.set()
            draft = inspect_backup(future.result(10))
        try:
            assert (draft.workspace / 'first.txt').read_bytes() == (draft.workspace / 'second.txt').read_bytes() == b'invented new generation'
        finally:
            draft.close()
        process.join(10)
        assert process.exitcode == 0
    finally:
        finish.set()
        if process.is_alive(): process.terminate()
        process.join(5)


def test_etf_publication_cannot_be_split_by_snapshot(tmp_path, monkeypatch):
    from portfolio_app.etf import load_funds
    from portfolio_app.etf_sources import publish_snapshot
    source = create_demo_data(tmp_path / 'source')
    fund = load_funds(source / 'etfs')[0]
    prior = fund.manifest_path.read_bytes()
    frame = fund.constituents.copy()
    frame.loc[frame.index[0], 'name'] = 'Invented changed constituent'
    midway, finish = Event(), Event()
    original = Path.replace
    def replacing(path, target):
        result = original(path, target)
        if Path(target).suffix == '.csv' and Path(target).parent == source / 'etfs':
            midway.set()
            assert finish.wait(5)
        return result
    monkeypatch.setattr(Path, 'replace', replacing)
    with ThreadPoolExecutor(2) as pool:
        writer = pool.submit(publish_snapshot, fund, frame, fund.as_of, prior=prior)
        assert midway.wait(5)
        backup = pool.submit(create_backup, source)
        assert not backup.done()
        finish.set(); writer.result(5)
        draft = inspect_backup(backup.result(5))
    try:
        restored = load_funds(draft.workspace / 'etfs')[0]
        assert restored.constituents.name.iloc[0] == 'Invented changed constituent'
    finally:
        draft.close()


@pytest.mark.parametrize('kind', ['document', 'position', 'analytics', 'etf-status', 'price', 'history', 'fee'])
def test_persistent_writers_wait_for_snapshot_barrier(tmp_path, kind):
    source = create_demo_data(tmp_path / 'source')
    started, saved = Event(), Event()
    def writer():
        started.set()
        if kind == 'document':
            save_settings(source, 'GBP', None)
        elif kind == 'position':
            snap = read_snapshot(source / 'holdings.csv')
            save_position(source / 'holdings.csv', {'shares':'42'}, expected_revision=snap.revision, position_id=snap.holdings.position_id.iloc[0])
        elif kind == 'analytics':
            from portfolio_app.analytics_cache import atomic_json
            atomic_json(source / '.cache' / 'analytics' / 'invented.json', {'invented':True})
        elif kind == 'etf-status':
            from portfolio_app.etf_refresh import write_json
            write_json(source / '.cache' / 'etf-refresh' / 'status.json', {'invented':True})
        elif kind == 'fee':
            from portfolio_app.fundamentals import save_fee_override, FundFee
            save_fee_override(source / 'fund-fees.json', 'ticker:SYNTH', FundFee(.002, 'Invented override', '2026-09-04'))
        elif kind == 'price':
            from portfolio_app.prices import PriceService, StaticProvider
            service = PriceService(StaticProvider(source / 'demo_prices.json'), source / '.cache' / 'prices.json')
            service.fx('USD')
        else:
            from portfolio_app.history import HistoryService, DemoHistoryProvider
            HistoryService(DemoHistoryProvider(), source / '.cache' / 'history').get('INVENTED')
        saved.set()
    with ThreadPoolExecutor(1) as pool:
        with workspace_lock(source):
            job = pool.submit(writer)
            assert started.wait(5)
            assert not saved.wait(.1)
        job.result(10)
    assert saved.is_set()


def test_destination_race_and_copy_failure_leave_source_intact(tmp_path, monkeypatch):
    import portfolio_app.backup as backup
    source = create_demo_data(tmp_path / 'source')
    before = inventory(source)
    draft = inspect_backup(create_backup(source))
    try:
        target = tmp_path / 'new'
        restore_destination(target, source)
        target.mkdir(); (target / 'sentinel').write_text('invented competing owner')
        with pytest.raises(DataError): commit_restore(draft, target, source)
        assert (target / 'sentinel').read_text() == 'invented competing owner'
        def failed_copy(*args): raise OSError('Invented disk full')
        monkeypatch.setattr(backup, '_copy_file', failed_copy)
        with pytest.raises(OSError): commit_restore(draft, tmp_path / 'failed', source)
        assert not (tmp_path / 'failed').exists()
        assert inventory(source) == before
    finally:
        draft.close()


def test_queued_market_worker_keeps_its_selection_generation(tmp_path):
    from portfolio_app.market_data import RequestCoordinator
    from portfolio_app.analytics_cache import atomic_json
    launch = create_demo_data(tmp_path / 'launch')
    target = create_demo_data(tmp_path / 'restored')
    ready, finish = Event(), Event()
    coordinator = RequestCoordinator(workers=1)
    context = write_context.set((launch, Selection(launch)))
    def work():
        ready.set()
        assert finish.wait(5)
        atomic_json(launch / '.cache' / 'invented.json', {'invented': True})
    try:
        assert coordinator.request(str(launch), 'invented', work)
    finally:
        write_context.reset(context)
    try:
        assert ready.wait(5)
        save_selection(launch, Selection(target, 1))
    finally:
        finish.set()
        coordinator.close()
    assert not (launch / '.cache' / 'invented.json').exists()
    assert 'active portfolio changed' in coordinator._errors[(str(launch), 'invented')]


@pytest.mark.skipif(__import__('os').name == 'nt', reason='Creating symlinks requires Windows developer mode')
def test_source_symlink_and_destination_symlink_are_rejected(tmp_path):
    source = create_demo_data(tmp_path / 'source')
    link = source / 'linked-file'
    link.symlink_to(source / 'holdings.csv')
    with pytest.raises(DataError, match='symbolic'): create_backup(source)
    link.unlink()
    parent = tmp_path / 'linked-parent'
    parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(DataError, match='links'): restore_destination(parent / 'new', source)


def test_fragment_without_context_cannot_save_retired_workspace(tmp_path):
    from portfolio_app.workspace_lock import bind_workspace
    launch = create_demo_data(tmp_path / 'launch')
    target = create_demo_data(tmp_path / 'restored')
    token = write_context.set(None)
    try:
        bind_workspace(launch, Selection(launch))
        write_context.set(None)  # Streamlit dialog fragment on another script run.
        save_selection(launch, Selection(target, 1))
        with pytest.raises(DataError, match='active portfolio changed'):
            save_settings(launch, 'GBP', None)
    finally:
        write_context.reset(token)


@pytest.mark.parametrize('name', ['company-identities.yaml', 'company-names.yaml', 'company-merges.yaml', 'fund-fees.json'])
def test_invalid_override_content_is_rejected(tmp_path, name):
    from hashlib import sha256
    source = create_demo_data(tmp_path / 'source')
    content = create_backup(source)
    def change(files):
        payload = b'["invented invalid override"]'
        files['workspace/' + name] = payload
        manifest = json.loads(files['manifest.json'])
        manifest['files'][name] = dict(size=len(payload), sha256=sha256(payload).hexdigest())
        files['manifest.json'] = json.dumps(manifest).encode()
    with pytest.raises(DataError): inspect_backup(mutate_archive(content, change))


def test_raw_zip_name_is_checked_before_library_normalization():
    from hashlib import sha256
    from portfolio_app.backup import FORMAT, VERSION
    manifest = dict(format=FORMAT, version=VERSION, app_version='0.1.0',
                    created_at='2026-10-06T12:00:00+00:00',
                    files={'foo': dict(size=8, sha256=sha256(b'invented').hexdigest())})
    stream = BytesIO()
    with ZipFile(stream, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.writestr('workspace/fooXbar', b'invented')
    # Preserve all header lengths while inserting a NUL into BOTH raw names.
    content = stream.getvalue().replace(b'workspace/fooXbar', b'workspace/foo\x00bar')
    with pytest.raises(DataError, match='Unsafe'): inspect_backup(content)
