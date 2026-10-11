"""Free, exact-contract observations. Fetching a quote never records cash or trades.

The website statistics interface is not a guaranteed historical-data service.
Its date selector may advertise dates with no rows. Final settlements must be
entered manually until a separately verified final-settlement source is added.
"""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType
from urllib.error import URLError
from urllib.request import Request, urlopen

from portfolio_app.derivative_types import ContractSpec, PriceObservation
from portfolio_app.holdings import DataError
from portfolio_app.storage import revision, save_document


@dataclass(frozen=True)
class ProductDefinition:
    product: str
    name: str
    currency: str
    multiplier: Decimal
    tick: Decimal
    return_convention: str
    underlying: str
    product_isin: str
    source: str
    statistics_id: str
    as_of: str = '2026-10-11'


def _product(code, name, currency, multiplier, tick, convention, underlying, isin, path, pid):
    return ProductDefinition(code, name, currency, Decimal(multiplier), Decimal(tick),
                             convention, underlying, isin, 'https://www.eurex.com/ex-en/markets/idx/' + path, pid)


# Public product metadata, verified against Eurex specifications. MSCI multipliers
# and ticks also appear in the public MSCI specifications/vendor-codes workbook.
CATALOG = MappingProxyType({p.product: p for p in (
    _product('FMWN', 'MSCI World EUR', 'EUR', '100', '.05', 'net', 'MSCI World', 'DE000A11Q364',
             'msci/regional/MSCI-World-Index-Futures-in-EUR-254542', '33934'),
    _product('FMWO', 'MSCI World USD', 'USD', '10', '1', 'net', 'MSCI World', 'DE000A1RRXJ7',
             'msci/regional/MSCI-World-Index-Futures-in-USD-254552', '33938'),
    _product('FMWB', 'MSCI World GBP', 'GBP', '5', '1', 'net', 'MSCI World', 'DE000A2RPLX5',
             'msci/regional/MSCI-World-Index-Futures-in-GBP-1525872', '1525870'),
    _product('FESX', 'EURO STOXX 50', 'EUR', '10', '1', 'price', 'EURO STOXX 50', 'DE0009652388',
             'stx/euro-stoxx-50-derivatives/products/EURO-STOXX-50-Index-Futures-160088', '34652'),
    _product('FTUK', 'FTSE 100', 'GBP', '10', '.5', 'price', 'FTSE 100', 'DE000A2QNKH7',
             'ftse/ftse-100/ftse-100-derivatives/FTSE-100-Index-Futures-2593794', '2593794'),
    _product('FDAX', 'DAX', 'EUR', '25', '1', 'gross', 'DAX', 'DE0008469594',
             'dax/DAX-Futures-139902', '34642'),
    _product('FDXM', 'Mini-DAX', 'EUR', '5', '1', 'gross', 'DAX', 'DE000A160WT6',
             'dax/Mini-DAX-Futures-34666', '34666'),
    _product('FDXS', 'Micro-DAX', 'EUR', '1', '1', 'gross', 'DAX', 'DE000A2QNFN5',
             'dax/Micro-DAX-Futures-2615492', '2615492'),
)})


