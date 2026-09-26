"""Convert optional position cost inputs without changing their currency."""

import math

import pandas as pd

from portfolio_app.holdings import DataError


def average_from_total(total: float | None, quantity: float) -> float | None:
    """Cost of the currently held quantity, not historical net contributions."""
    if total is None or pd.isna(total):
        return None
    if not math.isfinite(total) or total < 0:
        raise DataError('Total buy-in must be a finite nonnegative amount.')
    if not math.isfinite(quantity) or quantity < 0:
        raise DataError('Quantity must be a finite nonnegative number.')
    if quantity == 0:
        if total == 0:
            return 0.
        raise DataError('Enter a quantity greater than zero for a positive total buy-in.')
    average = total / quantity
    if not math.isfinite(average):
        raise DataError('Total buy-in divided by quantity exceeds the supported numeric range.')
    return average
