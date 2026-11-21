"""Purchase-first form behavior with invented holdings only."""
from datetime import date

from streamlit.testing.v1 import AppTest

from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.ledger.security_history import effective_holdings
from portfolio_app.portfolio.positions import read_snapshot
from test_ui import by_label


def editor(directory):
    app = AppTest.from_string(f'''from pathlib import Path
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.interface.views.positions.position_ui import render_position_editor
path = Path({str(directory / 'holdings.csv')!r})
render_position_editor(path, read_snapshot(path), [])
''').run()
    by_label(app.button, 'Add position').click().run()
    return app


def test_new_position_defaults_to_purchase_and_saves_trade(tmp_path):
    app = editor(tmp_path)
    assert by_label(app.get('button_group'), 'Entry method').value == 'New purchase'
    assert by_label(app.date_input, 'Trade date').value == date.today()
    by_label(app.button, 'Enter manually').click().run()
    by_label(app.text_input, 'Instrument name').set_value('Invented simple purchase')
    by_label(app.text_input, 'Quantity bought').set_value('2.5')
    by_label(app.text_input, 'Unit price').set_value('100')
    by_label(app.text_input, 'Fees').set_value('3').run()
    assert any('253.00 EUR' in c.value for c in app.caption)
    by_label(app.button, 'Save purchase').click().run()
    assert not app.exception and not app.error
    journal = load_journal(tmp_path)[0]
    holdings = effective_holdings(read_snapshot(tmp_path/'holdings.csv').holdings, journal.securities)
    assert holdings.iloc[0].shares == 2.5
    assert holdings.iloc[0].acquisition_price == 101.2
    assert len(journal.securities[0].active_ids) == 2


def test_snapshot_then_new_trade_is_one_save_without_history_review(tmp_path):
    app = editor(tmp_path)
    by_label(app.get('button_group'), 'Entry method').set_value('Existing holding').run()
    by_label(app.button, 'Enter manually').click().run()
    by_label(app.text_input, 'Instrument name').set_value('Invented starting holding')
    by_label(app.text_input, 'Quantity held (total)').set_value('10')
    by_label(app.text_input, 'Average buy-in per unit (optional)').set_value('90')
    by_label(app.date_input, 'Holding as of').set_value(date(2026, 1, 1)).run()
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    assert not load_journal(tmp_path)[0].securities
    identity = read_snapshot(tmp_path/'holdings.csv').holdings.iloc[0].id
    by_label(app.button, 'Add position').click().run()
    by_label(app.selectbox, 'Existing instrument').set_value(identity).run()
    assert by_label(app.selectbox, 'Purchase destination').value
    by_label(app.text_input, 'Quantity bought').set_value('2')
    by_label(app.text_input, 'Unit price').set_value('100')
    by_label(app.text_input, 'Fees').set_value('1').run()
    by_label(app.button, 'Save purchase').click().run()
    assert not app.exception and not app.error
    journal = load_journal(tmp_path)[0]
    holdings = effective_holdings(read_snapshot(tmp_path/'holdings.csv').holdings, journal.securities)
    assert len(holdings) == 1 and holdings.iloc[0].shares == 12
    assert holdings.iloc[0].acquisition_price == 1101/12


def test_existing_transactions_have_direct_buy_sell_and_secondary_backfill(tmp_path):
    from test_history_ui import fixture, launch
    fixture(tmp_path)
    app = launch(tmp_path)
    by_label(app.button, 'Add buy').click().run()
    by_label(app.text_input, 'Quantity bought').set_value('2')
    by_label(app.text_input, 'Unit price').set_value('20').run()
    by_label(app.button, 'Save purchase').click().run()
    assert not app.exception and not app.error
    journal = load_journal(tmp_path)[0]
    assert len(journal.securities[0].active_ids) == 2
    by_label(app.button, 'Add sell').click().run()
    by_label(app.text_input, 'Quantity sold').set_value('1')
    by_label(app.text_input, 'Unit price').set_value('22').run()
    by_label(app.button, 'Save sale').click().run()
    assert not app.exception and not app.error
    journal = load_journal(tmp_path)[0]
    assert effective_holdings(read_snapshot(tmp_path/'holdings.csv').holdings, journal.securities).iloc[0].shares == 11
    by_label(app.button, 'Enter earlier transactions').click().run()
    assert any(b.label == 'Save transaction' for b in app.button)


def test_new_trade_rejects_changed_journal(tmp_path):
    from test_history_ui import fixture, launch
    from portfolio_app.ledger.account_journal import save_journal
    from dataclasses import replace
    fixture(tmp_path)
    app = launch(tmp_path)
    by_label(app.button, 'Add buy').click().run()
    by_label(app.text_input, 'Quantity bought').set_value('2')
    by_label(app.text_input, 'Unit price').set_value('20').run()
    journal, revision = load_journal(tmp_path)
    save_journal(tmp_path, replace(journal, version=5), revision)
    by_label(app.button, 'Save purchase').click().run()
    assert not app.exception
    assert any('changed' in w.value for w in app.warning)
    assert len(load_journal(tmp_path)[0].securities[0].records) == 1


