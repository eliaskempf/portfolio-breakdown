"""A financial cell from a legacy CSV can be viewed, edited and traded without drift."""
from decimal import Decimal
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.ledger.security_history import selected_events
from portfolio_app.performance.cost_basis import FIELD, active_components, component
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.portfolio.purchases import read_opening, read_purchase_history, save_purchase_batch
from test_instrument_ui import launch_editor
from test_trade_commands import save, trade
from test_ui import by_label


@pytest.fixture
def position(tmp_path):
    path = tmp_path / 'holdings.csv'
    batch = component('2', '15.00000000000000002', 'EUR', '2023-01-12')
    details = json.dumps(dict(version=1, summary=['2', '7.50000000000000001', 'EUR'], source_exact=True,
                             components=[batch]), indent=1)
    raw = dict(position_key='position-0', id='EXAMPLE', name='Invented', shares='2E 0',
               acquisition_price='7.50000000000000001e 0', acquisition_currency='EUR',
               target_allocation='1e 1%', holdings_confirmed_on='2024-01-01', cost_basis_details=details)
    pd.DataFrame([raw]).to_csv(path, index=False)
    return path, raw, batch


def test_metadata_edit_keeps_legacy_spelling_and_original_cost_document(position):
    path, raw, _ = position
    original = path.read_bytes()
    app = launch_editor(path, action='Edit position')
    assert not app.exception
    assert by_label(app.text_input, 'Quantity held (total)').value == '2'
    assert path.read_bytes() == original
    by_label(app.text_input, 'Instrument name').set_value('Renamed sample')
    by_label(app.button, 'Save position').click().run()
    assert not app.error and not app.exception
    saved = pd.read_csv(path, dtype=str, keep_default_na=False).iloc[0]
    assert saved['name'] == 'Renamed sample'
    for field in ('shares', 'acquisition_price', 'acquisition_currency', 'target_allocation', FIELD):
        assert saved[field] == raw[field]


def test_existing_tiny_quantity_stays_compact_and_exact(position):
    path, raw, _ = position
    raw.update(shares='1E -90', cost_basis_details='')
    pd.DataFrame([raw]).to_csv(path, index=False)
    app = launch_editor(path, action='Edit position')
    assert not app.exception
    assert by_label(app.text_input, 'Quantity held (total)').value == '1E-90'
    by_label(app.text_input, 'Instrument name').set_value('Tiny sample')
    by_label(app.button, 'Save position').click().run()
    assert not app.error and not app.exception
    saved = pd.read_csv(path, dtype=str, keep_default_na=False).iloc[0]
    assert saved['name'] == 'Tiny sample' and saved['shares'] == raw['shares']


def test_metadata_edit_keeps_dated_foreign_cost_when_new_fx_is_available(position, monkeypatch):
    path, raw, batch = position
    details = json.loads(raw[FIELD])
    details['summary'][2] = 'USD'
    details['components'][0]['currency'] = 'USD'
    raw.update(acquisition_currency='USD', cost_basis_details=json.dumps(details, indent=1))
    pd.DataFrame([raw]).to_csv(path, index=False)
    rate = SimpleNamespace(rate=0.7, observed_on=batch['date'], source='Synthetic rate')
    monkeypatch.setattr('portfolio_app.interface.views.workspace.currency_ui.historical_for',
                        lambda *a, **kw: SimpleNamespace(get=lambda *args: rate))
    app = launch_editor(path, action='Edit position')
    assert by_label(app.selectbox, 'Purchase conversion').value == 'Keep saved conversion'
    by_label(app.text_input, 'Instrument name').set_value('Renamed example')
    by_label(app.button, 'Save position').click().run()
    assert not app.error and not app.exception
    saved = pd.read_csv(path, dtype=str, keep_default_na=False).iloc[0]
    assert saved['name'] == 'Renamed example' and saved[FIELD] == raw[FIELD]


def test_legacy_batch_starts_from_exact_opening_and_keeps_dated_costs(position):
    path, _, batch = position
    snap = read_snapshot(path)
    opening = read_opening(path, snap.revision, 'position-0')
    assert active_components(opening) == [batch]
    opening['acquisition_price'] = '7.50000000000000002'
    assert active_components(opening)[0]['date'] == ''
    save_purchase_batch(path, {}, [dict(date='2025-01-01', shares='1', price='3', fees='0')],
                        currency='EUR', expected_revision=snap.revision, position_id='position-0')
    row = read_snapshot(path).holdings.iloc[0]
    entry, = read_purchase_history(row.purchase_history)
    assert Decimal(entry['opening']['shares']) == 2
    assert Decimal(entry['opening']['acquisition_price']) == Decimal('7.50000000000000001')
    assert active_components(row)[0] == batch
    assert Decimal(entry['result_average']) * 3 == Decimal('18.00000000000000002')


def test_trade_uses_exact_legacy_starting_lot_and_preserves_snapshot(position):
    path, _, batch = position
    original = path.read_bytes()
    save(path.parent, trade(), position_id='position-0')
    journal, _ = load_journal(path.parent)
    opening = selected_events(journal.securities[0])[0]
    assert opening.quantity == Decimal('2') and opening.costs == (batch,)
    assert path.read_bytes() == original
