"""Futures investments entered through the normal app, using invented holdings."""
from datetime import date
from decimal import Decimal

from portfolio_app.account_journal import InvestmentSpec, load_journal, replay, save_journal
from portfolio_app.futures_investments import assign_investment
from portfolio_app.investment_ui import investment_review_rows
from test_accounts_ui import launch_actual
from test_actual_portfolio import invented
from test_ui import activate, by_label


def test_normal_entry_allocates_existing_cash_to_named_investment(tmp_path):
    invented(tmp_path)
    app = launch_actual(tmp_path)
    activate(app, 'Positions')
    by_label(app.button, 'Add position').click().run()
    by_label(app.get('button_group'), 'Position type').set_value('Future').run()
    maturity = by_label(app.selectbox, 'Exact contract maturity')
    maturity.select_index(0).run()
    by_label(app.text_input, 'Fill price').set_value('1000').run()
    by_label(app.text_input, 'Account name').set_value('Invented investment account').run()
    by_label(app.selectbox, 'Cash source').set_value('position-0').run()
    by_label(app.text_input, 'Investment name').set_value('Invented index investment').run()
    by_label(app.text_input, 'Initial allocated capital (EUR)').set_value('20000').run()
    by_label(app.button, 'Review changes').click().run()
    assert not app.exception
    by_label(app.button, 'Save reviewed changes').click().run()
    assert not app.exception
    journal, _ = load_journal(tmp_path)
    investment = journal.investments[0]
    assert investment.name == 'Invented index investment'
    assert investment.reference_capital == investment.allocated_capital == Decimal('20000')
    assert journal.positions[0].investment_id == investment.id
    assert replay(journal).cash[investment.account_id, 'EUR'] == Decimal('20000')
    app = launch_actual(tmp_path)
    activate(app, 'Futures')
    assert not app.exception
    assert by_label(app.selectbox, 'Futures investment').value == investment.id
    assert by_label(app.metric, 'Investment capital value').value == '€20,000.00'
    assert by_label(app.metric, 'Net P&L since recorded start').value == '+€0.00'


def test_existing_contract_adoption_and_funding_edit_keep_cash_and_gain(tmp_path):
    _, journal, _ = invented(tmp_path)
    save_journal(tmp_path, journal, None)
    app = launch_actual(tmp_path)
    activate(app, 'Futures')
    by_label(app.toggle, 'Group an existing contract history as an investment').set_value(True).run()
    by_label(app.text_input, 'Initial allocated capital at recorded start (EUR)').set_value('15000').run()
    by_label(app.button, 'Review investment changes').click().run()
    assert not app.exception
    by_label(app.button, 'Save investment changes').click().run()
    assert not app.exception
    grouped, _ = load_journal(tmp_path)
    investment = grouped.investments[0]
    assert grouped.positions[0].investment_id == investment.id
    assert grouped.events == journal.events
    assert by_label(app.metric, 'Investment capital value').value == '€14,992.00'
    assert by_label(app.metric, 'Net P&L since recorded start').value == '-€8.00'
    by_label(app.toggle, 'Edit investment allocation').set_value(True).run()
    by_label(app.text_input, 'Allocated principal (EUR)').set_value('18000').run()
    by_label(app.button, 'Review investment changes').click().run()
    assert investment_review_rows(journal, grouped)[0]['Allocated principal after change'] == '15000.00'
    by_label(app.text_input, 'Allocated principal (EUR)').set_value('19000').run()
    assert not any(button.label == 'Save investment changes' for button in app.button)
    by_label(app.button, 'Review investment changes').click().run()
    by_label(app.button, 'Save investment changes').click().run()
    changed, _ = load_journal(tmp_path)
    assert not app.exception
    assert changed.investments[0].reference_capital == Decimal('15000')
    assert changed.investments[0].allocated_capital == Decimal('19000')
    assert changed.events == journal.events
    assert by_label(app.metric, 'Investment capital value').value == '€18,992.00'
    assert by_label(app.metric, 'Net P&L since recorded start').value == '-€8.00'


def test_roll_keeps_investment_and_group_request_opens_active_contract(tmp_path):
    _, journal, _ = invented(tmp_path)
    investment = InvestmentSpec('investment:synthetic', 'Invented rolling index', 'a', 'EUR', 20000, 20000)
    journal = assign_investment(journal, investment, ['p'])
    save_journal(tmp_path, journal, None)
    app = launch_actual(tmp_path)
    activate(app, 'Futures')
    by_label(app.selectbox, 'Action').set_value('Roll position').run()
    by_label(app.text_input, 'Execution price').set_value('1050').run()
    by_label(app.date_input, 'New contract maturity').set_value(date(2027, 6, 18)).run()
    by_label(app.text_input, 'New contract execution price').set_value('1060').run()
    by_label(app.button, 'Review changes').click().run()
    by_label(app.button, 'Save reviewed changes').click().run()
    assert not app.exception
    rolled, _ = load_journal(tmp_path)
    assert len(rolled.investments) == 1
    assert len(rolled.positions) == 2
    assert {p.investment_id for p in rolled.positions} == {investment.id}
    assert by_label(app.metric, 'Investment capital value').value == '€20,992.00'
    assert by_label(app.metric, 'Net P&L since recorded start').value == '+€992.00'
    app.session_state['accounts_requested_position'] = investment.id
    app.run()
    assert not app.exception
    assert by_label(app.selectbox, 'Contract position').value == rolled.positions[-1].id
    by_label(app.selectbox, 'Contract position').set_value('p').run()
    assert by_label(app.selectbox, 'Contract position').value == 'p'
    assert by_label(app.selectbox, 'Futures investment').value == investment.id
    histories = [frame.value for frame in app.dataframe if {'Status', 'Maturity', 'Contracts'} <= set(frame.value)]
    assert len(histories) == 1
    assert histories[0].Status.tolist() == ['Closed', 'Open']


def test_overview_positions_keep_capital_values_when_allocation_measure_changes(tmp_path):
    import pandas as pd
    from test_futures_investments import grouped
    from list_helpers import list_frame, list_data
    _,journal,_=grouped(tmp_path,capital=5000)
    save_journal(tmp_path,journal,None)
    app=launch_actual(tmp_path)
    by_label(app.get('button_group'),'Allocation measure').set_value('Market exposure').run()
    exposure_mode=list_frame(app,'Positions')
    assert exposure_mode.set_index('id').loc['investment:synthetic','value']==4992
    columns=list_data(app,'Positions')['columns']
    assert next(c['label'] for c in columns if c['key']=='value')=='Value (EUR)'
    by_label(app.get('button_group'),'Allocation measure').set_value('Capital allocation').run()
    assert not app.exception
    pd.testing.assert_frame_equal(list_frame(app,'Positions'),exposure_mode)
    assert list_data(app,'Positions')['columns']==columns
