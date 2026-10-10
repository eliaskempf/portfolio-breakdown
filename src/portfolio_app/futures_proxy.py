"""Explicit companion position and approximate index holdings; no name inference."""
from portfolio_app.etf import matching_fund


def linked_fund(future, valued, funds):
    if future is None or not future.proxy_position_id or future.instrument != 'FMWN':
        return None
    positions = valued.loc[valued.position_id.eq(future.proxy_position_id)]
    if len(positions) != 1:
        return None
    position = positions.iloc[0].to_dict()
    # A saved ticker or snapshot alone is not a held companion ETF. Keep the
    # full index notional unresolved if that source was closed or reclassified.
    if position.get('instrument_type') != 'etf' or not position.get('shares', 0) > 0:
        return None
    fund = matching_fund(position, funds)
    return fund if fund and fund.equity_fund else None