def _day(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else date.fromisoformat(value.isoformat())
    except (ValueError, TypeError, AttributeError) as exc:
        raise DataError('A valid ISO business date is required.') from exc


def make_contract(product, expiry, *, last_trade_date='', final_settlement_date=''):
    """Use curated economics with an explicit maturity; never invent a calendar."""
    try:
        spec = CATALOG[product]
    except KeyError as exc:
        raise DataError('Product is not in the verified catalog. Enter a manual contract.') from exc
    maturity = _day(expiry).isoformat()
    return ContractSpec(product=product, expiry=maturity, currency=spec.currency,
                        multiplier=spec.multiplier, tick=spec.tick, name=spec.name,
                        source=spec.source, as_of=spec.as_of,
                        last_trade_date=last_trade_date or maturity,
                        final_settlement_date=final_settlement_date,
                        return_convention=spec.return_convention)


def manual_contract(product, expiry, currency, multiplier, tick, **metadata):
    """Manual specifications are available without a network or catalog match."""
    return ContractSpec(product=product, expiry=_day(expiry).isoformat(), currency=currency,
                        multiplier=multiplier, tick=tick, **metadata)


def manual_mark(contract, business_date, price, *, source='manual', kind='manual'):
    if not source.strip():
        raise DataError('A manual price requires provenance.')
    return PriceObservation(contract_id=contract.id, date=_day(business_date).isoformat(),
                            price=price, kind=kind, source=source,
                            observed_at=datetime.now(timezone.utc).isoformat())


def download(url):
    try:
        with urlopen(Request(url, headers={'User-Agent': 'Portfolio local research'}), timeout=20) as response:
            return response.read().decode('utf-8-sig')
    except (URLError, OSError, UnicodeError) as exc:
        raise DataError('Free market source is unavailable; saved observations are retained. Manual entry is available.') from exc


def statistics_request(html, product):
    """Discover a business date/API identifier from the verified product page."""
    candidates = []
    try:
        for script in re.findall(r'<script\b[^>]*data-product="[^"]+"[^>]*>(.*?)</script>', html, re.S):
            raw = json.loads(script)
            if 'busDate' in raw and 'productId' in raw:
                if str(raw['productId']) != product.statistics_id:
                    continue
                day = datetime.strptime(raw['busDate'], '%Y%m%d').date()
                if day > date.today():
                    raise ValueError('Future business date')
                candidates.append((day, str(raw['productId'])))
        if len(set(candidates)) != 1:
            raise ValueError('Missing or ambiguous product statistics')
        return candidates[0]
    except (ValueError, TypeError, KeyError) as exc:
        raise DataError('Could not verify the Eurex product business date.') from exc


def _url(product, day):
    return f'https://www.eurex.com/api/v1/overallstatistics/{product.statistics_id}?busdate={day:%Y%m%d}&filtertype=overview'


def _rows(raw, product, day):
    try:
        if (raw['meta']['productCode'] != product.product or raw['meta']['isin'] != product.product_isin
                or raw['meta'].get('productType', 'F') != 'F'):
            raise ValueError('Wrong instrument')
        published = {datetime.strptime(value, '%d-%m-%Y %H:%M').date() for value in raw['header']['tradingDates']}
        if day not in published or day > date.today():
            raise ValueError('Requested business date is unavailable')
        rows = raw.get('dataRows', [])
        if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            raise ValueError('No observations for requested business date')
        return rows
    except (KeyError, ValueError, TypeError) as exc:
        raise DataError(f'Could not verify Eurex observations: {exc}') from exc


def _known_product(contract):
    product = CATALOG.get(contract.product)
    if (product is None or contract.exchange != 'EUREX' or contract.currency != product.currency
            or contract.multiplier != product.multiplier or contract.tick != product.tick):
        raise DataError('Automatic data requires a matching verified contract specification. Use manual entry.')
    return product


def parse_settlement(raw, contract, business_date, *, source, observed_at=None):
    product, day = _known_product(contract), _day(business_date)
    if day >= _day(contract.last_trade_date or contract.expiry):
        raise DataError('Automatic final/expiry-day settlement is unsupported; record a verified final settlement manually.')
    if source != _url(product, day):
        raise DataError('Settlement source does not match the requested product and date.')
    try:
        rows = [r for r in _rows(raw, product, day)
                if r.get('contractType') == 'M' and r.get('date') == _day(contract.expiry).strftime('%Y%m%d')]
        if len(rows) != 1:
            raise ValueError('Exact maturity is missing or ambiguous')
        price = Decimal(str(rows[0]['dSettle']))
        if not price.is_finite() or price <= 0:
            raise ValueError('Invalid daily settlement')
        # The public JSON sometimes contains binary-float tails (e.g. 410.00000000000006).
        # Settlement levels need not obey trading ticks: only remove tiny artifacts.
        nearest = (price / contract.tick).to_integral_value() * contract.tick
        if abs(price-nearest) <= contract.tick * Decimal('0.00000001'):
            price = nearest
        return PriceObservation(contract_id=contract.id, date=day.isoformat(), price=price,
                                kind='daily_settlement', source=source,
                                observed_at=observed_at or datetime.now(timezone.utc).isoformat())
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        raise DataError(f'Could not verify the exact contract settlement: {exc}') from exc


def fetch_settlement(contract, business_date, fetch=download):
    product, day = _known_product(contract), _day(business_date)
    if day >= _day(contract.last_trade_date or contract.expiry):
        raise DataError('Automatic final/expiry-day settlement is unsupported; record a verified final settlement manually.')
    url = _url(product, day)
    try:
        raw = json.loads(fetch(url))
    except DataError:
        raise
    except (ValueError, TypeError) as exc:
        raise DataError('Market source returned invalid statistics.') from exc
    return parse_settlement(raw, contract, day, source=url)


def discover_contracts(product, fetch=download):
    """List observed maturities only; not a promise of liquidity/history coverage."""
    try:
        spec = CATALOG[product]
        day, _ = statistics_request(fetch(spec.source), spec)
        rows = _rows(json.loads(fetch(_url(spec, day))), spec, day)
        expiries = {datetime.strptime(row['date'], '%Y%m%d').date() for row in rows if row.get('contractType') == 'M'}
        return tuple(make_contract(product, expiry) for expiry in sorted(expiries) if expiry > day)
    except (KeyError, ValueError, TypeError) as exc:
        raise DataError('Could not discover supported contract maturities.') from exc


@dataclass(frozen=True)
class QuoteResult:
    observation: PriceObservation | None
    status: str  # fresh, cached, stale, unavailable
    message: str = ''


class SettlementCache:
    """Private per-contract/date observations, with optimistic revision checks."""
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, contract, day):
        identity = sha256(contract.id.encode()).hexdigest()
        return self.root / identity / (_day(day).isoformat() + '.json')

    def load(self, contract, business_date):
        path = self._path(contract, business_date)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text())
            if raw['version'] != 1:
                raise ValueError('Unsupported cache version')
            observation = PriceObservation(**raw['observation'])
            if observation.contract_id != contract.id or observation.date != _day(business_date).isoformat():
                raise ValueError('Wrong observation identity')
            return observation
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise DataError('Invalid saved contract observation.') from exc

    def save(self, contract, observation, expected_revision):
        if observation.contract_id != contract.id:
            raise DataError('Observation belongs to another contract.')
        path = self._path(contract, observation.date)
        save_document(path, json.dumps({'version': 1, 'observation': asdict(observation)}, default=str) + '\n', expected_revision)

    def get(self, contract, business_date, *, refresh=False, fetch=download, as_of=None, max_age_days=7):
        day = _day(business_date)
        current = _day(as_of or date.today())
        if day > current:
            raise DataError('A quote cannot be later than the valuation date.')
        path = self._path(contract, day)
        expected = revision(path)
        saved = self.load(contract, day)
        stale = (current - day).days > max_age_days
        if saved is not None and not refresh:
            return QuoteResult(saved, 'stale' if stale else 'cached')
        try:
            observed = fetch_settlement(contract, day, fetch)
        except (DataError, OSError) as exc:
            return QuoteResult(saved, 'stale' if saved else 'unavailable', str(exc))
        self.save(contract, observed, expected)
        return QuoteResult(observed, 'stale' if stale else 'fresh')
