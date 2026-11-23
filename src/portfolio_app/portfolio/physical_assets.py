"""Physical metal quote identities and fine-weight conversion."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SpotMetal:
    name: str
    symbol: str

    @property
    def price_source(self):
        return f'{self.name.lower()}_spot'

    @property
    def quote_key(self):
        return f'spot:{self.name.lower()}:USD:troy_oz'


METALS = {metal.price_source: metal for metal in (
    SpotMetal('Gold', 'XAU'), SpotMetal('Silver', 'XAG'),
    SpotMetal('Platinum', 'XPT'), SpotMetal('Palladium', 'XPD'),
)}
# Exact international troy ounce: https://www.royalmint.com/faqs/bullion/what-is-a-troy-ounce/
GRAMS_PER_TROY_OUNCE = 31.1034768
WEIGHT_UNITS = {'troy oz': 1., 'grams': 1. / GRAMS_PER_TROY_OUNCE,
                'kg': 1000. / GRAMS_PER_TROY_OUNCE}


def is_unit_based_physical(row):
    return row.get('instrument_type') == 'physical' and not row.get('ticker') and bool(row.get('quantity_unit'))


def pricing_key(position):
    metal = METALS.get(position.get('price_source'))
    return metal.quote_key if metal else position.get('ticker', '')


def price_unit_factor(position):
    """Convert a USD/troy-ounce quote to a price per stored quantity unit."""
    if position.get('price_source') in METALS:
        return WEIGHT_UNITS[position['quantity_unit']]
    return 1.
