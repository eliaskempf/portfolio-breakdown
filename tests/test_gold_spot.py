"""Synthetic spot quotes and fine-gold weights; unit tests never use the network."""
from datetime import datetime, timezone
import json

import pandas as pd
import pytest

from portfolio_app.gold_prices import SpotGoldProvider, fetch_gold_quote, parse_gold_quote
from portfolio_app.holdings import DataError, metadata_dimensions, parse_holdings
from portfolio_app.physical_assets import GOLD_SPOT_KEY, GRAMS_PER_TROY_OUNCE
from portfolio_app.prices import PriceService, Quote
from portfolio_app.valuation import value_holdings

NOW = datetime(2026, 1, 5, tzinfo=timezone.utc)


def holdings(unit='troy oz', shares=1., **extra):
    row = dict(id='invented-gold', name='Invented gold', shares=shares, ticker='', isin='',
               instrument_type='physical', quantity_unit=unit, price_source='gold_spot') | extra
    return parse_holdings(pd.DataFrame([row]).to_csv(index=False))


class SyntheticProvider:
    def __init__(self):
        self.requests = []
        self.fail = False

    def price(self, key):
        self.requests.append(key)
        if self.fail:
            raise ConnectionError('Synthetic unavailable quote')
        return Quote(2000., 'USD', NOW)

    def fx(self, currency):
        assert currency == 'USD'
        if self.fail:
            raise ConnectionError('Synthetic unavailable FX')
        return Quote(.8, 'EUR', NOW)


@pytest.mark.parametrize('unit,quantity', [('troy oz', 1.), ('grams', GRAMS_PER_TROY_OUNCE),
                                          ('kg', GRAMS_PER_TROY_OUNCE / 1000.)])
def test_equivalent_weights_value_identically_and_preserve_unit_cost(unit, quantity):
    provider = SyntheticProvider()
    rows = holdings(unit, quantity, acquisition_price=1200. / quantity, acquisition_currency='EUR')
    result = value_holdings(rows, PriceService(provider, now=lambda: NOW)).iloc[0]
    assert result.current_value_eur == pytest.approx(1600.)
    assert result.current_price * quantity == pytest.approx(2000.)
    assert result.acquisition_price * quantity == pytest.approx(1200.)
    assert result.shares == pytest.approx(quantity) and result.quantity_unit == unit and result.ticker == ''
    assert result.price_observed_at == NOW.isoformat()
    assert 'Gold spot' in result.valuation_note
    assert provider.requests == [GOLD_SPOT_KEY]
    assert 'price_source' not in metadata_dimensions(rows)


def test_one_request_across_weight_units_zero_positions_and_cached_failure(tmp_path):
    provider = SyntheticProvider()
    rows = pd.concat([holdings('troy oz', 2.), holdings('kg', .1), holdings('grams', 0.)], ignore_index=True)
    prices = PriceService(provider, tmp_path / 'invented-cache.json', now=lambda: NOW)
    initial = value_holdings(rows, prices)
    assert provider.requests == [GOLD_SPOT_KEY]
    assert initial.current_value_eur.iloc[2] == 0.
    provider.fail = True
    fallback = value_holdings(rows, prices, refresh=True)
    assert fallback.current_value_eur.tolist() == initial.current_value_eur.tolist()
    assert fallback.price_status.iloc[0] == 'cached fallback'
    assert 'Synthetic unavailable quote' in fallback.valuation_note.iloc[0]
    missing = value_holdings(rows, PriceService(provider, now=lambda: NOW))
    assert missing.current_value_eur.iloc[:2].isna().all()
    assert missing.current_value_eur.iloc[2] == 0.


@pytest.mark.parametrize('fields', [dict(quantity_unit='units'), dict(quantity_unit='oz'),
    dict(ticker='GC=F'), dict(isin='DE000EWG2LD7'), dict(instrument_type='equity'),
    dict(price_source='silver_spot'), dict(manual_price=10, manual_price_currency='EUR', manual_price_date='2026-01-01')])
def test_incompatible_gold_inputs_rejected(fields):
    with pytest.raises(DataError):
        holdings(**({'unit': fields.pop('quantity_unit')} if 'quantity_unit' in fields else {}), **fields)


def test_manual_physical_assets_are_not_automatically_repriced():
    provider = SyntheticProvider()
    result = value_holdings(holdings('grams', 10, price_source='', manual_price=60,
        manual_price_currency='EUR', manual_price_date='2026-01-01'), PriceService(provider, now=lambda: NOW))
    assert result.current_value_eur.tolist() == [600.]
    assert provider.requests == []


def test_spot_adapter_preserves_metadata_and_delegates_other_requests(monkeypatch):
    payload = dict(symbol='XAU', currency='USD', price=2000., updatedAt=NOW.isoformat())
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return json.dumps(payload).encode()
    def fetch(request, timeout):
        assert request.full_url == 'https://api.gold-api.com/price/XAU'
        assert timeout == 10
        return Response()
    monkeypatch.setattr('portfolio_app.gold_prices.urlopen', fetch)
    market = SyntheticProvider()
    provider = SpotGoldProvider(market)
    assert provider.price(GOLD_SPOT_KEY) == Quote(2000., 'USD', NOW)
    assert provider.price('INVENTED').currency == 'USD'
    assert market.requests == ['INVENTED']
    assert provider.fx('USD').price == .8
    payload['currency'] = 'EUR'
    with pytest.raises(ValueError):
        fetch_gold_quote()


@pytest.mark.parametrize('change', [dict(symbol='XAG'), dict(currency='EUR'), dict(price=-1),
    dict(price=True), dict(price=float('nan')), dict(updatedAt='2026-01-05')])
def test_invalid_provider_quotes_are_rejected(change):
    with pytest.raises(ValueError):
        parse_gold_quote(dict(symbol='XAU', currency='USD', price=2000., updatedAt=NOW.isoformat()) | change)
