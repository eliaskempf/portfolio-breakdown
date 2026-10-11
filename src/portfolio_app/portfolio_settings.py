"""Private, revision-checked portfolio reporting preferences."""
from dataclasses import dataclass
from pathlib import Path
from hashlib import sha256
import yaml

from portfolio_app.holdings import DataError
from portfolio_app.storage import save_document

REPORTING_CURRENCIES = ('EUR', 'USD', 'GBP')


@dataclass(frozen=True)
class PortfolioSettings:
    reporting_currency: str = 'EUR'
    revision: str | None = None


def load_settings(directory: Path) -> PortfolioSettings:
    path = directory / 'portfolio.yaml'
    try:
        content = path.read_bytes() if path.exists() else None
        raw = yaml.safe_load(content) if content is not None else {'version': 1, 'reporting_currency': 'EUR'}
        if not isinstance(raw, dict) or raw.get('version') != 1 or raw.get('reporting_currency') not in REPORTING_CURRENCIES:
            raise ValueError
        return PortfolioSettings(raw['reporting_currency'], sha256(content).hexdigest() if content is not None else None)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        raise DataError('Invalid portfolio currency settings. Restore portfolio.yaml from a backup.') from exc


def validate_currency_change(directory: Path, currency: str) -> None:
    """EUR leverage records cannot be silently reinterpreted in another currency."""
    if currency == 'EUR':
        return
    from portfolio_app.futures_workspace import load_sandbox
    from portfolio_app.leverage_settings import load_leverage_settings, requires_leverage
    sandbox = load_sandbox(directory)
    future = sandbox[0] if sandbox else None
    leverage, _ = load_leverage_settings(directory, future=future)
    from portfolio_app.account_journal import load_journal
    journal, _ = load_journal(directory)
    remaining = [x for x in leverage.liabilities if x.id not in journal.adopted_liability_ids]
    if remaining or future is not None and not journal.accounts:
        raise DataError('Leverage records currently require EUR reporting. Select capital planning and disable leverage before changing currency; active records must first be closed/removed.')


def save_settings(directory: Path, currency: str, expected_revision: str | None) -> None:
    from portfolio_app.workspace_lock import workspace_lock
    with workspace_lock(directory):
        if currency not in REPORTING_CURRENCIES:
            raise DataError('Choose EUR, USD or GBP as the reporting currency.')
        validate_currency_change(directory, currency)
        save_document(directory / 'portfolio.yaml', yaml.safe_dump(dict(version=1, reporting_currency=currency)), expected_revision)
