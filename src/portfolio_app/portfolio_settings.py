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


def save_settings(directory: Path, currency: str, expected_revision: str | None) -> None:
    if currency not in REPORTING_CURRENCIES:
        raise DataError('Choose EUR, USD or GBP as the reporting currency.')
    save_document(directory / 'portfolio.yaml', yaml.safe_dump(dict(version=1, reporting_currency=currency)), expected_revision)
