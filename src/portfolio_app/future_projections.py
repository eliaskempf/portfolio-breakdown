"""Named EUR projections of recorded futures facts, never tradable holdings.

Capital uses funded value and lifetime gain. Risk has a separate instrument ID
and takes signed notional as its weight. Exposure retains source identity while
its asset identities can be index or proxy constituents. Keep these contracts
separate; FuturePosition remains the sole owner of accounting formulas.
"""
FUTURE_SOURCE_ID = 'future:overlay'


def future_name(future):
    return f'{future.instrument} future'


def capital_position(future, bucket_id):
    """Overview compatibility row: gain / fixed reference capital, not TWR."""
    return dict(position_id=FUTURE_SOURCE_ID, id=future.instrument, name=future_name(future),
        instrument_type='future_strategy', shares=1., current_value_reporting=future.capital_value,
        bucket_id=bucket_id,
        cost_basis=future.reference_capital_eur, cost_basis_reporting=future.reference_capital_eur,
        unrealized_gain_reporting=future.lifetime_gain, cost_estimated=False,
        unrealized_gain=future.lifetime_gain, return_pct=future.reference_return_pct,
        acquisition_currency='EUR', reporting_currency='EUR', within_bucket_target=float('nan'),
        performance_note='Lifetime gain / fixed reference capital; not time-weighted return.')


def capital_contribution(future, bucket_id):
    funded = future.reference_capital_eur is not None
    return dict(source_position_id=FUTURE_SOURCE_ID if funded else 'future:settlement',
                name=future_name(future) if funded else 'Mock futures settlement adjustment',
                bucket_id=bucket_id, value=future.capital_value if funded else future.pnl,
                kind='funded futures strategy' if funded else 'simulation adjustment')


def exposure_source(future, bucket_id):
    return dict(source_position_id=FUTURE_SOURCE_ID, source_instrument=future.instrument,
                bucket_id=bucket_id, account='')


def risk_position(future):
    """Notional is supplied separately to the risk engine, never stored as wealth."""
    return dict(id=FUTURE_SOURCE_ID, name=future_name(future), shares=1.,
                current_value_reporting=future.capital_value, quote_currency='EUR',
                instrument_type='future', reporting_currency='EUR')
