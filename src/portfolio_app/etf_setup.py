"""Reviewable snapshot setup, sharing validation and publication with refresh."""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd

from portfolio_app.etf import FundSnapshot, load_funds, validate_constituents
from portfolio_app.etf_discovery import Discovery
from portfolio_app.etf_sources import Source, download, publish_snapshot, retrieve_snapshot
from portfolio_app.holdings import DataError
from portfolio_app.instruments import valid_isin
from portfolio_app.locking import write_lock


@contextmanager
def snapshot_writer(directory):
    cache = Path(directory) / '.cache' / 'etf-refresh'
    cache.mkdir(parents=True, exist_ok=True)
    with ExitStack() as locks:
        try:
            locks.enter_context(write_lock(cache / 'writer.lock', blocking=False))
        except BlockingIOError:
            raise DataError('An ETF update is running. Retry after it completes.') from None
        yield


@dataclass
class SnapshotDraft:
    fund: FundSnapshot
    frame: pd.DataFrame
    stamp: date
    notes: str
    prior: bytes | None
    source: Source | None = None
    basket: pd.DataFrame | None = None


def prepare_draft(directory, identifier, *, product_url='', content=None, as_of=None, asset_class='equity', fetch=download):
    directory = Path(directory)
    source = None
    if content is None:
        isin, source = Discovery(fetch).resolve(identifier, product_url=product_url)
        stamp, frame, notes, basket = retrieve_snapshot(isin, source, fetch)
        name, wkn = source.name, source.wkn
    else:
        isin = valid_isin(identifier.get('isin'))
        if not isin:
            raise DataError('Supply the exact fund ISIN for this manual snapshot.')
        try:
            frame = validate_constituents(pd.read_csv(StringIO(content.decode('utf-8-sig')), dtype=str, keep_default_na=False))
        except (UnicodeError, ValueError) as exc:
            raise DataError(f'Invalid normalized holdings CSV: {exc}') from exc
        if asset_class not in {'equity', 'fixed_income', 'money_market'}:
            raise DataError('Choose the physical fund asset class.')
        # Non-equity constituents must be typed; never infer a company from an issuer name.
        if asset_class != 'equity' and ('instrument_type' not in frame or frame.instrument_type.eq('').any()):
            raise DataError('Bond and money-market CSVs require instrument_type for every row (bond, money_market, cash or unknown).')
        stamp, notes, basket = as_of, 'Manually supplied physical holdings; weights are fractions of the entire fund.', None
        name, wkn = identifier['name'], identifier.get('wkn', '')
    existing = next((f for f in load_funds(directory / 'etfs') if f.isin == isin), None)
    path = existing.manifest_path if existing else directory / 'etfs' / f'isin_{isin.lower()}.yaml'
    if not isinstance(stamp, date) or stamp > date.today() or (existing and stamp < existing.as_of):
        raise DataError('Snapshot dates must not be future-dated or older than the saved snapshot.')
    fund = existing or FundSnapshot('isin_' + isin.lower(), name, isin, source.tickers if source else (), date.min, product_url,
                                    pd.DataFrame(columns=['isin', 'constituent_id']), path,
                                    equity_fund=asset_class == 'equity', asset_class=asset_class,
                                    replication='physical', wkn=wkn)
    if content is not None:
        from dataclasses import replace
        fund = replace(fund, provider='', product_url='', source='Manual normalized CSV', asset_class=asset_class,
                       equity_fund=asset_class == 'equity', replication='physical', breakdown_basis='holdings',
                       proxy_source='', summaries={})
    return SnapshotDraft(fund, frame, stamp, notes, path.read_bytes() if path.exists() else None, source, basket)


def save_draft(directory, draft):
    with snapshot_writer(directory):
        return publish_snapshot(draft.fund, draft.frame, draft.stamp, source=draft.source,
                                notes=draft.notes, basket=draft.basket, prior=draft.prior)
