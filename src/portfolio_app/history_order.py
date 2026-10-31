"""Review incomparable same-day chronology without reordering accepted history."""
from collections import defaultdict
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from portfolio_app.holdings import DataError
from portfolio_app.security_history import affected_positions, selected_events


class OrderReviewRequired(DataError):
    def __init__(self, groups):
        self.groups = groups
        super().__init__('Review same-day transactions: dependent entries need distinct explicit sequence values.')


def domain(event):
    if event.order_domain:
        return event.order_domain
    if event.timestamp_utc:
        return 'utc'
    if event.source.startswith('transaction_csv'):
        return 'legacy-csv'
    if event.source.startswith('consors_pdf:'):
        return 'legacy-pdf'
    return 'manual'


def order_conflicts(journal, books):
    accepted = {i for b in journal.securities for i in b.active_ids}
    days = defaultdict(list)
    boundaries = defaultdict(list)
    for book in books:
        for event in selected_events(book):
            if event.kind in ('opening', 'checkpoint', 'note', 'cost'):
                if event.kind in ('opening', 'checkpoint'):
                    boundaries[book.position_id, event.date, event.sequence].append((book, event))
                continue
            if (book.custody_id.startswith('aggregate:') and event.kind == 'transfer'
                    and event.other_position_id == book.position_id and event.charges_known
                    and not event.asset_charges and not event.charges):
                continue
            resources = {('position', p) for p in affected_positions(book, event)}
            if book.account_id:
                resources.add(('account', book.account_id))
            days[event.date].append((book, event, resources))
    conflicts = [tuple(group) for group in boundaries.values() if len(group) > 1
                 and any(e.id not in accepted for _, e in group)]
    for entries in days.values():
        parents = list(range(len(entries)))
        def root(i):
            while parents[i] != i:
                parents[i] = parents[parents[i]]
                i = parents[i]
            return i
        owners = {}
        for i, (_, _, resources) in enumerate(entries):
            for resource in resources:
                if resource in owners:
                    parents[root(i)] = root(owners[resource])
                owners[resource] = i
        groups = defaultdict(list)
        for i, (book, event, _) in enumerate(entries):
            groups[root(i)].append((book, event))
        for group in groups.values():
            if len(group) < 2 or all(e.id in accepted for _, e in group):
                continue
            if (len({domain(e) for _, e in group}) != 1
                    or len({e.sequence for _, e in group}) != len(group)):
                conflicts.append(tuple(group))
    return tuple(conflicts)


def validate_order(journal, books):
    conflicts = order_conflicts(journal, books)
    if conflicts:
        raise OrderReviewRequired(conflicts)


def apply_order(journal, books, overrides):
    """Append reviewed corrections for saved records; keep originals byte-for-byte."""
    books = tuple(books)
    groups = order_conflicts(journal, books)
    if not overrides or not groups:
        return books
    eligible = {e.id for group in groups for _, e in group}
    if not eligible <= overrides.keys():
        raise OrderReviewRequired(groups)
    for group in groups:
        ranks = [overrides[e.id] for _, e in group]
        if any(type(n) is not int or n < 1 for n in ranks) or len(set(ranks)) != len(ranks):
            raise OrderReviewRequired(groups)
    retained = {e.id for b in journal.securities for e in b.records}
    result = []
    for book in books:
        records, active = list(book.records), list(book.active_ids)
        for index, event in enumerate(book.records):
            if event.id not in eligible:
                continue
            rank = overrides[event.id]
            sequence = 86400000000000000+rank if event.kind in ('opening', 'checkpoint') else rank
            reviewed = replace(event, sequence=sequence, order_domain='reviewed')
            if event.id in retained:
                reviewed = replace(reviewed, id=uuid5(NAMESPACE_URL, f'order:{event.id}:{rank}').hex,
                                   replaces=event.id)
                records.append(reviewed)
                active[active.index(event.id)] = reviewed.id
            else:
                records[index] = reviewed
        result.append(replace(book, records=tuple(records), active_ids=tuple(active)))
    return tuple(result)


def original_order_ids(identities, overrides):
    """Map displayed ordering corrections back to the proposals being reviewed."""
    origins = {uuid5(NAMESPACE_URL, f'order:{identity}:{rank}').hex: identity
               for identity, rank in overrides.items()}
    return {origins.get(identity, identity) for identity in identities}


def editable_order_ids(books, candidates, overrides):
    origins = {uuid5(NAMESPACE_URL, f'order:{identity}:{rank}').hex: identity
               for identity, rank in overrides.items()}
    candidates = set(candidates)
    return frozenset(e.id for book in books for e in book.records
                     if origins.get(e.id, e.id) in candidates)
