"""Normalize and commit reviewed current-position snapshots, independent of UI."""
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from io import StringIO
from numbers import Real
from pathlib import Path
from uuid import uuid4

import pandas as pd

from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.instruments import Instrument, valid_isin
from portfolio_app.positions import read_snapshot
from portfolio_app.storage import save_document


@dataclass(frozen=True)
class ImportOptions:
    mapping: dict[str, str]
    decimal: str = ','
    defaults: dict[str, str] = field(default_factory=dict)
    use_prices: bool = False
    quantity_unit: str = ''


@dataclass
class ImportDraft:
    positions: pd.DataFrame
    issues: list[str]
    warnings: list[str]
    included: int
    excluded: int


def parse_number(value, decimal: str = ',') -> float:
    if isinstance(value, Real):
        number = float(value)
    else:
        value = str(value).strip().replace('\u00a0', '').replace('\u202f', '').replace(' ', '')
        thousands = '.' if decimal == ',' else ','
        pattern = rf'[+]?((\d{{1,3}}({re.escape(thousands)}\d{{3}})+)|\d+)({re.escape(decimal)}\d+)?'
        if not re.fullmatch(pattern, value):
            raise DataError('Use a nonnegative number in the selected number format (without currency symbols).')
        number = float(value.replace(thousands, '').replace(decimal, '.'))
    if not math.isfinite(number) or number < 0:
        raise DataError('Quantity and amounts must be finite and nonnegative.')
    return number


def parse_date(value: str) -> str:
    try:
        if re.fullmatch(r'\d{2}\.\d{2}\.\d{4}', value):
            day, month, year = value.split('.')
            value = f'{year}-{month}-{day}'
        parsed = date.fromisoformat(value)
        if value != parsed.isoformat() or parsed > datetime.now().astimezone().date():
            raise ValueError
        return value
    except ValueError:
        raise DataError('Use a price date in YYYY-MM-DD or DD.MM.YYYY format, no later than today.') from None


