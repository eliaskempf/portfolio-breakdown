"""Private, revision-guarded tax inputs and bounded public market observations."""
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from urllib.request import urlopen
import xml.etree.ElementTree as ET

from portfolio_app.holdings import DataError
from portfolio_app.storage import save_document, revision
from portfolio_app.vorabpauschale import FundInputs, number, ZERO

FILE = 'tax-inputs.json'


def evidence_key(year, instrument):
    return f'{year}:{instrument}'


def load_inputs(directory):
    path = Path(directory)/FILE
    if not path.exists():
        return {'version': 1, 'funds': {}, 'settings': {}}, None
    content = path.read_bytes()
    try:
        raw = json.loads(content)
        validate_inputs(raw)
    except (ValueError, TypeError, KeyError, ArithmeticError):
        raise DataError('Tax inputs are invalid. Restore or correct the private tax-inputs file.') from None
    return raw, sha256(content).hexdigest()


def validate_inputs(raw):
    if raw['version'] != 1 or not isinstance(raw['funds'], dict) or not isinstance(raw['settings'], dict):
        raise ValueError('Invalid tax input version')
    for value in raw['funds'].values():
        if not isinstance(value, dict):
            raise ValueError('Invalid fund evidence')
        for field in ('start_price', 'end_price', 'distributions', 'expected_distributions', 'exemption'):
            if value.get(field) not in (None, ''):
                number(value[field], field)
        if value.get('distribution_mode', 'Unknown') not in ('Unknown', 'Accumulating / no distributions', 'Market history', 'Manual total'):
            raise ValueError('Invalid distributions')
        if value.get('price_basis', 'Market-price estimate') not in ('Market-price estimate', 'Redemption prices', 'Statutory market-price fallback'):
            raise ValueError('Invalid price basis')
        FundInputs(2026, exemption=value.get('exemption', '0'))
    from portfolio_app.vorabpauschale import TaxLot
    if not isinstance(raw.get('ownership', {}), dict):
        raise ValueError('Invalid ownership inputs')
    for value in raw.get('ownership', {}).values():
        if not isinstance(value.get('fingerprint'), str) or len(value['fingerprint']) != 64:
            raise ValueError('Invalid ownership revision')
        for lot in value['lots']:
            TaxLot(lot['quantity'], lot['acquired'])
    from portfolio_app.vorabpauschale import estimate_tax
    estimate_tax(0, church_rate=raw['settings'].get('church_rate', '0'))


def save_inputs(directory, raw, expected_revision):
    validate_inputs(raw)
    save_document(Path(directory)/FILE, json.dumps(raw, ensure_ascii=False, indent=2)+'\n', expected_revision)


def cache_path(directory, year, instrument):
    token = sha256(evidence_key(year, instrument).encode()).hexdigest()
    return Path(directory)/'.cache'/'tax'/f'{token}.json'


def read_market(directory, year, instrument):
    path = cache_path(directory, year, instrument)
    try:
        raw = json.loads(path.read_text())
        if raw['year'] != year or raw['instrument'] != instrument:
            return {}
        return raw
    except (OSError, ValueError, KeyError, TypeError):
        return {}


@lru_cache(maxsize=2)
def ecb_history(download_day):
    """One official ECB daily reference-rate download shared by fund requests."""
    with urlopen('https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml', timeout=20) as response:
        payload = response.read(16*1024*1024)
    return parse_ecb(payload)


def parse_ecb(payload):
    result = {}
    for node in ET.fromstring(payload).iter():
        if 'time' in node.attrib:
            result[node.attrib['time']] = {child.attrib['currency']: number(child.attrib['rate'])
                                           for child in node if 'currency' in child.attrib}
    return result


def to_eur(amount, currency, day, rates):
    if currency == 'EUR':
        return amount
    # ECB publishes on working days. Use the latest published rate on/before
    # the event (bounded to seven calendar days), never a subsequent rate.
    stamp = date.fromisoformat(day)
    for delta in range(8):
        row = rates.get((stamp-timedelta(days=delta)).isoformat(), {})
        rate = row.get(currency)
        if rate is not None and rate > 0:
            return amount/rate
    raise DataError('Dated ECB reference rate missing')


