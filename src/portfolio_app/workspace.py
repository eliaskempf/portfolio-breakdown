"""Lossless, explicit workspace copies; never migrate or delete user data on launch."""
from hashlib import sha256
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import yaml

from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock


def validate_workspace(directory: Path) -> None:
    from portfolio_app.positions import read_snapshot
    from portfolio_app.allocation import load_allocation
    from portfolio_app.taxonomy import load_classifications
    from portfolio_app.etf import load_funds
    if not (directory / 'holdings.csv').is_file():
        raise DataError('Workspace must contain holdings.csv.')
    snapshot = read_snapshot(directory / 'holdings.csv')
    load_allocation(directory / 'allocation.yaml', snapshot.holdings)
    if (directory / 'classifications.yaml').exists():
        load_classifications(directory / 'classifications.yaml')
    # External ETF files would leave the copy dependent on the original location.
    for path in (directory / 'etfs').glob('*.yaml'):
        try:
            raw = yaml.safe_load(path.read_text(encoding='utf-8'))
        except (yaml.YAMLError, UnicodeError) as exc:
            raise DataError('Cannot copy a workspace with an invalid ETF manifest.') from exc
        if isinstance(raw, dict):
            for field in ('holdings_file', 'basket_file'):
                if isinstance(raw.get(field), str):
                    target = (path.parent / raw[field]).resolve()
                    if not target.is_relative_to(directory.resolve()):
                        raise DataError('ETF holdings and basket files must be inside the workspace before copying.')
    load_funds(directory / 'etfs')


def inventory(directory: Path) -> dict[str, str]:
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise DataError('Workspace copies do not follow symbolic links. Use ordinary files.')
        if path.is_file() and not path.name.endswith(('.lock', '.tmp')):
            result[path.relative_to(directory).as_posix()] = sha256(path.read_bytes()).hexdigest()
    return result


def copy_workspace(source: Path, destination: Path) -> Path:
    """Copy into a NEW directory, verify bytes and schemas, and leave source intact.

    Used for migration, full backups and restoration. Stop all apps/editors first;
    a running managed app holds the source lease and prevents this operation.
    """
    from portfolio_app.launcher import session_files
    source, destination = source.expanduser().resolve(), destination.expanduser().resolve()
    if not source.is_dir():
        raise DataError('Source workspace does not exist.')
    if destination.exists() or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise DataError('Destination must be a new directory outside the source workspace.')
    lease, _ = session_files(source)
    try:
        with write_lock(lease, blocking=False):
            before = inventory(source)
            validate_workspace(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix='.portfolio-copy-', dir=destination.parent) as temporary:
                staged = Path(temporary) / 'workspace'
                shutil.copytree(source, staged, symlinks=True, ignore=shutil.ignore_patterns('*.lock', '*.tmp'))
                if inventory(staged) != before or inventory(source) != before:
                    raise DataError('Workspace changed during copy. Stop all writers and retry.')
                validate_workspace(staged)
                # mkdir is exclusive: never replace an existing destination.
                destination.mkdir()
                try:
                    for child in staged.iterdir():
                        child.rename(destination / child.name)
                except BaseException:
                    # This destination was created by this operation, not user data.
                    shutil.rmtree(destination)
                    raise
    except (BlockingIOError, PermissionError) as exc:
        raise DataError('Stop this workspace before copying it.') from exc
    return destination
