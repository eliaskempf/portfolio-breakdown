"""Synthetic settlement pairs and date menus; no live requests."""
from datetime import date
import json

import pytest

from portfolio_app.futures_financing import (FINANCING_FILE, financing_url, implied_forward_carry,
    load_financing_history, parse_financing, refresh_financing_history)
from portfolio_app.futures_market import EUREX_URL
from portfolio_app.holdings import DataError


def synthetic_curve(day=date(2001, 1, 3), rate=.015):
    gap=(date(2001, 6, 15)-date(2001, 3, 16)).days
    return dict(meta=dict(productCode='FMWN', isin='DE000A11Q364'),
        header=dict(tradingDates=['03-01-2001 12:00', '02-01-2001 12:00']),
        dataRows=[dict(contractType='D', date='20010104', dSettle=999.),
                  dict(contractType='M', date='20010316', dSettle=100.),
                  dict(contractType='M', date='20010615', dSettle=100*(1+rate/360)**gap)])


def synthetic_fetch(url):
    if url == EUREX_URL:
        return '<script data-product="33934">{"productId":"33934","busDate":"20010103"}</script>'
    return json.dumps(synthetic_curve())


def test_implied_rate_round_trips_carry_convention_and_uses_quarterly_pair():
    assert parse_financing(synthetic_curve(), date(2001, 1, 3))['rate'] == pytest.approx(.015)
    # The history menu lists newest first even for an older selected date.
    row = parse_financing(synthetic_curve(rate=-.01), date(2001, 1, 2))
    assert row['rate'] == pytest.approx(-.01)
    assert row['near_expiry'] == '2001-03-16'
    assert row['source'] == financing_url(date(2001, 1, 2))
    with pytest.raises(DataError):
        parse_financing(synthetic_curve(), date(2000, 12, 29))
    with pytest.raises(DataError):
        implied_forward_carry(0, 100, 90)
    raw = synthetic_curve()
    raw['meta']['productCode'] = 'FMWO'
    with pytest.raises(DataError):
        parse_financing(raw, date(2001, 1, 3))


def test_refresh_saves_actual_dates_and_preserves_snapshot_if_latest_fails(tmp_path):
    result = refresh_financing_history(tmp_path, synthetic_fetch)
    assert [r['date'] for r in result['observations']] == ['2001-01-02', '2001-01-03']
    assert load_financing_history(tmp_path) == result
    before = (tmp_path/FINANCING_FILE).read_bytes()
    with pytest.raises(DataError):
        refresh_financing_history(tmp_path, lambda url: synthetic_fetch(url) if url == EUREX_URL else '{}')
    assert (tmp_path/FINANCING_FILE).read_bytes() == before


def test_missing_old_date_is_reported_without_fabrication_and_old_cache_retained(tmp_path):
    refresh_financing_history(tmp_path, synthetic_fetch)
    def fetch(url):
        if 'busdate=20010102' in url:
            raise OSError('Synthetic missing observation')
        return synthetic_fetch(url)
    result = refresh_financing_history(tmp_path, fetch)
    assert len(result['observations']) == 2
    assert result['missing'][0]['date'] == '2001-01-02'
    raw = result.copy()
    raw['observations'][0]['rate'] = .1
    (tmp_path/FINANCING_FILE).write_text(json.dumps(raw))
    with pytest.raises(DataError, match='match'):
        load_financing_history(tmp_path)


def write_synthetic_financing_history(directory, as_of='2025-01-02'):
    from datetime import timedelta
    import pandas as pd
    start = date.fromisoformat(as_of)
    first = pd.Timestamp(start) + pd.DateOffset(months=3)
    second = first + pd.DateOffset(months=3)
    days = (second-first).days
    rate = .015
    row = dict(date=start.isoformat(), near_expiry=first.date().isoformat(), far_expiry=second.date().isoformat(),
        near_settlement=100., far_settlement=100*(1+rate/360)**days, rate=rate, source=financing_url(start))
    raw = dict(version=1, instrument='FMWN', observations=[row], missing=[], fetched_at=as_of+'T12:00:00+00:00')
    (directory/FINANCING_FILE).write_text(json.dumps(raw))
    return raw
