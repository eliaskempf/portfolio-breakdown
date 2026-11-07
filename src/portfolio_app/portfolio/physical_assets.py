"""Explicit physical-gold pricing identity and pure weight conversion."""

GOLD_SPOT_KEY = 'spot:gold:USD:troy_oz'
# Exact international troy ounce: https://www.royalmint.com/faqs/bullion/what-is-a-troy-ounce/
GRAMS_PER_TROY_OUNCE = 31.1034768
GOLD_WEIGHT_UNITS = {'troy oz': 1., 'grams': 1. / GRAMS_PER_TROY_OUNCE,
                     'kg': 1000. / GRAMS_PER_TROY_OUNCE}


def pricing_key(position):
    return GOLD_SPOT_KEY if position.get('price_source') == 'gold_spot' else position.get('ticker', '')


def price_unit_factor(position):
    """Convert a USD/troy-ounce quote to a price per stored quantity unit."""
    if position.get('price_source') != 'gold_spot':
        return 1.
    return GOLD_WEIGHT_UNITS[position['quantity_unit']]
