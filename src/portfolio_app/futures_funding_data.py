"""Read-only history adapters and revision-checked private study settings."""
from dataclasses import asdict, dataclass
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path

import pandas as pd

from portfolio_app.futures_funding import FundingPolicy, FundingStudy, validate_prices
from portfolio_app.holdings import DataError
from portfolio_app.storage import save_document

SETTINGS_FILE = 'futures-funding-study.json'


@dataclass(frozen=True)
class FundingHistory:
    prices: pd.Series
    digest: str
    source: str
    qualification: str


def load_funding_history(path):
    """Read an explicit local CSV, bundle directory, or existing EUR ETF snapshot.

    Never imports the archived simulator, scans other portfolios, or rewrites
    input data. An archive's USD benchmark identification remains unverified.
    """
    path = Path(path).expanduser()
    if path.is_dir():
        path = path / 'legacy' / 'data' / 'market.csv'
    try:
        content = path.read_bytes()
        if len(content) > 20_000_000:
            raise DataError('History exceeds the supported 20 MB file size.')
        if path.suffix == '.json':
            raw = json.loads(content)
            if raw.get('version') != 1 or raw.get('currency') != 'EUR' or not raw.get('ticker'):
                raise DataError('Choose an identified EUR return-history snapshot.')
            prices = pd.Series(raw['prices'], index=pd.to_datetime(raw['dates']), dtype=float)
            source = f"Adjusted EUR ETF proxy · {raw['ticker']}"
            qualification = 'Adjusted ETF prices include fund costs and tracking differences; not futures settlements.'
        else:
            frame = pd.read_csv(StringIO(content.decode('utf-8')))
            prices = pd.Series(frame['eur_index'].to_numpy(dtype=float), index=pd.to_datetime(frame['date']))
            source = 'Imported EUR index proxy'
            qualification = ('Source identity is supplied by the importer. For the research archive, benchmark identity/currency '
                'are reported by its handoff, not independently established by the small benchmark snapshot. '
                'ECB FX and equity closes can be asynchronous; this is not an official synchronized EUR index close.')
        validate_prices(prices)
        if len(prices) > 20000:
            raise DataError('Choose at most 20,000 daily observations.')
        return FundingHistory(prices, sha256(content).hexdigest(), source, qualification)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise DataError(f'Cannot load funding history: {exc}') from exc


def study_from_dict(raw):
    fields = dict(raw)
    fields['baseline'] = FundingPolicy(**fields['baseline'])
    fields['candidates'] = tuple(FundingPolicy(**p) for p in fields['candidates'])
    return FundingStudy(**fields)


def load_funding_settings(directory):
    path = Path(directory)/SETTINGS_FILE
    if not path.exists():
        return None, None
    try:
        content = path.read_bytes()
        raw = json.loads(content)
        if raw['version'] != 1 or not isinstance(raw['history_path'], str):
            raise ValueError('Unsupported funding-study settings')
        return (study_from_dict(raw['study']), raw['history_path']), sha256(content).hexdigest()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise DataError(f'Invalid funding-study settings: {exc}') from exc


def save_funding_settings(directory, study, history_path, expected_revision):
    # Financial assumptions remain in the existing shared comparison document.
    raw = dict(version=1, study=asdict(study), history_path=str(history_path))
    save_document(Path(directory)/SETTINGS_FILE, json.dumps(raw, indent=2)+'\n', expected_revision)
