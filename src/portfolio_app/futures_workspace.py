"""Private, independent portfolio copies for the opt-in futures sandbox."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path


from portfolio_app.futures import CONTRACTS, FuturePosition
from portfolio_app.holdings import DataError
from portfolio_app.storage import revision, save_document
from portfolio_app.futures_comparison import ComparisonSettings
from portfolio_app.futures_history import HistoricalSettings


SANDBOX_FILE = 'futures-sandbox.json'
COMPARISON_FILE = 'futures-comparison.json'
HISTORICAL_FILE = 'futures-historical-settings.json'


def load_historical(directory):
    path = directory / HISTORICAL_FILE
    if not path.exists():
        return HistoricalSettings(), None
    try:
        content = path.read_bytes()
        from hashlib import sha256
        raw = json.loads(content)
        # Financial inputs now live only in the shared comparison document.
        return HistoricalSettings(quarters=raw.get('quarters', 4), end_date=raw.get('end_date', '')), sha256(content).hexdigest()
    except (ValueError, TypeError, OSError) as exc:
        raise DataError(f'Invalid historical comparison settings: {exc}') from exc


def save_historical(directory, settings, expected_revision):
    if load_sandbox(directory) is None:
        raise DataError('Historical settings require a futures sandbox.')
    save_document(directory / HISTORICAL_FILE, json.dumps(asdict(settings), indent=2) + '\n', expected_revision)


def load_comparison(directory, *, savings_rate=.03):
    path = directory / COMPARISON_FILE
    if not path.exists():
        return ComparisonSettings(savings_rate=savings_rate), None
    try:
        content = path.read_bytes()
        from hashlib import sha256
        raw = json.loads(content)
        # Retire the short-window implied source without rewriting private files.
        # Explicit manual rates remain manual; all ECB financing now has one
        # shared editable spread (zero is available for a plain €STR scenario).
        if raw.get('financing_source') == 'implied':
            raw['financing_source'] = 'ecb'
        return ComparisonSettings(**raw), sha256(content).hexdigest()
    except (ValueError, TypeError, OSError) as exc:
        raise DataError(f'Invalid saved futures comparison: {exc}') from exc


def save_comparison(directory, settings, expected_revision):
    if load_sandbox(directory) is None:
        raise DataError('Comparison settings require a futures sandbox.')
    save_document(directory / COMPARISON_FILE, json.dumps(asdict(settings), indent=2) + '\n', expected_revision)


def load_sandbox(directory: Path) -> tuple[FuturePosition, str] | None:
    path = directory / SANDBOX_FILE
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding='utf-8')
        raw = json.loads(content)
        if raw['version'] not in (1, 2) or raw['instrument'] not in CONTRACTS or raw['simulation'] is not True:
            raise ValueError('Unsupported futures sandbox format')
        fields = dict(raw['position'])
        if fields.get('instrument', raw['instrument']) != raw['instrument']:
            raise ValueError('Conflicting instrument identities')
        fields['instrument'] = raw['instrument']
        position = FuturePosition(**fields)
        from hashlib import sha256
        return position, sha256(content.encode()).hexdigest()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DataError(f'Cannot load the futures sandbox: {exc}') from exc


def save_sandbox(directory: Path, position: FuturePosition, expected_revision: str) -> None:
    path = directory / SANDBOX_FILE
    # Existing marker required; the editor cannot enable this in a normal workspace.
    if revision(path) != expected_revision:
        raise DataError('The futures position changed. Reload before saving.')
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['position'] = asdict(position)
    raw.update(version=2, instrument=position.instrument)
    save_document(path, json.dumps(raw, indent=2) + '\n', expected_revision)


def switch_to_fmwn(directory: Path, expected_revision: str) -> None:
    """Back up the old mock via save_document; never reinterpret its entry/mark."""
    previous, current_revision = load_sandbox(directory)
    if current_revision != expected_revision:
        raise DataError('The futures position changed. Reload before switching.')
    if previous.instrument == 'FMWN':
        return
    position = FuturePosition(contracts=previous.contracts, collateral_budget=previous.collateral_budget,
                              base_beta=previous.base_beta)
    save_sandbox(directory, position, expected_revision)


def fork_futures_workspace(source: Path, destination: Path) -> Path:
    """Copy bytes, never link or overwrite. The source is only read.

    Keep caches for immediate valuation, skip backups and transient lock files.
    Reject external ETF paths so refresh cannot reach back into the source.
    """
    source, destination = source.resolve(), destination.resolve()
    if destination == source or source in destination.parents or destination in source.parents:
        raise DataError('The sandbox must be separate from the source portfolio.')
    if destination.exists():
        raise DataError('The sandbox directory already exists. Open it with --data-dir; it will not be overwritten.')
    if not (source / 'holdings.csv').is_file():
        raise DataError('The source portfolio has no holdings.csv to copy.')
    if (source / SANDBOX_FILE).exists():
        raise DataError('Choose a base portfolio without an existing futures overlay.')
    from portfolio_app.portfolio_settings import load_settings
    from portfolio_app.workspace import copy_workspace
    if load_settings(source).reporting_currency != 'EUR':
        raise DataError('Futures sandboxes currently require an EUR source portfolio.')
    # Reuse main's lease, path validation and verified byte-for-byte copy.
    copy_workspace(source, destination)
    # This marker contains no source path and is never followed back to the source.
    raw = dict(version=2, simulation=True, instrument='FMWN',
               created_at=datetime.now(timezone.utc).isoformat(), position=asdict(FuturePosition()))
    save_document(destination / SANDBOX_FILE, json.dumps(raw, indent=2) + '\n', None)
    return destination
