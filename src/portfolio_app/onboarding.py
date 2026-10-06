"""Validation and revision-safe category creation for an empty workspace."""
import math
from uuid import uuid4

from portfolio_app.allocation import Allocation, Bucket, save_allocation
from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock
from portfolio_app.positions import read_snapshot


def initial_categories(names, targets=None):
    names = [name.strip() for name in names]
    if not names or any(not name for name in names) or len({name.casefold() for name in names}) != len(names):
        raise DataError('Enter at least one category, with a different name for each.')
    if targets is not None:
        if len(targets) != len(names) or any(value is not None and (not math.isfinite(value) or not 0 <= value <= 100) for value in targets):
            raise DataError('Targets must be between 0 and 100, or blank to leave them for later.')
        if sum(value for value in targets if value is not None) > 100.0001:
            raise DataError('Category targets cannot exceed 100% of the portfolio.')
    return Allocation(tuple(Bucket(uuid4().hex, name, target=None if targets is None or targets[i] is None else targets[i]/100)
                            for i, name in enumerate(names)))


def save_initial_categories(directory, names, targets, expected_holdings_revision):
    config = initial_categories(names, targets)
    from portfolio_app.workspace_lock import workspace_lock
    with workspace_lock(directory):
        directory.mkdir(parents=True, exist_ok=True)
        with write_lock(directory / '.holdings.csv.lock'):
            snapshot = read_snapshot(directory / 'holdings.csv')
            if not snapshot.holdings.empty or snapshot.revision != expected_holdings_revision:
                raise DataError('The portfolio changed while setup was open. Reopen setup before saving.')
            # Never replace an existing allocation, even if another session created it.
            save_allocation(directory / 'allocation.yaml', config, snapshot.holdings, expected_revision=None)
    return config
