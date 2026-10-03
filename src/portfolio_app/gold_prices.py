"""Gold API's XAU spot adapter; listed instruments and FX retain their provider."""
import json
from urllib.request import Request, urlopen

from portfolio_app.physical_assets import GOLD_SPOT_KEY
from portfolio_app.prices import Quote, parse_time

# Public XAU spot in USD per troy ounce, with the provider's observation time.
# Contract: https://gold-api.com/docs and https://gold-api.com/llms.txt
GOLD_SPOT_URL = 'https://api.gold-api.com/price/XAU'


def parse_gold_quote(payload):
    if payload.get('symbol') != 'XAU' or payload.get('currency') != 'USD':
        raise ValueError('Gold spot response must identify XAU in USD.')
    if isinstance(payload.get('price'), bool):
        raise ValueError('Invalid gold spot price.')
    return Quote(float(payload['price']), 'USD', parse_time(payload['updatedAt']))


def fetch_gold_quote():
    request = Request(GOLD_SPOT_URL, headers={'Accept': 'application/json', 'User-Agent': 'PortfolioBreakdown/0.1'})
    with urlopen(request, timeout=10) as response:
        payload = response.read(65537)
    if len(payload) > 65536:
        raise ValueError('Gold spot response exceeds the supported size.')
    return parse_gold_quote(json.loads(payload))


class SpotGoldProvider:
    """Compose spot gold with the ordinary provider; caches remain in PriceService."""
    def __init__(self, market_provider):
        self.market_provider = market_provider

    def price(self, key):
        return fetch_gold_quote() if key == GOLD_SPOT_KEY else self.market_provider.price(key)

    def fx(self, currency):
        return self.market_provider.fx(currency)
