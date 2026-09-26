"""Replacement snapshots, independent of purchase-history accounting."""
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from collections.abc import Mapping

import pandas as pd

from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.costs import average_from_total
from portfolio_app.positions import EMPTY_CSV
from portfolio_app.storage import revision, save_document

BALANCE_FIELDS = {'shares', 'acquisition_price', 'acquisition_currency', 'holdings_confirmed_on'}


def replacement_changes(edited: pd.DataFrame, original: pd.DataFrame, *, total_buy_in: bool = False,
                        confirmed_on: str | None = None) -> dict[str, dict]:
    """Normalize balance inputs to the existing per-unit cost representation."""
    previous = original.set_index('position_id')
    changes = {}
    for _, row in edited.iterrows():
        values = {column: row[column] for column in BALANCE_FIELDS if column in row}
        if total_buy_in:
            old = previous.loc[row.position_id]
            old_total = old.shares * old.acquisition_price
            same_total = (pd.isna(row.total_buy_in) and pd.isna(old_total)) or row.total_buy_in == old_total
            if row.shares == old.shares and same_total:
                values['acquisition_price'] = old.acquisition_price
            else:
                try:
                    values['acquisition_price'] = average_from_total(row.total_buy_in, row.shares)
                except DataError as exc:
                    raise DataError(f'{row["name"]}: {exc}') from exc
        if confirmed_on:
            values['holdings_confirmed_on'] = confirmed_on
        changes[row.position_id] = values
    return changes


def patch_holdings(path: Path, changes: Mapping[str, Mapping], *, expected_revision: str | None,
                   replacement: bool = False) -> None:
    if revision(path) != expected_revision:
        raise DataError('Holdings changed. Reload before saving.')
    content = path.read_text(encoding='utf-8-sig') if path.exists() else EMPTY_CSV
    current = parse_holdings(content)
    raw = pd.read_csv(StringIO(content), dtype=str, keep_default_na=False)
    if replacement:
        for column in ('acquisition_price', 'acquisition_currency'):
            if column not in raw:
                raw[column] = ''
    if set(changes) - set(current.position_id):
        raise DataError('A selected position no longer exists. Reload before saving.')
    for key, fields in changes.items():
        if replacement and set(fields) - BALANCE_FIELDS:
            raise DataError('Balance replacements may only change quantity, buy-in, currency and confirmation date.')
        if set(fields) & {'position_key', 'position_id', 'id'}:
            raise DataError('Position identity cannot be edited.')
        index = current.index[current.position_id == key][0]
        for column, value in fields.items():
            if column not in raw:
                raw[column] = ''
            raw.at[index, column] = '' if value is None or pd.isna(value) else str(value).strip()
        if replacement:
            price, currency = raw.at[index, 'acquisition_price'], raw.at[index, 'acquisition_currency'] if 'acquisition_currency' in raw else ''
            if price and not currency:
                try:
                    unchanged = float(price) == current.at[index, 'acquisition_price']
                except ValueError:
                    unchanged = False
                if not unchanged:
                    raise DataError('A changed buy-in needs its recorded currency.')
            if 'balance_replaced_at' not in raw:
                raw['balance_replaced_at'] = ''
            raw.at[index, 'balance_replaced_at'] = datetime.now(timezone.utc).isoformat()
    csv = raw.to_csv(index=False)
    parse_holdings(csv)
    save_document(path, csv, expected_revision)
