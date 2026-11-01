"""Invented older-format inputs and current history survive complete restoration."""
import csv
from dataclasses import replace
from decimal import Decimal
import json

import pandas as pd

from portfolio_app.account_journal import load_journal
from portfolio_app.backup import commit_restore, create_backup, inspect_backup
from portfolio_app.history_commands import correct_transaction, save_history
from portfolio_app.history_observations import Observation, ObservationStore, save_observations
from portfolio_app.positions import read_snapshot
from portfolio_app.purchases import read_purchase_history
from portfolio_app.security_history import effective_holdings, replay_securities
from portfolio_app.workspace import inventory, validate_workspace


def restore(source, destination):
    draft = inspect_backup(create_backup(source))
    try:
        return commit_restore(draft, destination, source)
    finally:
        draft.close()


def test_legacy_holdings_and_purchase_batches_are_not_implicitly_activated(tmp_path):
    # These literal schemas were supported by v0.1.0. Do not generate this input
    # through current writers: a changed writer could otherwise mask a regression.
    source = tmp_path / 'invented-old-workspace'
    source.mkdir()
    batch = dict(version=2, batch_id='invented-batch', saved_at='2026-01-03T12:00:00+00:00',
                 mode='add', currency='EUR',
                 purchases=[dict(date='2026-01-02', shares='2', price='10', fees='1', fx_rate='')],
                 opening=dict(shares='0', acquisition_price='', acquisition_currency=''),
                 result_shares='2', result_average='10.5')
    row = dict(id='invented', name='Invented old-format asset', ticker='', isin='', shares='2',
               acquisition_price='10.5', acquisition_currency='EUR', portfolio='Invented portfolio',
               account='Invented account', bucket_id='invented-category', within_bucket_target='1',
               purchase_history=json.dumps([batch]), custom_label='Invented retained label')
    with (source / 'holdings.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    (source / 'portfolio.yaml').write_text('version: 1\nreporting_currency: EUR\n')
    (source / 'allocation.yaml').write_text(
        'version: 2\nbuckets:\n  - id: invented-category\n    name: Invented category\n'
        '    parent: ""\n    target: 1.0\n    sell_protected: false\n')
    (source / '.backups').mkdir()
    (source / '.backups' / 'invented-original.csv').write_bytes((source / 'holdings.csv').read_bytes())
    before = inventory(source)

    validate_workspace(source)
    snapshot = read_snapshot(source / 'holdings.csv')
    journal, revision = load_journal(source)
    assert not journal.securities and revision is None
    assert read_purchase_history(snapshot.holdings.iloc[0].purchase_history) == [batch]
    assert snapshot.holdings.iloc[0].shares == 2
    assert snapshot.holdings.iloc[0].acquisition_price == 10.5
    assert snapshot.holdings.iloc[0].bucket_id == 'invented-category'
    assert inventory(source) == before

    destination = restore(source, tmp_path / 'invented-old-restored')
    assert inventory(destination) == inventory(source) == before
    assert not (destination / 'account-journal.json').exists()
    pd.testing.assert_frame_equal(read_snapshot(destination / 'holdings.csv').holdings, snapshot.holdings)


def test_version_five_corrected_imports_closed_positions_and_observations_roundtrip(tmp_path):
    from test_transaction_import_commands import setup, save

    source = tmp_path / 'invented-current-workspace'
    source.mkdir()
    snapshot = setup(source)
    save(source, snapshot)
    journal, revision = load_journal(source)
    book = next(book for book in journal.securities if book.position_id == 'desk')
    original = next(event for event in book.records if event.kind == 'buy')
    corrected = correct_transaction(book, original.id, amount=Decimal('110'))
    replacement = corrected.records[-1]
    corrected = replace(corrected, active_ids=tuple(
        replacement.id if identity == original.id else identity for identity in book.active_ids))
    saved = save_history(source, corrected, expected_journal_revision=revision,
                         expected_holdings_revision=read_snapshot(source / 'holdings.csv').revision,
                         activate=True, accept_balance_change=True)
    assert saved.version == 5 and saved.transaction_imports == journal.transaction_imports
    assert replacement.replaces == original.id and original in saved.securities[0].records
    observations = ObservationStore((
        Observation('invented-price', 'price', 'desk', '2024-04-08', Decimal('20'), 'EUR'),
        Observation('invented-corrected-price', 'price', 'desk', '2024-04-08', Decimal('21'),
                    'EUR', replaces='invented-price'),
    ))
    save_observations(source, observations, None)
    holdings = read_snapshot(source / 'holdings.csv').holdings
    projected = effective_holdings(holdings, saved.securities)
    state = replay_securities(saved.securities)
    assert state.positions['vault'].quantity == 0
    before = inventory(source)

    destination = restore(source, tmp_path / 'invented-current-restored')
    assert inventory(destination) == inventory(source) == before
    restored, _ = load_journal(destination)
    assert restored == saved
    assert replay_securities(restored.securities) == state
    pd.testing.assert_frame_equal(
        effective_holdings(read_snapshot(destination / 'holdings.csv').holdings, restored.securities), projected)
