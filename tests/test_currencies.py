"""Invented prices retain the same units in spot, chart and risk paths."""
import pandas as pd
import pytest

from portfolio_app.history import normalize_history
from portfolio_app.prices import YahooProvider
from portfolio_app.risk import eur_prices


@pytest.mark.parametrize('quoted,currency,expected', [
    ('GBp', 'GBP', 2.), ('GBX', 'GBP', 2.), ('ZAc', 'ZAR', 2.),
    ('ILA', 'ILS', 2.), ('GBP', 'GBP', 200.), ('EUR', 'EUR', 200.),
])
def test_quote_units_agree_across_price_paths(monkeypatch, quoted, currency, expected):
    import yfinance

    frame = pd.DataFrame({'Close': [200.]}, index=pd.to_datetime(['2026-01-02'], utc=True))
    class Ticker:
        history_metadata = {'currency': quoted}
        def history(self, **kwargs):
            return frame.copy()
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: Ticker())
    spot = YahooProvider().price('SYNTHETIC')
    chart = normalize_history(frame, quoted)
    fx = pd.Series([1.], index=frame.index)
    risk = eur_prices(frame.Close, quoted, fx)
    assert spot.currency == chart.currency == currency
    assert spot.price == chart.prices[0] == risk.iloc[0] == expected
