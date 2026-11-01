"""Explicit account inception evidence, distinct from an opening snapshot."""
from portfolio_app.holdings import DataError


def account_inceptions(journal, events=None):
    """Return validated dates before which the user declared no account assets.

    A zero cash snapshot alone has no such meaning. The reserved source marker
    is written only by the explicit newly funded account workflow. Existing
    records and corrections must remain consistent with that declaration.
    """
    from portfolio_app.account_journal import effective_events
    from portfolio_app.security_history import selected_events
    events = effective_events(journal) if events is None else events
    marked = [e for e in events if e.source == 'confirmed_zero_start']
    if not marked:
        return {}
    dates = {}
    owners = {p.id: p.account_id for p in journal.positions}
    for event in marked:
        if event.kind != 'cash_open' or event.amount != 0 or not event.account_id:
            raise DataError('Confirmed account inception requires a zero opening cash balance.')
        if event.account_id in dates and dates[event.account_id] != event.date:
            raise DataError('An account must have one confirmed inception date.')
        dates[event.account_id] = event.date
    for event in events:
        accounts = {event.account_id, event.other_account_id, owners.get(event.position_id)}
        for account in accounts.intersection(dates):
            start = dates[account]
            if event.date < start or (event.date == start and (
                    event.kind in {'debt_open', 'snapshot'} or event.kind == 'cash_open' and event.amount)):
                raise DataError('Account facts contradict its confirmed zero inception; review the opening history.')
    for book in journal.securities:
        if book.account_id in dates:
            start = dates[book.account_id]
            for event in selected_events(book):
                if event.kind == 'opening' and not event.quantity and event.source == 'confirmed_zero_start':
                    continue
                if event.date < start or (event.date == start and event.kind in {'opening', 'checkpoint'} and event.quantity):
                    raise DataError('Linked security history contradicts the account’s confirmed zero inception.')
    return dates
