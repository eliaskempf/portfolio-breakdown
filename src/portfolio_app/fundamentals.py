"""Listing fundamentals with explicit units and exact-share-class fund fees."""
from dataclasses import asdict, dataclass, field, replace
from datetime import date
import json
import math
from pathlib import Path
import re
from typing import Protocol

from portfolio_app.analytics_cache import AnalyticsCache, atomic_json, utc_now
from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock as _write_lock


@dataclass(frozen=True)
class MetricDefinition:
    label: str
    field: str
    unit: str
    description: str


DEFINITIONS = {
    'trailing_pe': MetricDefinition('P/E (trailing)', 'trailingPE', 'ratio', 'Price divided by trailing 12-month earnings; nonpositive ratios are not meaningful.'),
    'forward_pe': MetricDefinition('P/E (forward)', 'forwardPE', 'ratio', 'Price divided by provider-estimated forward earnings.'),
    'price_book': MetricDefinition('Price / book', 'priceToBook', 'ratio', 'Price divided by book value per share.'),
    'price_sales': MetricDefinition('Price / sales', 'priceToSalesTrailing12Months', 'ratio', 'Market capitalization divided by trailing revenue.'),
    'market_cap': MetricDefinition('Market capitalization', 'marketCap', 'currency', 'Provider market value of the company, in quote currency.'),
    'distribution_yield': MetricDefinition('Trailing cash yield', 'trailingAnnualDividendYield', 'fraction', 'Trailing cash dividends or distributions divided by price; not a forecast.'),
    'payout_ratio': MetricDefinition('Payout ratio', 'payoutRatio', 'fraction', 'Provider-reported dividends relative to earnings.'),
    'revenue_growth': MetricDefinition('Revenue growth', 'revenueGrowth', 'fraction', 'Provider-reported year-over-year quarterly revenue growth.'),
    'earnings_growth': MetricDefinition('Earnings growth', 'earningsGrowth', 'fraction', 'Provider-reported year-over-year quarterly earnings growth.'),
    'profit_margin': MetricDefinition('Profit margin', 'profitMargins', 'fraction', 'Provider-reported net income relative to revenue.'),
    'return_equity': MetricDefinition('Return on equity', 'returnOnEquity', 'fraction', 'Provider-reported trailing net income relative to shareholder equity.'),
    'fee': MetricDefinition('TER / annual fund fee', '', 'fraction', 'Annual recurring fund cost; already reflected in fund prices. Fee definitions vary by source.'),
    'fund_assets': MetricDefinition('Fund assets', 'totalAssets', 'currency', 'Provider-reported assets in fund reporting currency; unavailable when that currency is unspecified.'),
    'fund_pe': MetricDefinition('Fund-reported P/E', '', 'ratio', 'Provider aggregate valuation of fund holdings; methodology may differ from direct equities.'),
    'fund_pb': MetricDefinition('Fund-reported price / book', '', 'ratio', 'Provider aggregate book-value multiple of fund holdings.'),
}


@dataclass(frozen=True)
class Metric:
    value: float | None = None
    unit: str = ''
    source: str = ''
    as_of: str = ''
    note: str = ''


@dataclass(frozen=True)
class Fundamentals:
    ticker: str
    kind: str = 'unknown'
    metrics: dict[str, Metric] = field(default_factory=dict)
    status: str = 'unavailable'
    fetched_at: str = ''
    note: str = ''


@dataclass(frozen=True)
class FundFee:
    rate: float
    source: str
    verified_on: str
    label: str = 'TER'
    accumulating: bool = False


