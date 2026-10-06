"""Bounded, portable portfolio archives and reviewable restoration.

No extraction API interprets archive paths. Only validated ordinary files are
streamed to an operation-owned staging directory. Nothing overwrites a workspace.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import struct
from tempfile import TemporaryDirectory
import unicodedata
import zlib
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

import yaml

from portfolio_app.holdings import DataError
from portfolio_app.settings import app_version
from portfolio_app.workspace import inventory, validate_workspace
from portfolio_app.workspace_lock import workspace_lock

FORMAT = 'portfolio-breakdown-backup'
VERSION = 1
MAX_ARCHIVE = 200 * 1024**2
MAX_EXPANDED = 1024**3
MAX_FILES = 10_000
MAX_MANIFEST = 4 * 1024**2
CHUNK = 1024**2


def safe_path(name: str) -> str:
    if not isinstance(name, str) or not name or len(name.encode('utf-8')) > 1024:
        raise DataError('Invalid archive path.')
    parts = name.split('/')
    if (PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive or '\\' in name
            or any(part in {'', '.', '..'} or len(part.encode('utf-8')) > 255 or
                   part.endswith((' ', '.')) or re.search(r'[\x00-\x1f\x7f<>:"|?*]', part) or
                   PureWindowsPath(part).is_reserved()
                   for part in parts)):
        raise DataError('Unsafe or non-portable archive path.')
    return name


def _unique_paths(names):
    seen, files = {}, set(names)
    for name in names:
        safe_path(name)
        parts = name.split('/')
        for i in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:i])
            key = unicodedata.normalize('NFC', prefix).casefold()
            if key in seen and seen[key] != prefix:
                raise DataError('Archive paths collide on another platform.')
            seen[key] = prefix
            if i < len(parts) and prefix in files:
                raise DataError('Archive contains a file/directory conflict.')


def _json(content):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(content, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON number')))


def validate_portable(directory: Path):
    # Unlike legacy folder copying, a portable reference cannot be absolute even
    # when it happens to resolve inside the source on this machine.
    for path in (directory / 'etfs').glob('*.yaml'):
        try:
            raw = yaml.safe_load(path.read_bytes())
            if isinstance(raw, dict):
                for field in ('holdings_file', 'basket_file'):
                    if field in raw:
                        safe_path(raw[field])
        except (ValueError, TypeError, yaml.YAMLError) as exc:
            raise DataError('Invalid ETF manifest in backup.') from exc
    validate_workspace(directory, allow_empty=True)


def _copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as incoming, destination.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing, CHUNK)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def create_backup(source: Path) -> bytes:
    source = Path(source).expanduser().resolve()
    with TemporaryDirectory(prefix='portfolio-backup-') as temporary:
        staged = Path(temporary) / 'workspace'
        staged.mkdir()
        with workspace_lock(source):
            before = inventory(source)
            _unique_paths(before)
            if len(before) > MAX_FILES or sum((source / p).stat().st_size for p in before) > MAX_EXPANDED:
                raise DataError('Workspace exceeds backup size limits.')
            for name in before:
                _copy_file(source / name, staged / name)
            if inventory(staged) != before or inventory(source) != before:
                raise DataError('Workspace changed during backup. Close external editors and retry.')
            validate_portable(staged)
        manifest = dict(format=FORMAT, version=VERSION, app_version=app_version(),
                        created_at=datetime.now(timezone.utc).isoformat(),
                        files={name: dict(size=(staged / name).stat().st_size, sha256=digest)
                               for name, digest in before.items()})
        encoded = json.dumps(manifest, sort_keys=True).encode()
        if len(encoded) > MAX_MANIFEST:
            raise DataError('Backup manifest exceeds the size limit.')
        # File-backed compression bounds memory even for incompressible workspaces.
        archive = Path(temporary) / 'backup.zip'
        with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as output:
            output.writestr('manifest.json', encoded)
            for name in before:
                output.write(staged / name, 'workspace/' + name)
                if output.fp.tell() > MAX_ARCHIVE:
                    raise DataError('Compressed backup exceeds the 200 MiB limit.')
        if archive.stat().st_size > MAX_ARCHIVE:
            raise DataError('Compressed backup exceeds the 200 MiB limit.')
        return archive.read_bytes()


@dataclass
class RestoreDraft:
    temporary: TemporaryDirectory
    workspace: Path
    manifest: dict
    summary: dict

    def close(self):
        self.temporary.cleanup()

    @property
    def digests(self):
        return {name: record['sha256'] for name, record in self.manifest['files'].items()}


def _manifest(raw):
    if (not isinstance(raw, dict) or raw.get('format') != FORMAT
            or type(raw.get('version')) is not int or raw['version'] != VERSION):
        raise DataError('Unsupported backup format/version. Use a compatible Portfolio Breakdown version.')
    if not isinstance(raw.get('app_version'), str) or not isinstance(raw.get('created_at'), str):
        raise DataError('Invalid backup metadata.')
    stamp = datetime.fromisoformat(raw['created_at'])
    if stamp.tzinfo is None:
        raise DataError('Backup creation time needs a timezone.')
    files = raw.get('files')
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        raise DataError('Invalid backup file inventory.')
    _unique_paths(files)
    total = 0
    for name, record in files.items():
        if name.endswith(('.lock', '.tmp')):
            raise DataError('Backup contains transient files.')
        if (not isinstance(record, dict) or type(record.get('size')) is not int or record['size'] < 0
                or not isinstance(record.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', record['sha256'])):
            raise DataError('Invalid backup checksum/size inventory.')
        total += record['size']
    if total > MAX_EXPANDED:
        raise DataError('Expanded backup exceeds the size limit.')
    return raw


def _check_directory(content: bytes):
    # ZipFile constructs every ZipInfo eagerly. Bound and count the central
    # directory first, so a forged entry count cannot allocate millions of objects.
    end = content.rfind(b'PK\x05\x06', max(0, len(content) - 65557))
    if end < 0 or end + 22 > len(content):
        raise DataError('Invalid or damaged portfolio backup.')
    _, disk, directory_disk, disk_count, count, size, offset, comment = struct.unpack_from('<4s4H2LH', content, end)
    if (disk or directory_disk or disk_count != count or count > MAX_FILES + 1
            or offset + size != end or end + 22 + comment != len(content)):
        raise DataError('Invalid, oversized or unsupported ZIP directory.')
    current, actual = offset, 0
    while current < end:
        if current + 46 > end or content[current:current + 4] != b'PK\x01\x02':
            raise DataError('Invalid ZIP directory entry.')
        name, extra, note = struct.unpack_from('<3H', content, current + 28)
        current += 46 + name + extra + note
        actual += 1
        if actual > MAX_FILES + 1:
            raise DataError('Archive has too many entries.')
    if actual != count or current != end:
        raise DataError('Invalid ZIP directory count.')


def inspect_backup(content: bytes) -> RestoreDraft:
    if len(content) > MAX_ARCHIVE:
        raise DataError('Backup exceeds the 200 MiB upload limit.')
    _check_directory(content)
    temporary = TemporaryDirectory(prefix='portfolio-restore-')
    workspace = Path(temporary.name) / 'workspace'
    workspace.mkdir()
    try:
        with ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            # ZipInfo.filename may already have truncated a NUL or normalized a
            # platform separator. Validate original names before that can hide it.
            names = [member.orig_filename for member in members]
            if len(members) > MAX_FILES + 1 or len(set(names)) != len(names):
                raise DataError('Archive has too many or duplicate entries.')
            _unique_paths(names)
            for member in members:
                kind = stat.S_IFMT(member.external_attr >> 16)
                if (member.is_dir() or kind not in {0, stat.S_IFREG} or member.flag_bits & 1
                        or member.compress_type not in {ZIP_STORED, ZIP_DEFLATED}):
                    raise DataError('Archive must contain unencrypted ordinary files only.')
                if member.file_size > MAX_EXPANDED:
                    raise DataError('Archive entry exceeds the size limit.')
            info = archive.getinfo('manifest.json')
            if info.file_size > MAX_MANIFEST:
                raise DataError('Backup manifest exceeds the size limit.')
            with archive.open(info) as stream:
                encoded = stream.read(MAX_MANIFEST + 1)
            if len(encoded) > MAX_MANIFEST:
                raise DataError('Backup manifest exceeds the size limit.')
            manifest = _manifest(_json(encoded))
            expected = {'manifest.json', *('workspace/' + name for name in manifest['files'])}
            if set(names) != expected:
                raise DataError('Archive contents do not match the manifest.')
            total = 0
            for name, record in manifest['files'].items():
                info = archive.getinfo('workspace/' + name)
                if info.file_size != record['size']:
                    raise DataError('Archive size does not match the manifest.')
                destination = workspace / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest, size = sha256(), 0
                with archive.open(info) as incoming, destination.open('xb') as outgoing:
                    while chunk := incoming.read(CHUNK):
                        size += len(chunk)
                        total += len(chunk)
                        if size > record['size'] or total > MAX_EXPANDED:
                            raise DataError('Expanded backup exceeds the declared size.')
                        digest.update(chunk)
                        outgoing.write(chunk)
                if size != record['size'] or digest.hexdigest() != record['sha256']:
                    raise DataError('Backup checksum verification failed.')
        validate_portable(workspace)
        from portfolio_app.positions import read_snapshot
        from portfolio_app.portfolio_settings import load_settings
        from portfolio_app.allocation import load_allocation
        from portfolio_app.taxonomy import load_classifications
        from portfolio_app.etf import load_funds
        holdings = read_snapshot(workspace / 'holdings.csv').holdings
        allocation = load_allocation(workspace / 'allocation.yaml', holdings)
        summary = dict(positions=len(holdings), categories=len(allocation.buckets) if allocation else 0,
                       classifications=len(load_classifications(workspace / 'classifications.yaml')) if (workspace / 'classifications.yaml').exists() else 0,
                       etf_snapshots=len(load_funds(workspace / 'etfs')),
                       reporting_currency=load_settings(workspace).reporting_currency,
                       files=len(manifest['files']), bytes=total)
        return RestoreDraft(temporary, workspace, manifest, summary)
    except BaseException as exc:
        temporary.cleanup()
        if isinstance(exc, (DataError, KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(exc, (OSError, ValueError, TypeError, KeyError, BadZipFile, RuntimeError, EOFError, RecursionError, zlib.error)):
            raise DataError('Invalid or damaged portfolio backup.') from exc
        raise


def restore_destination(destination: Path, source: Path) -> Path:
    destination = Path(destination).expanduser()
    if not destination.is_absolute():
        raise DataError('Enter the full absolute path of the new workspace folder.')
    # Reject even dangling links and links in ancestors, before resolving.
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
           for p in (destination, *destination.parents)):
        raise DataError('Restore destination must not use links or junctions.')
    destination, source = destination.resolve(), Path(source).resolve()
    if destination.exists() or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise DataError('Destination must be a new directory outside the current workspace.')
    if not destination.parent.is_dir():
        raise DataError('The destination parent folder must already exist.')
    return destination


def commit_restore(draft: RestoreDraft, destination: Path, source: Path) -> Path:
    destination = restore_destination(destination, source)
    from portfolio_app.launcher import workspace_lease
    with workspace_lease(destination):
        return _commit_restore(draft, destination, source)


def _commit_restore(draft: RestoreDraft, destination: Path, source: Path) -> Path:
    destination = restore_destination(destination, source)
    if inventory(draft.workspace) != draft.digests:
        raise DataError('Reviewed backup changed. Upload and review it again.')
    validate_portable(draft.workspace)
    # Reserve exclusively. It is not discoverable as the active portfolio until
    # fully verified; failures remove only the directory this operation created.
    destination.mkdir(mode=0o700)
    try:
        for name in draft.digests:
            _copy_file(draft.workspace / name, destination / name)
        if inventory(destination) != draft.digests:
            raise DataError('Restored workspace verification failed.')
        validate_portable(destination)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination
