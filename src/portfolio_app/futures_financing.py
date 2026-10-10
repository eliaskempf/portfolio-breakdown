"""Indicative FMWN forward carry from same-day quarterly settlement pairs.

For an NTR index, F_far/F_near cancels the shared index anchor in a constant
carry model. This is settlement-implied forward carry between the two maturities,
not a spot-to-front funding quote or a broker borrowing rate.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import json
import math

from portfolio_app.futures_market import EUREX_URL, download, statistics_request
from portfolio_app.holdings import DataError
from portfolio_app.storage import revision, save_document

FINANCING_FILE = 'futures-financing-history.json'


def financing_url(day):
    return f'https://www.eurex.com/api/v1/overallstatistics/33934?busdate={day:%Y%m%d}&filtertype=overview'


def available_dates(raw):
    return sorted({datetime.strptime(value, '%d-%m-%Y %H:%M').date() for value in raw['header']['tradingDates']})


def implied_forward_carry(near_price, far_price, days):
    """Nominal ACT/360 daily-compounded rate, matching the rolling engine."""
    if days <= 0 or not all(math.isfinite(p) and p > 0 for p in (near_price, far_price)):
        raise DataError('Carry requires positive settlement prices and increasing maturities.')
    rate = 360*math.expm1(math.log(far_price/near_price)/days)
    if not -.1 <= rate <= .2:
        raise DataError('Implied financing is outside the supported -10% to 20% range.')
    return rate


def parse_financing(raw, day):
    try:
        if raw['meta']['productCode'] != 'FMWN' or raw['meta']['isin'] != 'DE000A11Q364':
            raise ValueError('Wrong futures instrument')
        # tradingDates is the AVAILABLE history menu, not the requested row date.
        if day not in available_dates(raw):
            raise ValueError('Requested day is outside the published history window')
        rows = sorted((row for row in raw['dataRows'] if row['contractType'] == 'M'
                       and datetime.strptime(row['date'], '%Y%m%d').date() > day), key=lambda row: row['date'])
        near, far = rows[:2]
        first, second = (datetime.strptime(row['date'], '%Y%m%d').date() for row in (near, far))
        if first.month not in (3, 6, 9, 12) or second.month not in (3, 6, 9, 12) or not 60 <= (second-first).days <= 120:
            raise ValueError('Expected adjacent quarterly expiries')
        near_price, far_price = float(near['dSettle']), float(far['dSettle'])
        rate = implied_forward_carry(near_price, far_price, (second-first).days)
        return dict(date=day.isoformat(), near_expiry=first.isoformat(), far_expiry=second.isoformat(),
                    near_settlement=near_price, far_settlement=far_price, rate=rate, source=financing_url(day))
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise DataError(f'Could not verify FMWN implied financing: {exc}') from exc


def validate_history(raw):
    try:
        if raw['version'] != 1 or raw['instrument'] != 'FMWN' or not raw['observations']:
            raise ValueError('Unsupported or empty financing history')
        datetime.fromisoformat(raw['fetched_at'])
        previous = None
        for row in raw['observations']:
            day = date.fromisoformat(row['date'])
            near, far = date.fromisoformat(row['near_expiry']), date.fromisoformat(row['far_expiry'])
            if (previous and day <= previous) or not day < near < far or row['source'] != financing_url(day):
                raise ValueError('Invalid dates or provenance')
            rate = implied_forward_carry(row['near_settlement'], row['far_settlement'], (far-near).days)
            if not math.isclose(rate, row['rate'], abs_tol=1e-10):
                raise ValueError('Rate does not match its settlement pair')
            previous = day
        return raw
    except (ValueError, KeyError, TypeError) as exc:
        raise DataError(f'Invalid financing-history snapshot: {exc}') from exc


def load_financing_history(directory):
    path = directory / FINANCING_FILE
    if not path.exists():
        return None
    try:
        return validate_history(json.loads(path.read_text()))
    except (OSError, ValueError) as exc:
        raise DataError(f'Could not load financing history: {exc}') from exc


def latest_financing_observation(directory, as_of):
    history = load_financing_history(directory)
    if history is None:
        return None
    available = [row for row in history['observations'] if row['date'] <= as_of.isoformat()]
    if not available or (as_of-date.fromisoformat(available[-1]['date'])).days > 7:
        return None
    return available[-1]


def refresh_financing_history(directory, fetch=download):
    path = directory / FINANCING_FILE
    expected = revision(path)
    latest, url = statistics_request(fetch(EUREX_URL))
    raw = json.loads(fetch(url))
    latest_row = parse_financing(raw, latest)
    days = [day for day in available_dates(raw) if day < latest][-30:]

    def retrieve(day):
        try:
            return parse_financing(json.loads(fetch(financing_url(day))), day), None
        except (OSError, ValueError) as exc:
            return None, dict(date=day.isoformat(), reason=str(exc))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(retrieve, days))
    observations = [row for row, _ in results if row is not None] + [latest_row]
    # Keep already retrieved older observations as the public window advances.
    previous = load_financing_history(directory)
    merged = {row['date']: row for row in previous['observations']} if previous else {}
    merged.update({row['date']: row for row in observations})
    snapshot = dict(version=1, instrument='FMWN', observations=[merged[day] for day in sorted(merged)],
                    missing=[failure for _, failure in results if failure],
                    fetched_at=datetime.now(timezone.utc).isoformat())
    validate_history(snapshot)
    save_document(path, json.dumps(snapshot, indent=2)+'\n', expected)
    return snapshot
