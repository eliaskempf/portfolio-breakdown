"""Dated FMWN settlements and an explicitly labelled EUR equity volatility proxy."""
from dataclasses import replace
from datetime import date, datetime, timezone
import json
import math
import re
from urllib.request import Request, urlopen
from urllib.parse import urlparse, parse_qs

import numpy as np
import pandas as pd

from portfolio_app.futures import CONTRACTS
from portfolio_app.holdings import DataError
from portfolio_app.storage import revision, save_document


QUOTE_FILE = 'futures-quote.json'
ENTRY_QUOTE_FILE = 'futures-entry-quote.json'
VOL_FILE = 'futures-volatility.json'
EUREX_URL = CONTRACTS['FMWN'][2]
PROXY_URL = 'https://www.ishares.com/de/privatanleger/de/produkte/251882/ishares-msci-world-ucits-etf-acc'
PROXY_TICKER = 'EUNL.DE'  # EUR Xetra listing of the accumulating MSCI World ETF.


def download(url):
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
        return response.read().decode('utf-8')


def statistics_request(html):
    """Use the public product page's business date, never the download date."""
    for script in re.findall(r'<script\b[^>]*data-product="33934"[^>]*>(.*?)</script>', html, re.S):
        raw = json.loads(script)
        if raw.get('productId') == '33934' and 'busDate' in raw:
            day = datetime.strptime(raw['busDate'], '%Y%m%d').date()
            if day > date.today():
                raise DataError('Eurex returned a future business date.')
            return day, f'https://www.eurex.com/api/v1/overallstatistics/33934?busdate={day:%Y%m%d}&filtertype=overview'
    raise DataError('Eurex business date unavailable; the previous price is retained.')


def parse_settlement(raw, business_date, source, *, contract_expiry=None):
    try:
        if raw['meta']['productCode'] != 'FMWN' or raw['meta']['isin'] != 'DE000A11Q364':
            raise ValueError('Wrong futures instrument')
        published_dates = [datetime.strptime(stamp, '%d-%m-%Y %H:%M').date() for stamp in raw['header']['tradingDates']]
        if contract_expiry is None:
            if not published_dates or published_dates[0] != business_date:
                raise ValueError('Statistics business date does not match the request')
        else:
            # Historical requests retain the full date selector in the header,
            # newest date first. The requested busdate selects the dataRows.
            request = urlparse(source)
            if (request.scheme != 'https' or request.netloc != 'www.eurex.com'
                    or request.path != '/api/v1/overallstatistics/33934'
                    or parse_qs(request.query).get('busdate') != [business_date.strftime('%Y%m%d')]
                    or business_date not in published_dates):
                raise ValueError('Historical statistics date does not match the request')
            if business_date > date.today() or contract_expiry <= business_date:
                raise ValueError('Invalid historical quote or contract date')
        rows = sorted((r for r in raw['dataRows'] if r['contractType'] == 'M'
                       and datetime.strptime(r['date'], '%Y%m%d').date() > business_date), key=lambda r: r['date'])
        if contract_expiry is not None:
            rows = [row for row in rows if row['date'] == contract_expiry.strftime('%Y%m%d')]
            if len(rows) != 1:
                raise ValueError('Requested contract settlement is unavailable or ambiguous')
        row = rows[0]  # Exact requested maturity, or nearest unexpired quarterly contract.
        price = float(row['dSettle'])
        if not math.isfinite(price) or price <= 0:
            raise ValueError('No valid settlement for the nearest contract')
        return dict(instrument='FMWN', price=round(price, 8), quote_date=business_date.isoformat(),
                    contract_expiry=datetime.strptime(row['date'], '%Y%m%d').date().isoformat(),
                    kind='Daily settlement', source=source, product_source=EUREX_URL,
                    fetched_at=datetime.now(timezone.utc).isoformat())
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DataError(f'Could not verify the FMWN settlement: {exc}') from exc


def fetch_contract_settlement(business_date, contract_expiry, fetch=download):
    """Read an exact maturity on an explicit business day, without saving a trade."""
    url = f'https://www.eurex.com/api/v1/overallstatistics/33934?busdate={business_date:%Y%m%d}&filtertype=overview'
    return parse_settlement(json.loads(fetch(url)), business_date, url, contract_expiry=contract_expiry)


def position_from_settlements(position, entry, mark, *, contracts=1, collateral_budget=0., entry_convention=''):
    """Hypothetical fills at observed settlements of ONE maturity; no roll-gap P&L.

    Earmarked collateral is an assumption, not a deposit into portfolio wealth.
    Entry and mark provenance remain independent when the position is edited.
    """
    if (entry['instrument'] != 'FMWN' or mark['instrument'] != 'FMWN'
            or entry['kind'] != 'Daily settlement' or mark['kind'] != 'Daily settlement'
            or entry['contract_expiry'] != mark['contract_expiry']):
        raise DataError('Entry and mark must be FMWN settlements of the same contract.')
    return replace(position, instrument='FMWN', contracts=contracts, collateral_budget=collateral_budget,
                   entry_price=entry['price'], entry_date=entry['quote_date'],
                   entry_reference_price=entry['price'], entry_price_source=entry['source'],
                   entry_convention=entry_convention,
                   mark_price=mark['price'], reference_price=mark['price'], quote_date=mark['quote_date'],
                   contract_expiry=mark['contract_expiry'], price_source=mark['source'])


