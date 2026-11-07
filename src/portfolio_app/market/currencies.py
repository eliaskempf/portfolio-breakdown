"""Case-sensitive quote subunits shared by spot and historical prices."""

SUBUNITS = {'GBp': ('GBP', .01), 'GBX': ('GBP', .01), 'ZAc': ('ZAR', .01), 'ILA': ('ILS', .01)}


def quote_unit(currency: str) -> tuple[str, float]:
    return SUBUNITS.get(currency, (currency, 1.0))