# Public issuer metadata, independent of any user's portfolio. These are dated
# observations, not an automatic feed. Overrides remain in the private workspace.
PUBLIC_FEES = {
    'IE00BMC38736': FundFee(.0035, 'https://www.vaneck.com/ucits/en/semiconductor-etf/', '2026-09-27', accumulating=True),
    'IE00BJ0KDQ92': FundFee(.0012, 'https://etf.dws.com/en-gb/product-finder/', '2026-09-27', accumulating=True),
    'IE00BKM4GZ66': FundFee(.0018, 'https://www.ishares.com/uk/individual/en/products/264659/ishares-msci-emerging-markets-imi-ucits-etf', '2026-09-27', accumulating=True),
    'LU1681041460': FundFee(.00229, 'https://www.amundietf.com/pdfDocuments/kiid/LU1681041460/ENG/GBR/20260211', '2026-09-27', 'Estimated ongoing charges (KIID 2026-02-11)', True),
}


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def normalize_info(ticker: str, info: dict, fund_data=None) -> Fundamentals:
    kind = {'EQUITY': 'equity', 'ETF': 'etf', 'CRYPTOCURRENCY': 'crypto'}.get(info.get('quoteType'), 'unknown')
    allowed = (set(DEFINITIONS) - {'fee', 'fund_assets', 'fund_pe', 'fund_pb'}) if kind == 'equity' else (
        {'distribution_yield', 'fund_assets'} if kind == 'etf' else set())
    metrics = {}
    currency = str(info.get('currency') or '')
    source = f'https://finance.yahoo.com/quote/{ticker}/'
    for name in allowed:
        definition = DEFINITIONS[name]
        value = finite(info.get(definition.field))
        # Fund assets can be reported in a different currency from the listing.
        units = str(info.get('fundCurrency') or info.get('financialCurrency') or '') if name == 'fund_assets' else currency
        unit = units if definition.unit == 'currency' else definition.unit
        if definition.unit == 'currency' and (len(units) != 3 or not units.isalpha() or not units.isupper()):
            value = None
        if name in {'distribution_yield', 'market_cap', 'fund_assets'} and value is not None and value < 0:
            value = None
        metrics[name] = Metric(value, unit, source)
    if kind == 'etf':
        fund_data = fund_data or {}
        fee = finite(fund_data.get('fee', info.get('annualReportExpenseRatio')))
        metrics['fee'] = Metric(fee if fee is not None and 0 <= fee <= 1 else None, 'fraction', source,
                                note='Provider annual report expense ratio')
        for name in ('fund_pe', 'fund_pb'):
            metrics[name] = Metric(finite(fund_data.get(name)), 'ratio', source)
    return Fundamentals(ticker, kind, metrics)


class FundamentalsProvider(Protocol):
    def fetch(self, ticker: str) -> Fundamentals: ...


class YahooFundamentalsProvider:
    def fetch(self, ticker):
        import yfinance as yf
        instrument = yf.Ticker(ticker)
        info = instrument.get_info()
        if not info or not info.get('quoteType'):
            raise ValueError('No fundamentals returned')
        if str(info.get('symbol', ticker)).upper() != ticker.upper():
            raise ValueError('Provider returned a different listing')
        extra = {}
        note = ''
        if info.get('quoteType') == 'ETF':
            try:
                data = instrument.get_funds_data()
                operations, equity = data.fund_operations, data.equity_holdings
                for frame, row, key in ((operations, 'Annual Report Expense Ratio', 'fee'),
                                        (equity, 'Price/Earnings', 'fund_pe'), (equity, 'Price/Book', 'fund_pb')):
                    if ticker in frame and row in frame.index:
                        extra[key] = frame.at[row, ticker]
            except Exception:
                note = 'Some fund statistics are unavailable.'
        return replace(normalize_info(ticker, info, extra), note=note)


class DemoFundamentalsProvider:
    def fetch(self, ticker):
        # Deliberately invented values, unrelated to the named public instruments.
        info = dict(quoteType='EQUITY', trailingPE=20., forwardPE=18., priceToBook=3.,
                    priceToSalesTrailing12Months=4., marketCap=1e9, currency='EUR',
                    trailingAnnualDividendYield=.02, payoutRatio=.4, revenueGrowth=.1,
                    earningsGrowth=.12, profitMargins=.2, returnOnEquity=.15)
        return replace(normalize_info(ticker, info), note='Synthetic demo fundamentals')


def fee_key(row):
    return 'isin:' + str(row['isin']).strip().upper() if row.get('isin') else 'ticker:' + str(row.get('ticker', '')).strip().upper()


