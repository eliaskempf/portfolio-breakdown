"""Qualified history and free currency benchmark rates for hypothetical studies.

These interfaces supply observations, never portfolio journal entries. A proxy
is not a futures settlement history; no automatic proxy or currency substitution
is made. Downloaded CSV is provider transport, not a transaction-import feature.
"""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import StringIO
import json
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from portfolio_app.derivative_market import download
from portfolio_app.holdings import DataError


BENCHMARKS = {
    'EUR': ('ESTR', 'ACT/360', 'https://data.ecb.europa.eu/data/datasets/EST/EST.B.EU000A2X2A25.WT'),
    'USD': ('SOFR', 'ACT/360', 'https://www.newyorkfed.org/markets/reference-rates/sofr'),
    'GBP': ('SONIA', 'ACT/365F', 'https://www.bankofengland.co.uk/markets/sonia-benchmark'),
}


def _series(values, *, positive=False):
    try:
        series = values.copy().astype(float)
        if (not isinstance(series.index, pd.DatetimeIndex) or series.empty
                or series.index.tz is not None or series.index.hasnans or series.index.has_duplicates
                or not series.index.is_monotonic_increasing
                or not series.index.equals(series.index.normalize())
                or not np.isfinite(series).all() or (positive and series.le(0).any())):
            raise ValueError('Invalid daily observations')
        return series
    except (AttributeError, ValueError, TypeError) as exc:
        raise DataError('History requires unique, ordered, finite daily observations.') from exc


@dataclass(frozen=True)
class RateHistory:
    currency: str
    benchmark: str
    values: pd.Series  # annual decimal rate, indexed by effective date
    source: str
    day_count: str
    qualification: str = ''

    def __post_init__(self):
        if self.currency not in BENCHMARKS or not self.benchmark or not self.source:
            raise DataError('Rate history requires currency, benchmark and provenance.')
        if self.day_count not in ('ACT/360', 'ACT/365F'):
            raise DataError('Choose an explicit supported interest day-count convention.')
        values = _series(self.values)
        if values.le(-1).any() or values.gt(10).any():
            raise DataError('Rate is outside the supported decimal range.')
        object.__setattr__(self, 'values', values)

    def as_of(self, day, *, max_age_days=7):
        cutoff = pd.Timestamp(day)
        known = self.values.loc[:cutoff]
        if known.empty or (cutoff - known.index[-1]).days > max_age_days:
            raise DataError('No sufficiently recent rate on or before the requested date. Enter a manual assumption.')
        return float(known.iloc[-1])


def manual_rate_history(currency, observations, *, source='Manual study assumption', day_count=None):
    if currency not in BENCHMARKS:
        raise DataError('Choose EUR, USD or GBP.')
    return RateHistory(currency, 'MANUAL', observations, source,
                       day_count or BENCHMARKS[currency][1], 'User-entered assumptions; not observed benchmark rates.')


def parse_benchmark_history(currency, content):
    """Parse official native responses; all provider rates arrive in percent."""
    try:
        benchmark, convention, source = BENCHMARKS[currency]
        if currency == 'EUR':
            frame = pd.read_csv(StringIO(content))
            if frame.empty or not frame['KEY'].eq('EST.B.EU000A2X2A25.WT').all():
                raise ValueError('Wrong ECB series')
            dates, rates = frame.TIME_PERIOD, frame.OBS_VALUE
        elif currency == 'USD':
            frame = pd.DataFrame(json.loads(content)['refRates'])
            if frame.empty or not frame['type'].eq('SOFR').all():
                raise ValueError('Wrong NY Fed series')
            dates, rates = frame.effectiveDate, frame.percentRate
        else:
            frame = pd.read_csv(StringIO(content))
            frame.columns = frame.columns.str.strip().str.upper()
            if frame.empty or 'IUDSOIA' not in frame or 'DATE' not in frame:
                raise ValueError('Wrong BoE series')
            dates, rates = frame.DATE, frame.IUDSOIA
        series = pd.Series(pd.to_numeric(rates).to_numpy()/100,
                           index=pd.to_datetime(dates).to_numpy()).sort_index()
        return RateHistory(currency, benchmark, series, source, convention,
                           'Free database publication is delayed; automatic access may be unavailable.' if currency == 'GBP' else '')
    except (KeyError, ValueError, TypeError) as exc:
        raise DataError(f'Could not verify {currency} benchmark history. Manual assumptions are available.') from exc