def test_applied_trade_correction_updates_holding_with_one_save(tmp_path):
    from portfolio_app.ledger.trade_commands import save_trade
    from test_trade_commands import trade
    from test_history_ui import launch
    identity = save_trade(tmp_path, trade(), fields=dict(name='Invented correction'),
        expected_holdings_revision=None, expected_journal_revision=None)
    app = launch(tmp_path)
    from portfolio_app.interface.views.history.history_ui import _key
    key = _key(tmp_path, identity)
    app.session_state[key+'screen'] = 'edit'
    app.session_state[key+'edit_record'] = 'buy-1'
    app.run()
    by_label(app.text_input, 'Unit price').set_value('120').run()
    by_label(app.button, 'Save changes').click().run()
    assert not app.exception and not app.error
    journal = load_journal(tmp_path)[0]
    row = effective_holdings(read_snapshot(tmp_path/'holdings.csv').holdings, journal.securities).iloc[0]
    assert row.shares == 2.5 and row.acquisition_price == 121.2
    assert 'buy-1' not in journal.securities[0].active_ids
    assert len(journal.securities[0].records) == 3


def test_first_purchase_date_correction_moves_only_synthetic_boundary(tmp_path):
    from portfolio_app.ledger.trade_commands import save_trade
    from test_trade_commands import trade
    from test_history_ui import launch
    identity = save_trade(tmp_path, trade(), fields=dict(name='Invented dated purchase'),
        expected_holdings_revision=None, expected_journal_revision=None)
    app = launch(tmp_path)
    from portfolio_app.interface.views.history.history_ui import _key
    key = _key(tmp_path, identity)
    app.session_state[key+'screen'] = 'edit'
    app.session_state[key+'edit_record'] = 'buy-1'
    app.run()
    by_label(app.date_input, 'Transaction date').set_value(date(2025, 12, 10)).run()
    by_label(app.button, 'Save changes').click().run()
    assert not app.exception and not app.error
    from portfolio_app.ledger.security_history import selected_events
    events = selected_events(load_journal(tmp_path)[0].securities[0])
    assert [e.date for e in events] == ['2025-12-09', '2025-12-10']


def test_sale_taxes_reduce_linked_cash_once_and_remain_editable(tmp_path):
    from dataclasses import replace
    from decimal import Decimal as D
    from portfolio_app.ledger.account_journal import Account, Event, replay, save_journal
    from portfolio_app.ledger.security_history import selected_events
    from portfolio_app.interface.views.history.history_ui import _key
    from test_history_ui import launch
    from test_trade_commands import trade, save

    save(tmp_path, trade(stamp='2024-01-02', quantity='10', price='10', fee='0'),
        fields=dict(name='Invented taxed sale'))
    journal, revision = load_journal(tmp_path)
    book = replace(journal.securities[0], account_id='cash')
    save_journal(tmp_path, replace(journal, version=5, securities=(book,),
        accounts=(Account('cash', 'Invented cash account'),),
        events=(Event('opening-cash', '2024-01-01', 'cash_open', 'cash', 'EUR', D(1000)),)), revision)
    app = launch(tmp_path)
    by_label(app.button, 'Add sell').click().run()
    assert by_label(app.text_input, 'Taxes paid / withheld').value == '0'
    by_label(app.text_input, 'Quantity sold').set_value('2')
    by_label(app.text_input, 'Unit price').set_value('20')
    by_label(app.text_input, 'Fees').set_value('1')
    by_label(app.text_input, 'Taxes paid / withheld').set_value('4').run()
    assert any('Net proceeds: 35.00 EUR' in c.value for c in app.caption)
    by_label(app.button, 'Save sale').click().run()
    assert not app.exception and not app.error
    journal = load_journal(tmp_path)[0]
    sale = selected_events(journal.securities[0])[-1]
    assert [(c.kind, c.amount) for c in sale.charges] == [('fee', D(1)), ('tax', D(4))]
    assert replay(journal).cash['cash', 'EUR'] == D(935)
    row = effective_holdings(read_snapshot(tmp_path/'holdings.csv').holdings, journal.securities).iloc[0]
    assert row.shares == 8 and row.acquisition_price == 10
    from portfolio_app.performance.gain_components import security_gain_components
    from portfolio_app.performance.history_observations import ObservationStore
    parts = security_gain_components(journal, book.position_id, ObservationStore(),
        date.today().isoformat(), current_value=D(80))
    assert (parts.remaining_cost, parts.realized, parts.expenses) == (D(80), D(15), D(0))
    key = _key(tmp_path, book.position_id)
    app.session_state[key+'screen'] = 'edit'
    app.session_state[key+'edit_record'] = sale.id
    app.run()
    by_label(app.text_input, 'Tax 2').set_value('5').run()
    by_label(app.button, 'Save changes').click().run()
    assert not app.exception and not app.error
    assert replay(load_journal(tmp_path)[0]).cash['cash', 'EUR'] == D(934)


def test_sale_requires_nonnegative_explicit_taxes_and_preserves_zero_for_correction(tmp_path):
    from test_history_ui import fixture, launch
    from portfolio_app.ledger.security_history import selected_events
    fixture(tmp_path)
    app = launch(tmp_path)
    by_label(app.button, 'Add sell').click().run()
    by_label(app.text_input, 'Quantity sold').set_value('1')
    by_label(app.text_input, 'Unit price').set_value('20')
    for invalid in ('-1', '', 'invalid'):
        by_label(app.text_input, 'Taxes paid / withheld').set_value(invalid).run()
        by_label(app.button, 'Save sale').click().run()
        assert app.error and not app.exception
        assert len(load_journal(tmp_path)[0].securities[0].active_ids) == 1
    by_label(app.text_input, 'Taxes paid / withheld').set_value('0').run()
    by_label(app.button, 'Save sale').click().run()
    assert not app.exception and not app.error
    sale = selected_events(load_journal(tmp_path)[0].securities[0])[-1]
    assert [(c.kind, c.amount) for c in sale.charges] == [('fee', 0), ('tax', 0)]
