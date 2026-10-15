"""Observed contract settlements and derived MTM, never broker cash transfers."""
from datetime import date, datetime, timezone
import json
import math

import pandas as pd

from portfolio_app.futures import CONTRACTS
from portfolio_app.futures_market import fetch_contract_settlement
from portfolio_app.holdings import DataError
from portfolio_app.storage import revision, save_document

MTM_FILE = 'futures-settlement-history.json'


def validate_quote(quote, expiry):
    if (quote['instrument'] != 'FMWN' or quote['contract_expiry'] != expiry
            or quote['kind'] != 'Daily settlement' or isinstance(quote['price'], bool)
            or not math.isfinite(quote['price']) or quote['price'] <= 0
            or not quote['source'].startswith('https://www.eurex.com/')
            or date.fromisoformat(quote['quote_date']) >= date.fromisoformat(expiry)):
        raise ValueError('Invalid contract settlement')


def load_settlements(directory):
    path = directory / MTM_FILE
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        if raw['version'] != 1:
            raise ValueError('Unsupported settlement-history version')
        for expiry, quotes in raw['contracts'].items():
            seen = set()
            for quote in quotes:
                validate_quote(quote, expiry)
                if quote['quote_date'] in seen:
                    raise ValueError('Duplicate settlement date')
                seen.add(quote['quote_date'])
        return raw['contracts']
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise DataError('Invalid saved settlement history; existing file retained.') from exc


def settlement_dates(position):
    if position.instrument != 'FMWN' or not position.entry_date or not position.quote_date:
        raise DataError('Daily MTM requires a dated FMWN entry and mark.')
    days = pd.bdate_range(position.entry_date, position.quote_date)
    if len(days) > 270:
        raise DataError('Settlement downloads are limited to one year per current contract.')
    return days


def refresh_settlements(directory, position, *, fetch=fetch_contract_settlement, refresh=False):
    """Fetch exact maturities on exact dates; keep verified cache on failures.

    Weekdays are candidates, not an invented exchange calendar. Holidays or
    unavailable observations remain explicit gaps. No interpolation is allowed.
    """
    days = settlement_dates(position)
    path = directory / MTM_FILE
    expected = revision(path)
    contracts = load_settlements(directory)
    quotes = {q['quote_date']: q for q in contracts.get(position.contract_expiry, [])}
    unavailable = []
    fetched = 0
    for day in days:
        stamp = day.date().isoformat()
        if stamp in quotes and not refresh:
            continue
        try:
            quote = fetch(day.date(), date.fromisoformat(position.contract_expiry))
            validate_quote(quote, position.contract_expiry)
            if quote['quote_date'] != stamp:
                raise ValueError('Requested settlement date was not returned')
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            unavailable.append(stamp)
            continue
        quotes[stamp] = quote
        fetched += 1
    if fetched:
        contracts[position.contract_expiry] = sorted(quotes.values(), key=lambda q: q['quote_date'])
        save_document(path, json.dumps(dict(version=1, contracts=contracts,
            fetched_at=datetime.now(timezone.utc).isoformat()), indent=2) + '\n', expected)
    return tuple(unavailable)


def daily_mtm(position, quotes):
    """Constant signed quantity during this entry-to-mark window, before fees.

    Undated funding/fees cannot produce daily cash balances. Closing and rolls
    require historical quantities; the current snapshot cannot reconstruct them.
    """
    if not position.contracts:
        raise DataError('This contract is closed. Historical quantities are required to reconstruct its daily MTM.')
    days = settlement_dates(position)
    by_date = {}
    try:
        for quote in quotes:
            validate_quote(quote, position.contract_expiry)
            if quote['quote_date'] in by_date:
                raise ValueError('Duplicate settlement date')
            by_date[quote['quote_date']] = quote
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DataError('Invalid settlement series for this contract.') from exc
    scale = position.contracts * CONTRACTS[position.instrument][1]
    rows = []
    previous = position.entry_price
    for day in days:
        stamp = day.date().isoformat()
        quote = by_date.get(stamp)
        price = quote['price'] if quote else None
        rows.append({'Date': stamp, 'Settlement (points)': price,
            'Daily MTM (EUR)': (price - previous) * scale if price is not None and previous is not None else None,
            'Contract P&L (EUR)': (price - position.entry_price) * scale if price is not None else None,
            'Coverage': 'Observed settlement' if quote else 'No verified settlement (holiday or unavailable)',
            'Source': quote['source'] if quote else ''})
        previous = price
    return pd.DataFrame(rows)


def mtm_stats(table):
    changes = table['Daily MTM (EUR)'].dropna()
    complete = len(table) > 0 and table['Settlement (points)'].notna().all()
    # Include zero initial gain so an immediate loss counts as a drawdown.
    curve = pd.concat([pd.Series([0.]), table['Contract P&L (EUR)']], ignore_index=True)
    return dict(complete=complete,
        total=table['Contract P&L (EUR)'].iloc[-1] if len(table) and pd.notna(table['Contract P&L (EUR)'].iloc[-1]) else None,
        best=float(changes.max()) if complete else None,
        worst=float(changes.min()) if complete else None,
        drawdown=float((curve.cummax() - curve).max()) if complete else None,
        up=int(changes.gt(0).sum()) if complete else None,
        down=int(changes.lt(0).sum()) if complete else None,
        flat=int(changes.eq(0).sum()) if complete else None)
