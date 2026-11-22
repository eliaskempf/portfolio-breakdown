"""Repeatable two-year portfolio. No tickers, real accounts or provider access."""
import csv
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from portfolio_app.ledger.account_journal import Journal, save_journal
from portfolio_app.ledger.history_commands import book_for, reconstruction_candidate
from portfolio_app.ledger.security_history import SecurityEvent
from portfolio_app.performance.history_observations import Observation, ObservationStore, save_observations
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.workspace.display_preferences import save_overview_view


def create_workspace(directory: Path):
    directory.mkdir()
    today = date.today()
    start = today - timedelta(days=730)
    def day(offset):
        return (start + timedelta(days=offset)).isoformat()

    columns = ('position_key id name ticker shares acquisition_price acquisition_currency bucket_id '
               'instrument_type exposure_kind quantity_unit manual_price manual_price_currency manual_price_date')
    with (directory / 'holdings.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(columns.split())
        for index in range(12):
            identity = f'invented-{index}'
            writer.writerow([identity, identity, f'Invented holding {index}', '', 25, 100, 'EUR',
                f'group{index % 3}', 'other', 'non_equity', 'units', 110 + index, 'EUR', today.isoformat()])
    (directory / 'allocation.yaml').write_text('version: 2\nbuckets:\n' + ''.join(
        f'- id: group{i}\n  name: Invented group {i}\n  target: {target}\n'
        for i, target in enumerate((.4, .3, .3))), encoding='utf-8')
    rows = read_snapshot(directory / 'holdings.csv').holdings
    journal, books, observations = Journal(version=2), [], []
    for index, (_, row) in enumerate(rows.iterrows()):
        book = book_for(journal, row)
        buys = tuple(SecurityEvent(f'b-{index}-{t}', 'buy', day(t * 30), quantity=Decimal(1),
            price=Decimal(100), currency='EUR', charges_known=True) for t in range(25))
        books.append(reconstruction_candidate(replace(book, records=buys),
            tuple(event.id for event in buys), starts_at_zero=True))
        observations.extend(Observation(f'o-{index}-{t}', 'price', book.position_id, day(t),
            100 + Decimal((10 + index) * t) / 730, 'EUR', source='invented') for t in range(731))
    save_journal(directory, replace(journal, securities=tuple(books)), None)
    save_observations(directory, ObservationStore(tuple(observations)), None)
    save_overview_view(directory, 'Allocation')