def refresh_quote(directory, fetch=download):
    path = directory / QUOTE_FILE
    expected = revision(path)
    day, url = statistics_request(fetch(EUREX_URL))
    result = parse_settlement(json.loads(fetch(url)), day, url)
    save_document(path, json.dumps(result, indent=2) + '\n', expected)
    return result


def load_observation(directory, filename):
    path = directory / filename
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        datetime.fromisoformat(raw['fetched_at'])
        if filename in (QUOTE_FILE, ENTRY_QUOTE_FILE):
            if raw['instrument'] != 'FMWN' or not math.isfinite(raw['price']) or raw['price'] <= 0:
                raise ValueError('Invalid quote')
            if date.fromisoformat(raw['quote_date']) >= date.fromisoformat(raw['contract_expiry']):
                raise ValueError('Expired quote contract')
            if raw['kind'] != 'Daily settlement' or not isinstance(raw['source'], str) or not raw['source'].startswith('https://www.eurex.com/'):
                raise ValueError('Invalid quote provenance')
        else:
            if raw['ticker'] != PROXY_TICKER or not 0 <= raw['volatility'] <= 1 or raw['observations'] < 200:
                raise ValueError('Invalid volatility observation')
            date.fromisoformat(raw['start_date'])
            date.fromisoformat(raw['end_date'])
        return raw
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise DataError(f'Invalid saved market observation: {exc}') from exc


def apply_quote(directory, expected_revision):
    from portfolio_app.futures_workspace import load_sandbox, save_sandbox
    loaded = load_sandbox(directory)
    quote = load_observation(directory, QUOTE_FILE)
    if not loaded or not quote or loaded[0].instrument != 'FMWN':
        raise DataError('A saved FMWN quote and sandbox are required.')
    if date.fromisoformat(quote['contract_expiry']) <= date.today():
        raise DataError('This contract has expired. Refresh its quote before starting a new mock.')
    current = loaded[0]
    if current.reference_capital_eur is not None and current.contracts:
        raise DataError('Close the funded contract at its saved closing mark before starting a new entry; this preserves lifetime P&L.')
    # A new mock entry at the same settlement avoids manufacturing a gain from
    # replacing the invented 500-point mark. save_sandbox preserves a backup.
    updated = replace(current, entry_price=quote['price'], mark_price=quote['price'],
                      reference_price=quote['price'], quote_date=quote['quote_date'],
                      contract_expiry=quote['contract_expiry'], price_source=quote['source'],
                      entry_date=quote['quote_date'], entry_reference_price=quote['price'],
                      entry_price_source=quote['source'], entry_convention='Entry at saved daily settlement')
    save_sandbox(directory, updated, expected_revision)


def estimate_volatility(prices, as_of):
    """Three-year adjusted daily EUR ETF log-return volatility; no look-ahead."""
    series = pd.Series(prices).sort_index()
    if not isinstance(series.index, pd.DatetimeIndex) or series.index.has_duplicates:
        raise DataError('Volatility history requires unique daily dates.')
    if series.index.tz is not None:
        series.index = series.index.tz_localize(None)
    cutoff = pd.Timestamp(as_of)
    series = series.loc[(series.index <= cutoff) & (series.index >= cutoff - pd.DateOffset(years=3))]
    if len(series) < 201 or not np.isfinite(series).all() or series.le(0).any():
        raise DataError('At least 201 valid daily proxy prices are required.')
    if (cutoff - series.index[-1]).days > 7:
        raise DataError('Proxy history is too old for the selected quote date.')
    returns = np.log(series).diff().dropna()
    vol = float(returns.std(ddof=1) * math.sqrt(252))
    if not 0 <= vol <= 1:
        raise DataError('Proxy volatility is outside the supported range.')
    return dict(ticker=PROXY_TICKER, volatility=vol, observations=len(returns),
                start_date=series.index[0].date().isoformat(), end_date=series.index[-1].date().isoformat(),
                as_of=cutoff.date().isoformat(), source=PROXY_URL,
                fetched_at=datetime.now(timezone.utc).isoformat())


def refresh_volatility(directory, as_of, service=None):
    from portfolio_app.risk_data import RiskHistoryService, YahooRiskHistoryProvider
    service = service or RiskHistoryService(YahooRiskHistoryProvider(), directory / '.cache' / 'futures-risk')
    path = directory / VOL_FILE
    expected = revision(path)
    result = service.get(PROXY_TICKER, 3, refresh=True)
    if result.currency != 'EUR' or result.status not in ('fresh', 'cached'):
        raise DataError('Fresh EUR proxy history unavailable; previous observation retained.')
    observation = estimate_volatility(result.prices, as_of)
    save_document(path, json.dumps(observation, indent=2) + '\n', expected)
    return observation
