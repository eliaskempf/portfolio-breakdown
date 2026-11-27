"""Invented older-format inputs and current history survive complete restoration."""
import csv
from dataclasses import replace
from decimal import Decimal
import json

import pandas as pd
import pytest

from portfolio_app.ledger.account_journal import Journal, load_journal, save_journal
from portfolio_app.performance.cost_basis import active_components, resolve_cost
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.workspace.backup import commit_restore, create_backup, inspect_backup
from portfolio_app.ledger.history_commands import correct_transaction, save_history
from portfolio_app.performance.history_observations import Observation, ObservationStore, save_observations
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.portfolio.purchases import read_purchase_history
from portfolio_app.ledger.security_history import SecurityBook, SecurityEvent, effective_holdings, replay_securities
from portfolio_app.workspace.workspace import inventory, validate_workspace


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


@pytest.mark.parametrize(('quantity', 'price', 'saved_quantity', 'saved_price'), [
    # Actual fingerprints from the parsers shipped in v0.2.1 and used during v0.3.0 development.
    ('2', '12.0100000000000009', '2', '12.01'),
    ('0.30000000000000004', '0.34567890123456789', '0.3', '0.3456789012345678'),
    ('0.30000000000000004', '0.01234567890123456789', '0.3', '0.0123456789012345'),
    ('0.30000000000000004', '0.01234567890123456789', '0.30000000000000004', '0.012345678901234568'),
    ('2', '2388.0313341626393', '2', '2388.0313341626393'),
])
def test_upgrade_reads_history_fingerprint_from_unchanged_csv(tmp_path, quantity, price, saved_quantity, saved_price):
    path = tmp_path / 'holdings.csv'
    path.write_text(
        'id,name,position_key,shares,acquisition_price,acquisition_currency,manual_price,manual_price_currency,manual_price_date,quantity_unit\n'
        f'invented,Invented units,invented-position,{quantity},{price},EUR,15,EUR,2025-01-01,units\n')
    opening = SecurityEvent('opening', 'opening', '2025-01-01', quantity=Decimal(saved_quantity),
                            amount=Decimal(saved_quantity) * Decimal(saved_price), currency='EUR')
    book = SecurityBook('invented-position', 'invented', (saved_quantity, saved_price, 'EUR'),
                        records=(opening,), active_ids=(opening.id,))
    save_journal(tmp_path, Journal(version=5, securities=(book,)), None)
    before = inventory(tmp_path)
    validate_workspace(tmp_path)
    assert inventory(tmp_path) == before
    path.write_text(path.read_text().replace(f',{price},EUR', ',17.25,EUR'))
    with pytest.raises(DataError, match='outside History'):
        validate_workspace(tmp_path)


@pytest.mark.parametrize('saved_price', ['12.01', '12.010000000000002'])
def test_upgrade_and_restore_preserve_dated_costs_and_supplied_fx(tmp_path, saved_price):
    source = tmp_path / 'invented-costs'
    source.mkdir()
    path = source / 'holdings.csv'
    parts = [
        dict(shares='1', amount='12', currency='USD', date='2025-01-01',
             conversions={'EUR': dict(amount='10', method='supplied_cost')}),
        dict(shares='1', amount='12.02', currency='USD', date='2025-02-01',
             conversions={'EUR': dict(amount='11', method='supplied_cost')}),
    ]
    details = json.dumps(dict(version=1, summary=['2', saved_price, 'USD'], components=parts))
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['id', 'name', 'shares', 'acquisition_price', 'acquisition_currency', 'cost_basis_details'])
        writer.writerow(['invented', 'Invented dated costs', '2', '12.0100000000000009', 'USD', details])
    before = inventory(source)
    restored = restore(source, tmp_path / 'restored-invented-costs')
    for directory in [source, restored]:
        row = read_snapshot(directory / 'holdings.csv').holdings.iloc[0]
        recovered = active_components(row)
        assert recovered == parts
        assert resolve_cost(recovered, 'EUR').exact_amount == Decimal('21')
        assert inventory(directory) == before


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
