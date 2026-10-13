"""Lossless, explicit workspace copies; never migrate or delete user data on launch."""
from hashlib import sha256
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import yaml

from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock


def validate_workspace(directory: Path, *, allow_empty: bool = False) -> None:
    from portfolio_app.positions import read_snapshot
    from portfolio_app.allocation import load_allocation
    from portfolio_app.taxonomy import load_classifications
    from portfolio_app.etf import load_funds
    if not allow_empty and not (directory / 'holdings.csv').is_file():
        raise DataError('Workspace must contain holdings.csv.')
    from portfolio_app.portfolio_settings import load_settings
    load_settings(directory)
    from portfolio_app.monitoring_store import load_monitoring
    load_monitoring(directory)
    snapshot = read_snapshot(directory / 'holdings.csv')
    allocation = load_allocation(directory / 'allocation.yaml', snapshot.holdings)
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
    from portfolio_app.company_merges import load_settings as load_merges, load_company_names
    from portfolio_app.stock_exposure import load_company_identities
    from portfolio_app.fundamentals import load_fee_overrides
    load_merges(directory / 'company-merges.yaml')
    load_company_names(directory / 'company-names.yaml')
    load_company_identities(directory / 'company-identities.yaml')
    load_fee_overrides(directory / 'fund-fees.json')
    from portfolio_app.futures_workspace import load_sandbox, load_comparison, load_historical
    from portfolio_app.leverage_settings import load_leverage_settings
    from portfolio_app.exposure_targets import load_exposure_targets
    sandbox = load_sandbox(directory)
    load_leverage_settings(directory, future=sandbox[0] if sandbox else None)
    load_exposure_targets(directory, allocation)
    if sandbox:
        load_comparison(directory)
        load_historical(directory)
    from portfolio_app.futures_funding_data import load_funding_settings
    load_funding_settings(directory)
    from portfolio_app.account_journal import load_journal
    from portfolio_app.actual_portfolio import validate_links
    from portfolio_app.account_observations import load_observations
    validate_links(load_journal(directory)[0], snapshot.holdings)
    load_observations(directory)
    from portfolio_app.generalized_studies_ui import load_study_settings
    load_study_settings(directory)



def inventory(directory: Path) -> dict[str, str]:
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise DataError('Workspace copies do not follow symbolic links. Use ordinary files.')
        if not path.is_file() and not path.is_dir():
            raise DataError('Workspace contains a special file.')
        if path.is_file() and not path.name.endswith(('.lock', '.tmp')):
            digest = sha256()
            with path.open('rb') as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            result[path.relative_to(directory).as_posix()] = digest.hexdigest()
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
