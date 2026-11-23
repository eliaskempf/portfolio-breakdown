"""Current precious-metal quotes from Gold API."""
import json
from urllib.request import Request, urlopen

from portfolio_app.portfolio.physical_assets import METALS
from portfolio_app.market.prices import Quote, parse_time

# Public precious-metal spot in USD per troy ounce, with the provider's observation time.
# Contract: https://gold-api.com/docs and https://gold-api.com/llms.txt
METALS_BY_KEY = {metal.quote_key: metal for metal in METALS.values()}


def parse_spot_quote(payload, symbol):
    if payload.get('symbol') != symbol or payload.get('currency') != 'USD':
        raise ValueError(f'Spot response must identify {symbol} in USD.')
    if isinstance(payload.get('price'), bool):
        raise ValueError('Invalid metal spot price.')
    return Quote(float(payload['price']), 'USD', parse_time(payload['updatedAt']))


def fetch_spot_quote(symbol):
    request = Request(f'https://api.gold-api.com/price/{symbol}', headers={'Accept': 'application/json', 'User-Agent': 'PortfolioBreakdown/0.1'})
    with urlopen(request, timeout=10) as response:
        payload = response.read(65537)
    if len(payload) > 65536:
        raise ValueError('Spot response exceeds the supported size.')
    return parse_spot_quote(json.loads(payload), symbol)


class SpotMetalProvider:
    """Route metal quotes by identity and delegate listings and FX."""
    def __init__(self, market_provider):
        self.market_provider = market_provider

    def price(self, key):
        metal = METALS_BY_KEY.get(key)
        return fetch_spot_quote(metal.symbol) if metal else self.market_provider.price(key)

    def fx(self, currency):
        return self.market_provider.fx(currency)
