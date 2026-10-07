"""Public listing identifiers and invented prices; no live requests or user data."""
from dataclasses import asdict
from types import SimpleNamespace
import json

import pandas as pd
import pytest

from portfolio_app.history import HistoryResult, HistoryService, YahooHistoryProvider
from portfolio_app.instruments import InstrumentSearch
from portfolio_app.prices import PriceService, Quote, YahooProvider
from portfolio_app.valuation import value_holdings


@pytest.mark.parametrize('offline', [False, True])
def test_overnight_isin_offers_verified_eur_listings_not_bad_feed(tmp_path, monkeypatch, offline):
    def search(*args, **kwargs):
        if offline:
            raise OSError('Invented outage')
        return SimpleNamespace(quotes=[dict(symbol='0E2B.IL', quoteType='ETF', shortname='Invented stale listing')])
    monkeypatch.setattr('portfolio_app.instruments.yf.Search', search)
    found = InstrumentSearch(tmp_path).search('LU1190417599')
    assert [x.ticker for x in found] == ['CSH2.PA', 'SMART.MI']
    assert all(x.isin == 'LU1190417599' and x.currency == 'EUR' for x in found)


@pytest.mark.parametrize('refresh', [False, True])
@pytest.mark.parametrize('prior_error', ['', 'Invented outage'])
def test_bad_spot_cache_is_never_valued_but_manual_price_remains_valid(tmp_path, now, refresh, prior_error):
    path = tmp_path / 'prices.json'
    path.write_text(json.dumps({'price:0E2B.IL': {'quote': {
        'price': 987.65, 'currency': 'EUR', 'observed_at': now.isoformat()},
        'attempted_at': now.isoformat(), 'error': prior_error}}))
    before = path.read_bytes()
    class Provider:
        def price(self, ticker):
            pytest.fail('Blocked listing must not fetch or silently switch venue')
    prices = PriceService(Provider(), path, now=lambda: now)
    cached, due = prices.cached('price:0E2B.IL')
    assert cached.quote is None and not due and 'Connect live prices' in cached.error
    row = dict(id='invented', ticker='0E2B.IL', shares=2., isin='LU1190417599')
    result = value_holdings(pd.DataFrame([row]), prices, refresh=refresh)
    assert result.current_value_reporting.isna().all()
    assert 'CSH2.PA' in result.valuation_note.iloc[0]
    row.update(manual_price=123., manual_price_currency='EUR', manual_price_date='2026-01-01')
    manual = value_holdings(pd.DataFrame([row]), prices, refresh=refresh)
    assert manual.current_value_reporting.item() == 246.
    assert path.read_bytes() == before


def test_bad_history_cache_and_forced_refresh_never_return_obsolete_values(tmp_path, now):
    class Provider:
        def history(self, *args):
            pytest.fail('Excluded history must not be requested')
    history = HistoryService(Provider(), tmp_path, now=lambda: now)
    old = HistoryResult(('2026-01-01',), (987.65,), 'EUR', 'fresh', now.isoformat())
    path = history._path('0E2B.IL', '1Y')
    path.write_text(json.dumps(asdict(old)))
    before = path.read_bytes()
    for refresh in (False, True):
        result = history.get('0E2B.IL', refresh=refresh)
        assert not result.prices and result.status == 'unavailable'
        assert 'CSH2.PA' in result.note
    assert path.read_bytes() == before


def test_direct_yahoo_adapters_reject_known_bad_feed_without_network():
    with pytest.raises(ValueError, match='unreliable'):
        YahooProvider().price('0E2B.IL')
    with pytest.raises(ValueError, match='unreliable'):
        YahooHistoryProvider().history('0E2B.IL', '1y')


def test_verified_alternative_uses_provider_price_without_fixed_scaling(now):
    class Provider:
        def price(self, ticker):
            assert ticker == 'CSH2.PA'
            return Quote(123.45, 'EUR', now)
    result = PriceService(Provider(), now=lambda: now).price('CSH2.PA')
    assert result.quote.price == 123.45 and result.status == 'fresh'
