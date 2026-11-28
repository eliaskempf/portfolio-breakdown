"""Exact CSV and applied journal defaults in the real history editors."""
from dataclasses import replace
from decimal import Decimal as D

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.ledger.account_journal import load_journal, save_journal
from portfolio_app.ledger.security_history import SecurityEvent, replay_securities
from test_history_ui import label, launch
from test_legacy_purchase_recovery import content, opening, purchase, v1_batch


@pytest.mark.parametrize('grid', [False, True])
def test_starting_balance_preserves_exact_quantity_and_dated_costs(tmp_path, grid):
    q = '2.00000000000000001'
    avg = '9.9999999999999999500000000000000002500000000000000'
    path = tmp_path/'holdings.csv'
    path.write_text(content([v1_batch('reconcile', opening(q, '8'), [purchase()], q, avg)]))
    before = path.read_bytes()
    if grid:
        app = AppTest.from_string(f"""from pathlib import Path
from portfolio_app.interface.views.history.history_ui import _fix_history
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.ledger.history_commands import book_for
from portfolio_app.portfolio.positions import read_snapshot
path = Path({str(tmp_path)!r})
journal, revision = load_journal(path)
snapshot = read_snapshot(path/'holdings.csv')
row = snapshot.holdings.iloc[0]
_fix_history(path, journal, revision, snapshot, book_for(journal, row), row, 'grid')
""").run()
        assert app.dataframe[0].value.iloc[0]['Quantity'] == q
        label(app.button, 'Save evidence').click().run()
    else:
        app = launch(tmp_path)
        label(app.button, 'Add starting balance').click().run()
        assert label(app.text_input, 'Units held').value == q
        assert D(label(app.text_input, 'Total acquisition cost').value) == 20
        label(app.button, 'Save starting balance').click().run()
    assert not app.exception and path.read_bytes() == before
    journal, revision = load_journal(tmp_path)
    book, = journal.securities
    event, = book.records
    assert event.quantity == D(q) and event.amount == 20 and event.lots == ()
    assert [(D(p['shares']), p['date'], D(p['amount'])) for p in event.costs] == [(D(q), '2024-01-02', D(20))]

    if not grid:
        # The current journal may contain a purchase absent from the editable CSV.
        buy = SecurityEvent('add', 'buy', '2025-01-02', quantity=D('0.25000000000000001'),
                            amount=D(4), currency='USD', charges_known=True)
        applied = replace(book, records=(replace(event, date='2025-01-01'), buy), active_ids=(event.id, buy.id))
        save_journal(tmp_path, replace(journal, securities=(applied,)), revision)
        assert replay_securities((applied,)).positions['test'].quantity == D('2.25000000000000002')
        app = launch(tmp_path)
        label(app.button, 'Replace balance').click().run()
        assert label(app.text_input, 'Total quantity').value == '2.25000000000000002'
        assert D(label(app.text_input, 'Total acquisition cost').value) == 24
        assert not app.exception


@pytest.mark.parametrize('also_known', [False, True])
def test_opening_defaults_keep_lots_with_no_recorded_basis_unknown(tmp_path, also_known):
    from portfolio_app.ledger.account_journal import Journal
    from portfolio_app.ledger.security_history import SecurityBook, SecurityLot, effective_holdings, opening_defaults
    from portfolio_app.performance.cost_basis import active_components, component, resolve_cost
    from portfolio_app.portfolio.holdings import parse_holdings

    holdings = parse_holdings('id,name,shares\ninvented,Invented,7\n')
    lots = (SecurityLot('unknown', D(1), date='2024-01-01'),)
    if also_known:
        lots += (SecurityLot('known', D(2), (component(2, 6, 'EUR'),), date='2024-01-01'),)
    quantity = sum(lot.quantity for lot in lots)
    event = SecurityEvent('opening', 'opening', '2024-01-02', quantity=quantity, lots=lots)
    book = SecurityBook('position-0', 'invented', ('7', '', ''), records=(event,), active_ids=(event.id,))
    defaults = opening_defaults(Journal(version=5, securities=(book,)), book, holdings)
    assert defaults['quantity'] == quantity and defaults['amount'] is None
    projected = effective_holdings(holdings, (book,)).iloc[0]
    assert resolve_cost(active_components(projected), 'EUR').amount is None
