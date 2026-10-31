"""Private mutable assembly; only validated immutable books leave preparation."""
from dataclasses import replace

from portfolio_app.holdings import DataError
from portfolio_app.security_history import latest_records


class ImportBook:
    def __init__(self, book):
        self.original = book
        self.position_id, self.instrument_id = book.position_id, book.instrument_id
        self.account_id, self.custody_id = book.account_id, book.custody_id
        self.records = {e.id: e for e in book.records}
        self.active_ids = dict.fromkeys(book.active_ids)
        self.replaced = {e.replaces for e in book.records if e.replaces}
        self.roots, self.latest = latest_records(book)

    @property
    def active(self):
        return any(self.records[i].kind != 'void' for i in self.active_ids)

    def append(self, event):
        if event.id in self.records:
            if self.records[event.id] != event:
                raise DataError('Record identity already has different facts.')
            return
        self.records[event.id] = event
        if event.replaces:
            self.replaced.add(event.replaces)
        root = self.roots[event.replaces] if event.replaces else event.id
        self.roots[event.id] = root
        self.latest[root] = event

    def finish(self):
        return replace(self.original, account_id=self.account_id, custody_id=self.custody_id,
                       records=tuple(self.records.values()), active_ids=tuple(self.active_ids))