def currency(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch('[A-Z]{3}', value):
        raise DataError('Supply a three-letter currency, such as EUR.')
    return value


def normalize_table(table: pd.DataFrame, options: ImportOptions, *, source: str = 'Table',
                    first_row: int = 2, included: list[bool] | None = None) -> ImportDraft:
    issues, warnings, positions = [], [], []
    included = [True] * len(table) if included is None else included
    if len(included) != len(table):
        raise DataError('The row selection no longer matches the table.')
    if any(not options.mapping.get(key) for key in ('name', 'shares')):
        return ImportDraft(pd.DataFrame(), ['Map instrument name and quantity before importing.'], [], sum(included), len(table) - sum(included))
    if options.mapping.get('acquisition_price') and options.mapping.get('total_cost'):
        return ImportDraft(pd.DataFrame(), ['Map either average buy-in or total buy-in, not both.'], [], sum(included), len(table) - sum(included))
    for offset, (_, row) in enumerate(table.iterrows()):
        if not included[offset]:
            continue
        location = f'{source}, row {first_row + offset}'
        def get(key, row=row):
            column = options.mapping.get(key)
            raw = row[column] if column else ''
            value = '' if raw is None or pd.isna(raw) else str(raw).strip()
            return value or options.defaults.get(key, '').strip()
        try:
            name = get('name')
            if not name:
                raise DataError('Instrument name is missing. Exclude blank or non-position rows.')
            if re.match(r'^(gesamt|summe|subtotal|total)(\b|:)', name, re.IGNORECASE):
                raise DataError('Possible subtotal: explicitly exclude it or correct the instrument name.')
            quantity = parse_number(get('shares'), options.decimal)
            isin, wkn, ticker = get('isin').upper(), get('wkn').upper(), get('ticker').upper()
            if isin and not valid_isin(isin):
                raise DataError('ISIN has an invalid format or check digit. Correct it or leave it blank.')
            if wkn and not re.fullmatch('[A-Z0-9]{6}', wkn):
                raise DataError('WKN must contain six letters/digits. Preserve leading zeroes.')
            values = {'name': name, 'shares': quantity, 'isin': isin, 'wkn': wkn, 'ticker': ticker,
                      'account': get('account'), 'portfolio': '', 'bucket_id': '', 'within_bucket_target': '',
                      'acquisition_price': '', 'acquisition_currency': '', 'quantity_unit': options.quantity_unit,
                      'manual_price': '', 'manual_price_currency': '', 'manual_price_date': '',
                      'import_source': 'snapshot', 'import_row': location}
            cost, total = get('acquisition_price'), get('total_cost')
            if cost or total:
                amount = parse_number(cost or total, options.decimal)
                if total and quantity == 0:
                    raise DataError('Total buy-in cannot be converted for zero quantity.')
                values.update(acquisition_price=amount if cost else amount / quantity,
                              acquisition_currency=currency(get('acquisition_currency')))
            price = get('price')
            if options.use_prices and price:
                if options.quantity_unit not in {'shares', 'units'}:
                    raise DataError('Confirm a per-unit quantity convention before using exported prices; nominal/percent quotes need conversion.')
                unit_price = parse_number(price, options.decimal)
                if unit_price <= 0:
                    raise DataError('Snapshot unit price must be positive; clear it to import without valuation.')
                values.update(manual_price=unit_price, manual_price_currency=currency(get('price_currency')),
                              manual_price_date=parse_date(get('price_date')))
            if not isin and not wkn and not ticker:
                warnings.append(f'{location}: no instrument identifier; link a listing later.')
            if not values['manual_price'] and not ticker:
                warnings.append(f'{location}: no valuation yet; the position will remain visible.')
            positions.append(values)
        except DataError as exc:
            issues.append(f'{location}: {exc}')
    return ImportDraft(pd.DataFrame(positions), issues, warnings, sum(included), len(table) - sum(included))


def combine_drafts(drafts: list[ImportDraft]) -> ImportDraft:
    frames = [draft.positions for draft in drafts if not draft.positions.empty]
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    issues = [issue for draft in drafts for issue in draft.issues]
    warnings = [warning for draft in drafts for warning in draft.warnings]
    identities, seen, names, tickers = {}, {}, {}, {}
    for index, row in frame.iterrows():
        # A listing distinguishes exchange/currency variants of the same ISIN.
        identity = (row['isin'] or row.wkn or row.ticker, row.ticker)
        if not identity[0]:
            identity = ('unidentified', str(index))
        if row.ticker:
            previous = tickers.setdefault(row.ticker, identity)
            if previous != identity:
                issues.append(f'{row.import_row}: the same ticker has conflicting identifiers. Correct the mapping before importing.')
        signature = (row['isin'] or row.wkn or row.ticker or row['name'].casefold(), row.ticker, row.account)
        if signature in seen:
            issues.append(f'{row.import_row}: duplicate position; also present at {seen[signature]}. Exclude or correct one row.')
        seen[signature] = row.import_row
        asset_id = identities.setdefault(identity, uuid4().hex)
        name = names.setdefault(asset_id, row['name'])
        if name != row['name']:
            warnings.append(f'{row.import_row}: shared instrument name standardized to the first matching identifier.')
        frame.at[index, 'id'] = asset_id
        frame.at[index, 'name'] = name
        frame.at[index, 'position_key'] = uuid4().hex
    return ImportDraft(frame, issues, warnings, sum(d.included for d in drafts), sum(d.excluded for d in drafts))


def save_import(path: Path, draft: ImportDraft, *, expected_revision: str | None, validate=None) -> None:
    if draft.issues or draft.positions.empty:
        raise DataError('Resolve all included-row errors before importing at least one position.')
    snapshot = read_snapshot(path)
    if snapshot.revision != expected_revision:
        raise DataError('Holdings changed. Review the import again.')
    if not snapshot.holdings.empty:
        raise DataError('Experimental import requires an empty portfolio. Existing positions were not changed.')
    frame = draft.positions.drop(columns=['import_row'], errors='ignore')
    content = frame.to_csv(index=False)
    candidate = parse_holdings(content)
    if validate:
        validate(candidate)
    save_document(path, content, expected_revision)


def link_listing(path: Path, asset_id: str, listing: Instrument, *, expected_revision: str | None,
                 use_live_prices: bool = False, validate=None) -> None:
    snapshot = read_snapshot(path)
    if snapshot.revision != expected_revision:
        raise DataError('Holdings changed. Reload before linking a listing.')
    current = snapshot.holdings
    selected = current.loc[current.id.eq(asset_id)]
    if selected.empty or not listing.ticker:
        raise DataError('Select an existing instrument and a price listing.')
    known_isin = selected.iloc[0]['isin']
    if known_isin and listing.isin != known_isin:
        raise DataError('The listing must have the same verified ISIN as the imported instrument.')
    if listing.isin and not valid_isin(listing.isin):
        raise DataError('The listing ISIN is invalid.')
    others = current.loc[current.id.ne(asset_id)]
    if others.ticker.eq(listing.ticker).any():
        raise DataError('This listing belongs to another instrument already. Resolve the duplicate before linking.')
    raw = pd.read_csv(StringIO(path.read_text(encoding='utf-8-sig')), dtype=str, keep_default_na=False)
    mask = raw.id.eq(asset_id)
    for column, value in {'ticker': listing.ticker, 'isin': listing.isin or known_isin,
                          'instrument_type': {'EQUITY': 'equity', 'ETF': 'etf', 'ETC': 'etc', 'CRYPTOCURRENCY': 'crypto'}.get(listing.kind, 'unknown')}.items():
        if column not in raw:
            raw[column] = ''
        raw.loc[mask, column] = value
    if use_live_prices:
        for column in ('manual_price', 'manual_price_currency', 'manual_price_date'):
            if column in raw:
                raw.loc[mask, column] = ''
    content = raw.to_csv(index=False)
    candidate = parse_holdings(content)
    if validate:
        validate(candidate)
    save_document(path, content, expected_revision)
