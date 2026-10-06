from datetime import timedelta
import pandas as pd
import pytest

from portfolio_app.prices import PriceService, Quote, YahooProvider
from portfolio_app.valuation import portfolio_weights, value_holdings


class CountingProvider:
    def __init__(self, now):
        self.now = now
        self.price_calls = []
        self.fx_calls = []
        self.fail = False

    def price(self, ticker):
        self.price_calls.append(ticker)
        if self.fail:
            raise ConnectionError("provider offline")
        return Quote(100, "USD", self.now)

    def fx(self, currency):
        self.fx_calls.append(currency)
        if self.fail:
            raise ConnectionError("provider offline")
        return Quote(0.8, "EUR", self.now)


def test_eur_and_fx_values_weights_and_unknowns(valued):
    assert valued.loc[valued["id"] == "enr", "current_value_reporting"].iloc[0] == 80
    assert valued.loc[valued["id"] == "nvda", "current_value_reporting"].tolist() == [160, 80]
    assert valued["current_value_reporting"].sum() == 744
    assert valued["portfolio_weight"].sum() == pytest.approx(1)
    assert valued.loc[valued["id"] == "unpriced", "current_value_reporting"].isna().all()
    assert valued.loc[valued["id"] == "unpriced", "portfolio_weight"].isna().all()


def test_unique_requests_and_no_cost_basis_fallback(holdings, now):
    provider = CountingProvider(now)
    service = PriceService(provider, now=lambda: now)
    valued = value_holdings(holdings, service, refresh=True)
    assert len(provider.price_calls) == 5
    assert provider.fx_calls == ["USD"]
    assert valued["current_value_reporting"].notna().sum() == 6
    provider.fail = True
    unvalued = value_holdings(holdings, PriceService(provider, now=lambda: now))
    assert unvalued["current_value_reporting"].isna().all()


def test_persistent_cache_and_expired_fallback(tmp_path, now):
    clock = [now]
    provider = CountingProvider(now)
    path = tmp_path / "cache.json"
    service = PriceService(provider, path, now=lambda: clock[0])
    assert service.price("A").status == "fresh"
    assert service.fx("USD").quote.price == 0.8
    assert service.price("A").status == "cached"
    assert len(provider.price_calls) == 1
    service = PriceService(provider, path, now=lambda: clock[0])
    assert service.price("A").status == "cached"
    clock[0] += timedelta(minutes=16)
    provider.fail = True
    fallback = service.price("A")
    assert fallback.status == "cached fallback"
    assert fallback.quote.price == 100
    assert fallback.quote.observed_at == now
    assert "offline" in fallback.error
    assert service.fx("USD").status == "cached fallback"
    assert service.price("A").status == "cached fallback"
    assert len(provider.price_calls) == 2
    provider.fail = False
    assert service.price("A", refresh=True).status == "fresh"


def test_failed_requests_are_throttled_across_reruns(tmp_path, now):
    provider = CountingProvider(now)
    provider.fail = True
    path = tmp_path / "cache.json"
    assert PriceService(provider, path, now=lambda: now).price("A").quote is None
    assert PriceService(provider, path, now=lambda: now).price("A").quote is None
    assert provider.price_calls == ["A"]


@pytest.mark.parametrize("content", ["{bad", "[]", '{"price:A": 4}', '{"price:A": {"quote": {"price": "invalid"}}}'])
def test_corrupt_cache_does_not_break_prices(tmp_path, now, content):
    path = tmp_path / "cache.json"
    path.write_text(content)
    service = PriceService(CountingProvider(now), path, now=lambda: now)
    assert service.price("A").quote.price == 100


def test_missing_fx_keeps_native_quote(holdings, now):
    provider = CountingProvider(now)
    provider.fx = lambda currency: (_ for _ in ()).throw(ValueError("no FX"))
    valued = value_holdings(holdings, PriceService(provider, now=lambda: now))
    assert valued["current_value_reporting"].isna().all()
    assert valued.iloc[0]["current_price"] == 100
    assert "Missing USD/EUR" in valued.iloc[0]["valuation_note"]


def test_fx_must_be_eur(holdings, now):
    provider = CountingProvider(now)
    provider.fx = lambda currency: Quote(1.2, "USD", now)
    valued = value_holdings(holdings, PriceService(provider, now=lambda: now))
    assert valued["current_value_reporting"].isna().all()


def test_zero_and_unknown_denominator():
    assert portfolio_weights(pd.Series([0.0, float("nan")])).isna().all()


@pytest.mark.parametrize("price,currency", [(0, "EUR"), (-1, "EUR"), (float("nan"), "EUR"), (float("inf"), "EUR"), (1, "")])
def test_invalid_quotes(now, price, currency):
    with pytest.raises(ValueError):
        Quote(price, currency, now)


def test_yahoo_provider_unadjusted_close_and_currency_subunits(monkeypatch, now):
    import yfinance

    calls = []

    class Ticker:
        history_metadata = {"currency": "GBp"}

        def __init__(self, ticker):
            calls.append(ticker)

        def history(self, **kwargs):
            assert kwargs["auto_adjust"] is False
            return pd.DataFrame({"Close": [150, 200]}, index=pd.date_range(now, periods=2))

    monkeypatch.setattr(yfinance, "Ticker", Ticker)
    provider = YahooProvider()
    assert provider.price("EXAMPLE").price == 2
    assert provider.price("EXAMPLE").currency == "GBP"
    assert provider.fx("USD").currency == "EUR"
    assert calls[-1] == "USDEUR=X"
