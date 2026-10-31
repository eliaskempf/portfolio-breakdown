"""Map Overview's existing category selection to historical source membership."""
from portfolio_app.account_journal import effective_events
from portfolio_app.history_observations import HistoryScope
from portfolio_app.strategic import bucket_positions


def can_start_history_chart(journal, scope):
    """A reconstructible scope may load its first prices; summaries stay clean."""
    from portfolio_app.security_history import selected_events, affected_positions
    books = {b.position_id: b for b in journal.securities if b.active}
    return (not scope.unresolved and set(scope.positions) <= books.keys()
            and any(e.kind in {'buy', 'sell', 'swap', 'reward', 'asset_fee', 'transfer', 'inventory_in', 'inventory_out', 'checkpoint'}
                    and affected_positions(book,e).intersection(scope.positions)
                    for book in books.values() for e in selected_events(book)))


def category_history_scope(journal, holdings, config, bucket='', *, displayed=None):
    if 'bucket_id' not in holdings:
        assignments = displayed.set_index('position_id').bucket_id.to_dict() if displayed is not None else {}
        holdings = holdings.assign(bucket_id=holdings.position_id.map(assignments).fillna(''))
    linked = {link.position_id for link in journal.cash_links}
    selected = bucket_positions(holdings, config, bucket)
    positions = set(selected.position_id) - linked
    unresolved = []
    if not bucket:
        # Retained histories must survive closing/removing the current position.
        positions.update(b.position_id for b in journal.securities)
        return HistoryScope(tuple(positions), tuple(a.id for a in journal.accounts))
    included = {''} if bucket == 'unassigned' else config.leaves(bucket)
    assignments = holdings.set_index('position_id').bucket_id.to_dict()
    if bucket == 'unassigned':
        positions.update(b.position_id for b in journal.securities if b.position_id not in assignments)
    cash = {(link.account_id, link.currency): assignments.get(link.position_id, '') for link in journal.cash_links}
    components = {a.id: set() for a in journal.accounts}
    for event in effective_events(journal):
        if event.kind == 'cash_open':
            components[event.account_id].add(cash.get((event.account_id, event.currency), ''))
        elif event.kind in ('debt_open', 'borrow'):
            components[event.account_id].add('')
    for position in journal.positions:
        components[position.account_id].add(position.bucket_id if position.bucket_id in config.leaves() else '')
    accounts = []
    for account in journal.accounts:
        categories = components[account.id]
        if not categories.intersection(included):
            continue
        if categories <= included:
            accounts.append(account.id)
        else:
            unresolved.append(f'{account.name}: cash, debt or futures span categories; a dated category allocation is needed')
    if displayed is not None:
        selected_display = bucket_positions(displayed, config, bucket)
        investment_ids = set(displayed.attrs.get('investment_members', {})) | {'future:overlay'}
        if set(selected_display.position_id).intersection(investment_ids):
            unresolved.append('This category includes allocated futures capital; dated category capital history is not yet supported')
    return HistoryScope(tuple(positions), tuple(accounts), tuple(unresolved))
