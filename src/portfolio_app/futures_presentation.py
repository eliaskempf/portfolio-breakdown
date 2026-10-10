"""Shared contract labels for the position disclosure and detail dialog."""
from datetime import date
from portfolio_app.futures import CONTRACTS


def future_name(position):
    return f'{position.instrument} future'


def future_summary(position):
    """One compact line; full dates and accounting details belong in the dialog."""
    expiry = date.fromisoformat(position.contract_expiry).strftime('%b %Y') if position.contract_expiry else 'Expiry unset'
    collateral = position.capital_value if position.reference_capital_eur is not None else position.collateral_budget
    return [
        {'label': 'Contracts', 'value': f'{position.contracts:+g}' if position.contracts else '0 · Closed'},
        {'label': 'Expiry', 'value': expiry},
        {'label': 'Notional', 'value': f'€{position.notional:,.0f}'},
        {'label': 'Collateral' if position.reference_capital_eur is not None else 'Assumed collateral', 'value': f'€{collateral:,.0f}'},
    ]


def future_details(position):
    def euros(value):
        return f'€{value:,.2f}'

    funded = position.reference_capital_eur is not None
    return [
        {'label': 'Underlying / exchange', 'value': f'{CONTRACTS[position.instrument][0]} · Eurex'},
        {'label': 'Contracts', 'value': f'{position.contracts:+g}' if position.contracts else '0 · Closed'},
        {'label': 'Signed notional exposure', 'value': euros(position.notional)},
        {'label': 'Reference collateral' if funded else 'Collateral assumption',
         'value': euros(position.reference_capital_eur if funded else position.collateral_budget)},
        *([{'label': 'Current collateral balance', 'value': euros(position.capital_value)}] if funded else []),
        {'label': 'Entry date', 'value': position.entry_date or 'Not recorded'},
        {'label': 'Contract last trading day', 'value': position.contract_expiry or 'Not recorded'},
        {'label': 'Mark date', 'value': position.quote_date or 'Not recorded'},
        {'label': 'Entry / mark (points)', 'value': f'{position.entry_price:,.2f} / {position.mark_price:,.2f}'},
        {'label': 'Initial margin', 'value': euros(position.margin)},
        {'label': 'Maintenance margin', 'value': euros(position.maintenance_margin)},
    ]