def benchmark_history(currency, start, end, fetch=download):
    """Retrieve only the requested observed dates, with no constant backfill."""
    try:
        first, last = date.fromisoformat(str(start)), date.fromisoformat(str(end))
        if currency not in BENCHMARKS or first > last or last > date.today():
            raise ValueError('Invalid currency or date range')
        if currency == 'EUR':
            url = 'https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT?' + urlencode(
                {'startPeriod': first.isoformat(), 'endPeriod': last.isoformat(), 'format': 'csvdata'})
        elif currency == 'USD':
            url = 'https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?' + urlencode(
                {'startDate': first.isoformat(), 'endDate': last.isoformat()})
        else:
            url = 'https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?' + urlencode(
                {'csv.x': 'yes', 'Datefrom': first.strftime('%d/%b/%Y'), 'Dateto': last.strftime('%d/%b/%Y'),
                 'SeriesCodes': 'IUDSOIA', 'CSVF': 'TN', 'UsingCodes': 'Y', 'VPD': 'Y', 'VFD': 'N'})
        result = parse_benchmark_history(currency, fetch(url))
        clipped = result.values.loc[first.isoformat():last.isoformat()]
        return RateHistory(result.currency, result.benchmark, clipped, result.source,
                           result.day_count, result.qualification)
    except (ValueError, TypeError, OSError) as exc:
        raise DataError('Free rate history is unavailable. Retain saved observations or enter manual study assumptions.') from exc


@dataclass(frozen=True)
class QualifiedHistory:
    values: pd.Series
    benchmark: str
    currency: str
    return_convention: str  # price, net, gross
    source: str
    qualification: str = ''
    hedged: bool = False

    def __post_init__(self):
        if (self.currency not in BENCHMARKS or self.return_convention not in ('price', 'net', 'gross')
                or not self.benchmark.strip() or not self.source.strip() or type(self.hedged) is not bool):
            raise DataError('History needs an identified benchmark, currency, return convention and provenance.')
        object.__setattr__(self, 'values', _series(self.values, positive=True))

    @property
    def digest(self):
        payload = {'benchmark': self.benchmark, 'currency': self.currency,
                   'return_convention': self.return_convention, 'source': self.source,
                   'qualification': self.qualification, 'hedged': self.hedged,
                   'dates': self.values.index.strftime('%Y-%m-%d').tolist(), 'values': self.values.tolist()}
        return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def validate_history_for_contract(history, contract, *, benchmark):
    """Reject guessed mappings, currency substitutions and return-type mismatches."""
    if (history.benchmark != benchmark or history.currency != contract.currency
            or history.return_convention != contract.return_convention or history.hedged):
        raise DataError('Proxy benchmark, currency, return convention and unhedged treatment must match the contract.')
    return history


def translate_history(history, currency, fx, *, source):
    """Translate an unhedged index/fund LEVEL into benchmark currency, not notional.

    FX is target units per source unit with an exact observation for every price
    date. Futures settlement P&L must subsequently be calculated in contract
    currency; reporting FX is applied to that P&L and cash, not full notional.
    """
    if currency not in BENCHMARKS or history.hedged or not source.strip():
        raise DataError('Choose supported target currency and explicit unhedged FX provenance.')
    if currency == history.currency:
        raise DataError('History already uses the requested currency.')
    rates = _series(fx, positive=True).reindex(history.values.index)
    if rates.isna().any():
        raise DataError('Every proxy observation needs a matching dated FX rate; no implicit fill is permitted.')
    return QualifiedHistory(history.values * rates, history.benchmark, currency,
                            history.return_convention, history.source + ' | FX: ' + source,
                            history.qualification + ' Translated with dated FX; closing times may differ.')
