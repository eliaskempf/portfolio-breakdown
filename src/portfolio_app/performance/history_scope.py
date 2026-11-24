"""Map Overview's existing category selection to historical source membership."""
from collections import defaultdict
from functools import cached_property

from portfolio_app.ledger.account_journal import effective_events
from portfolio_app.performance.history_observations import HistoryScope


def can_start_history_chart(journal, scope):
    """A reconstructible scope may load its first prices; summaries stay clean."""
    from portfolio_app.ledger.security_history import selected_events, affected_positions
    books = {b.position_id: b for b in journal.securities if b.active}
    return (not scope.unresolved and set(scope.positions) <= books.keys()
            and any(e.kind in {'buy', 'sell', 'swap', 'reward', 'asset_fee', 'transfer', 'inventory_in', 'inventory_out', 'checkpoint'}
                    and affected_positions(book,e).intersection(scope.positions)
                    for book in books.values() for e in selected_events(book)))


class CategoryHistoryScopes:
    """Resolve sibling scopes from the same holdings and journal during one comparison."""

    def __init__(self, journal, holdings, config, displayed=None):
        self.journal = journal
        self.holdings = holdings
        self.config = config
        self.displayed = displayed
        self.linked = {link.position_id for link in journal.cash_links}

    @cached_property
    def _holding_categories(self):
        if 'bucket_id' in self.holdings:
            return self.holdings.bucket_id
        displayed = self.displayed
        fallback = dict(zip(displayed.position_id, displayed.bucket_id)) if displayed is not None else {}
        return self.holdings.position_id.map(fallback).fillna('')

    @cached_property
    def assignments(self):
        return dict(zip(self.holdings.position_id, self._holding_categories))

    @cached_property
    def _positions(self):
        positions = defaultdict(set)
        for identity, category in zip(self.holdings.position_id, self._holding_categories):
            if identity not in self.linked:
                positions[category].add(identity)
        # A removed book belongs to Unassigned until given a current assignment.
        positions[''].update(b.position_id for b in self.journal.securities if b.position_id not in self.assignments)
        return positions

    @cached_property
    def _accounts(self):
        components = {account.id: set() for account in self.journal.accounts}
        cash = {(link.account_id, link.currency): self.assignments.get(link.position_id, '')
                for link in self.journal.cash_links}
        for event in effective_events(self.journal):
            if event.kind == 'cash_open':
                components[event.account_id].add(cash.get((event.account_id, event.currency), ''))
            elif event.kind in ('debt_open', 'borrow'):
                components[event.account_id].add('')
        configured = {b.id for b in self.config.buckets}
        for position in self.journal.positions:
            components[position.account_id].add(position.bucket_id if position.bucket_id in configured else '')
        return components

    @cached_property
    def _investment_categories(self):
        if self.displayed is None:
            return set()
        investments = set(self.displayed.attrs.get('investment_members', {})) | {'future:overlay'}
        return {category for identity, category in zip(self.displayed.position_id, self.displayed.bucket_id)
                if identity in investments}

    def scope(self, bucket=''):
        if not bucket:
            positions = set(self.holdings.position_id) - self.linked
            positions.update(b.position_id for b in self.journal.securities)
            return HistoryScope(tuple(positions), tuple(a.id for a in self.journal.accounts))
        included = {''} if bucket == 'unassigned' else self.config.descendants(bucket)
        positions = set().union(*(self._positions.get(category, ()) for category in included))
        accounts, unresolved = [], []
        for account in self.journal.accounts:
            categories = self._accounts[account.id]
            if categories.intersection(included):
                if categories <= included:
                    accounts.append(account.id)
                else:
                    unresolved.append(f'{account.name}: cash, debt or futures span categories; a dated category allocation is needed')
        if self._investment_categories.intersection(included):
            unresolved.append('This category includes allocated futures capital; dated category capital history is not yet supported')
        return HistoryScope(tuple(positions), tuple(accounts), tuple(unresolved))


def category_history_scope(journal, holdings, config, bucket='', *, displayed=None):
    return CategoryHistoryScopes(journal, holdings, config, displayed).scope(bucket)