def validate_fee(key: str, fee: FundFee):
    if not re.fullmatch(r'(isin:[A-Z]{2}[A-Z0-9]{9}[0-9]|ticker:[A-Z0-9^=._-]+)', key):
        raise DataError('A fee needs an exact ISIN or listing ticker.')
    if finite(fee.rate) is None or not isinstance(fee.rate, (float, int)) or not 0 <= fee.rate <= 1:
        raise DataError('Annual fees must be finite fractions between zero and one.')
    if not isinstance(fee.source, str) or not fee.source.strip() or not isinstance(fee.label, str):
        raise DataError('Enter a fee source and text label.')
    if not isinstance(fee.accumulating, bool) or date.fromisoformat(fee.verified_on) > date.today():
        raise DataError('Enter a valid verification date and accumulation policy.')


def load_fee_overrides(path: Path) -> dict[str, FundFee]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        result = {}
        for key, record in raw.items():
            fee = FundFee(**record)
            validate_fee(key, fee)
            result[key] = fee
        return result
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise DataError('Invalid private fund fee overrides') from exc


def save_fee_override(path: Path, key: str, fee: FundFee | None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock(path.with_suffix('.lock')):
        records = load_fee_overrides(path)
        if fee is None:
            records.pop(key, None)
        else:
            validate_fee(key, fee)
            records[key] = fee
        atomic_json(path, {key: asdict(value) for key, value in records.items()})


def apply_fund_metadata(snapshot, row, overrides, *, today=None):
    today = today or date.today()
    isin = str(row.get('isin') or '').upper()
    public = PUBLIC_FEES.get(isin)
    # An explicit saved type is authoritative; unknown rows may use provider metadata.
    declared = row.get('instrument_type') or 'unknown'
    kind = declared if declared != 'unknown' else ('etf' if public else snapshot.kind)
    if kind not in {'equity', 'etf'}:
        return replace(snapshot, kind=kind, metrics={})
    if kind == 'equity':
        return replace(snapshot, kind=kind, metrics={k: v for k, v in snapshot.metrics.items()
                       if k not in {'fee', 'fund_assets', 'fund_pe', 'fund_pb'}})
    metrics = {k: v for k, v in snapshot.metrics.items() if k in {'fee', 'fund_assets', 'fund_pe', 'fund_pb', 'distribution_yield'}}
    # Never interpret equity dividend fields as fund metrics after a type mismatch.
    if snapshot.kind != 'etf':
        metrics = {}
    fee = overrides.get(fee_key(row)) or public
    if fee:
        age = (today - date.fromisoformat(fee.verified_on)).days
        metrics['fee'] = Metric(fee.rate, 'fraction', fee.source, fee.verified_on,
            fee.label + (' · private override' if fee_key(row) in overrides else ' · dated issuer metadata')
            + (' · verification older than 90 days' if age > 90 else ''))
        income_source = fee if fee.accumulating else public if public and public.accumulating else None
        if income_source:
            metrics['distribution_yield'] = Metric(0., 'fraction', income_source.source, income_source.verified_on,
                                                   'Accumulating share class: no cash distributions')
    return replace(snapshot, kind='etf', metrics=metrics)


class FundamentalsService:
    def __init__(self, provider: FundamentalsProvider, directory: Path | None = None, *, now=utc_now):
        self.provider, self.cache = provider, AnalyticsCache(directory, now=now)

    def get(self, ticker: str, *, refresh=False):
        if not ticker:
            return Fundamentals('', note='No listing ticker')
        ticker = ticker.strip().upper()
        result = self.cache.get('fundamentals-v1:' + ticker, lambda: asdict(self.provider.fetch(ticker)), refresh=refresh)
        try:
            raw = result.data or {}
            metrics = {key: Metric(**value) for key, value in raw.get('metrics', {}).items() if key in DEFINITIONS}
            if any(value.value is not None and finite(value.value) is None for value in metrics.values()):
                raise ValueError('Invalid cached metric')
            return Fundamentals(ticker, raw.get('kind', 'unknown'), metrics, result.status, result.fetched_at,
                                result.note or raw.get('note', ''))
        except (ValueError, TypeError, AttributeError):
            return Fundamentals(ticker, note='Invalid cached fundamentals; refresh to retry.')
