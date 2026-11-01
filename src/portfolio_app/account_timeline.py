"""Bounded, lazy account checkpoints using the canonical journal replay engine."""
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Sequence

from portfolio_app.derivative_types import iso_date


class MovementPrefix(Sequence):
    """Immutable view of an append-only movement log, without copying prefixes."""

    def __init__(self, items, length):
        self._items, self._length = items, length

    def __len__(self):
        return self._length

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self._items[i] for i in range(*index.indices(self._length)))
        if index < 0:
            index += self._length
        if not 0 <= index < self._length:
            raise IndexError(index)
        return self._items[index]

    def __eq__(self, other):
        if not isinstance(other, Sequence):
            return NotImplemented
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))


class AccountTimeline:
    """Advance once through ascending dates; retain at most 128 checkpoints.

    Older uncached requests restart the pure engine. Cash value dates do not
    advance state without an explicit receipt. Whole-journal validation remains
    separate from dated historical queries, including the last event date.
    """

    def __init__(self, journal, postings):
        from portfolio_app.account_journal import effective_events
        self.journal, self.postings = journal, postings
        self.dates = sorted({e.date for e in (*effective_events(journal), *postings)})
        self._states = OrderedDict()
        self._complete = False
        self._reset()

    def _reset(self):
        from portfolio_app.account_journal import _replay_states
        self._iterator = _replay_states(self.journal, _security_postings=self.postings, snapshots=True)
        self._date, self._state = next(self._iterator)
        self._complete = False
        self._failure = None

    def at(self, stamp=None):
        if stamp is not None:
            iso_date(stamp)
            index = bisect_right(self.dates, stamp)
            target = self.dates[index-1] if index else ''
            if target in self._states:
                self._states.move_to_end(target)
                return self._states[target]
            if target < self._date:
                self._reset()
        else:
            target = None
        if self._failure is not None and (target is None or self._date < target):
            raise self._failure
        while not self._complete and (target is None or self._date < target):
            try:
                self._date, self._state = next(self._iterator)
            except StopIteration:
                self._complete = True
                break
            except Exception as exc:
                self._failure = exc
                raise
            self._states[self._date] = self._state
            self._states.move_to_end(self._date)
            while len(self._states) > 128:
                self._states.popitem(last=False)
        return self._state