def market_from_frame(frame, currency, year, as_of, rates, *, multiplier=Decimal(1)):
    """Restore as-traded units, then normalize to the selected year-end units.

    Yahoo Close/dividends may be split normalized even with auto_adjust=False.
    The request therefore includes actions through today, including later splits.
    """
    required = {'Close', 'Stock Splits', 'Dividends'}
    if frame.empty or not required <= set(frame.columns):
        raise DataError('Price history lacks distribution or split evidence')
    daily, factor = [], Decimal(1)
    for stamp, row in frame.sort_index(ascending=False).iterrows():
        split = number(row['Stock Splits'], 'Split ratio')
        dividend = number(row['Dividends'], 'Distribution')
        close = row['Close']
        day = stamp.date().isoformat()
        if close == close and close > 0:
            daily.append((day, number(close)*factor*multiplier, dividend*factor*multiplier, split))
        elif dividend or split:
            raise DataError('Corporate action has no usable price')
        if split:
            factor *= split
    daily.reverse()
    start, end = f'{year}-01-01', min(f'{year}-12-31', as_of)
    selected = [r for r in daily if start <= r[0] <= end]
    if not selected:
        raise DataError('No observations in the calculation year')
    actions = [(day, ratio) for day, _, _, ratio in selected if ratio and ratio != 1]
    def normal(amount, day):
        for split_day, ratio in actions:
            if split_day > day:
                amount /= ratio
        return to_eur(amount, currency, day, rates)
    first, last = selected[0], selected[-1]
    distributions = [(day, normal(dividend, day)) for day, _, dividend, _ in selected if dividend]
    return {
        'start_price': str(normal(first[1], first[0])) if first[0] <= f'{year}-01-10' else None,
        'end_price': str(normal(last[1], last[0])) if (date.fromisoformat(end)-date.fromisoformat(last[0])).days <= 10 else None,
        'start_date': first[0], 'end_date': last[0],
        # Quote subunits do not establish the provider's dividend currency unit.
        # Keep usable prices while requiring an explicit distribution assumption.
        'distributions': str(sum((value for _, value in distributions), ZERO)) if multiplier == 1 or not distributions else None,
        'distribution_count': len(distributions),
        'distribution_note': 'Distribution currency unit needs review' if multiplier != 1 and distributions else '',
        'distribution_dates': [day for day, _ in distributions],
        'splits': [(day, str(ratio)) for day, ratio in actions],
        'source': 'Yahoo unadjusted market history; ECB reference FX',
        'currency': 'EUR', 'original_currency': currency,
    }


def download_market(ticker, year, *, today=None, factory=None, fx_loader=ecb_history):
    today = today or date.today()
    if factory is None:
        import yfinance as yf
        factory = yf.Ticker
    from portfolio_app.currencies import quote_unit
    instrument = factory(ticker)
    frame = instrument.history(start=f'{year}-01-01', end=(today+timedelta(days=1)).isoformat(),
        auto_adjust=False, back_adjust=False, actions=True, repair=False, timeout=15, raise_errors=True)
    currency, multiplier = quote_unit(instrument.history_metadata.get('currency', ''))
    rates = {} if currency == 'EUR' else fx_loader(today.isoformat())
    result = market_from_frame(frame, currency, year, today.isoformat(), rates, multiplier=Decimal(str(multiplier)))
    return dict(result, ticker=ticker, fetched_on=today.isoformat())


def request_market(directory, year, instrument, ticker, *, force=False, download=download_market, jobs=None):
    from portfolio_app.market_data import coordinator
    jobs = jobs or coordinator
    directory = Path(directory).resolve()
    key = ('tax', year, instrument)
    cached = read_market(directory, year, instrument)
    today = date.today().isoformat()
    fresh = cached.get('fetched_on') == today or (year < date.today().year and cached.get('end_price') is not None)
    if not ticker or (fresh and not force) or jobs.pending(directory, key):
        return jobs.pending(directory, key)
    def work():
        path = cache_path(directory, year, instrument)
        expected = revision(path)
        try:
            data = download(ticker, year)
        except Exception:
            data = dict(cached, error='Market evidence unavailable. Retry or enter inputs.', fetched_on=today)
        data.update(year=year, instrument=instrument)
        save_document(path, json.dumps(data, indent=2)+'\n', expected)
    jobs.request(directory, key, work)
    return jobs.pending(directory, key)


def prepare_inputs(year, manual, market, *, forecast=False, current_price=None):
    """Manual overrides win. No distribution records is unknown, not zero."""
    def chosen(field):
        value = manual.get(field)
        return number(value, field) if value not in ('', None) else (
            number(market[field], field) if market.get(field) is not None else None)
    mode = manual.get('distribution_mode', 'Market history' if market.get('distribution_count', 0) else 'Unknown')
    distributions = None
    if mode == 'Accumulating / no distributions':
        distributions = ZERO
    elif mode == 'Manual total' and manual.get('distributions') not in (None, ''):
        distributions = number(manual['distributions'])
    elif mode == 'Market history' and market.get('distributions') is not None:
        # Explicitly selecting this mode also permits an assumed zero result.
        distributions = number(market['distributions'])
    if forecast and distributions is not None:
        distributions += number(manual.get('expected_distributions') or '0')
    end_price = chosen('end_price')
    if forecast and manual.get('end_price') in ('', None) and current_price is not None:
        end_price = number(current_price)
    explicit_prices = all(manual.get(f) not in (None, '') for f in ('start_price', 'end_price'))
    basis = manual.get('price_basis', 'Market-price estimate') if explicit_prices else 'Market-price estimate'
    return FundInputs(year, chosen('start_price'), end_price, distributions,
                      manual.get('exemption', '0'), basis)
