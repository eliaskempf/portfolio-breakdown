"""Synthetic public-provider-shaped examples; no network or personal history."""
from dataclasses import replace
import json

import pandas as pd
import pytest

from portfolio_app.derivative_market import make_contract
from portfolio_app.holdings import DataError
from portfolio_app.study_market import (QualifiedHistory, RateHistory, benchmark_history,
    manual_rate_history, parse_benchmark_history, translate_history, validate_history_for_contract)


def series(values=(100., 110.)):
    return pd.Series(values, index=pd.to_datetime(['2001-01-02', '2001-01-03']))


@pytest.mark.parametrize(('currency', 'content', 'benchmark', 'basis'), [
    ('EUR', 'KEY,TIME_PERIOD,OBS_VALUE\nEST.B.EU000A2X2A25.WT,2001-01-02,2.5\nEST.B.EU000A2X2A25.WT,2001-01-03,3.5', 'ESTR', 'ACT/360'),
    ('USD', json.dumps({'refRates': [{'effectiveDate': '2001-01-03', 'type': 'SOFR', 'percentRate': 3.5},
                                   {'effectiveDate': '2001-01-02', 'type': 'SOFR', 'percentRate': 2.5}]}), 'SOFR', 'ACT/360'),
    ('GBP', 'DATE,IUDSOIA\n02 Jan 2001,2.5\n03 Jan 2001,3.5', 'SONIA', 'ACT/365F'),
])
def test_currency_benchmarks_use_own_identity_dates_and_percent_units(currency, content, benchmark, basis):
    requests = []
    def fetch(url):
        requests.append(url)
        return content
    history = benchmark_history(currency, '2001-01-02', '2001-01-03', fetch)
    assert history.currency == currency and history.benchmark == benchmark and history.day_count == basis
    assert history.as_of('2001-01-02') == .025  # Never use next day's observation.
    assert history.as_of('2001-01-04') == .035
    assert len(requests) == 1
    with pytest.raises(DataError, match='recent'):
        history.as_of('2001-01-01')
    with pytest.raises(DataError, match='recent'):
        history.as_of('2001-02-01')


def test_rate_outage_has_manual_fallback_without_substituting_eur():
    def unavailable(_):
        raise OSError('Synthetic outage')
    with pytest.raises(DataError, match='manual'):
        benchmark_history('GBP', '2001-01-02', '2001-01-03', unavailable)
    manual = manual_rate_history('GBP', series((.02, .03)))
    assert manual.currency == 'GBP' and manual.benchmark == 'MANUAL' and manual.as_of('2001-01-02') == .02


@pytest.mark.parametrize('content', ['{}', '{"refRates":[]}',
    '{"refRates":[{"type":"EFFR","effectiveDate":"2001-01-02","percentRate":2}]}'])
def test_wrong_rate_series_or_empty_response_is_not_accepted(content):
    with pytest.raises(DataError):
        parse_benchmark_history('USD', content)


def test_rate_duplicate_missing_or_future_range_is_rejected():
    with pytest.raises(DataError):
        RateHistory('EUR', 'MANUAL', pd.Series([.02, .03], index=pd.to_datetime(['2001-01-02'] * 2)), 'Synthetic', 'ACT/360')
    with pytest.raises(DataError):
        benchmark_history('EUR', '2999-01-01', '2999-01-02', lambda _: pytest.fail('No network'))
    with pytest.raises(DataError):
        parse_benchmark_history('GBP', 'DATE,IUDSOIA\n02 Jan 2001,..')


def test_history_identity_and_currency_are_explicit_and_digest_tracks_inputs():
    history = QualifiedHistory(series(), 'MSCI World', 'EUR', 'net', 'Invented proxy')
    contract = make_contract('FMWN', '2001-06-15')
    assert validate_history_for_contract(history, contract, benchmark='MSCI World') is history
    assert history.digest != replace(history, currency='USD').digest
    for incompatible in (replace(history, currency='USD'), replace(history, benchmark='DAX'),
                         replace(history, return_convention='gross'), replace(history, hedged=True)):
        with pytest.raises(DataError, match='match'):
            validate_history_for_contract(incompatible, contract, benchmark='MSCI World')


def test_proxy_fx_translates_levels_and_requires_exact_dated_coverage():
    history = QualifiedHistory(series(), 'MSCI World', 'USD', 'net', 'Invented proxy')
    converted = translate_history(history, 'EUR', series((.8, .9)), source='Invented USD/EUR')
    assert converted.values.tolist() == [80., 99.]
    assert converted.currency == 'EUR' and 'FX:' in converted.source
    with pytest.raises(DataError, match='matching'):
        translate_history(history, 'EUR', series((.8, .9)).iloc[:1], source='Invented FX')
    with pytest.raises(DataError):
        translate_history(replace(history, hedged=True), 'EUR', series((.8, .9)), source='Invented FX')


@pytest.mark.parametrize('values', [series((0., 1.)), series((float('nan'), 1.)), series().iloc[::-1]])
def test_proxy_history_rejects_invalid_observations(values):
    with pytest.raises(DataError):
        QualifiedHistory(values, 'DAX', 'EUR', 'gross', 'Invented history')
