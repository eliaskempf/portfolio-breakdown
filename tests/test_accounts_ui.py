"""Normal-app futures entry with an explicitly invented portfolio."""
from streamlit.testing.v1 import AppTest
from portfolio_app.account_journal import load_journal, save_journal
from test_actual_portfolio import invented
from test_ui import by_label, activate


def launch_actual(tmp_path):
    return AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.ui import render_app
from portfolio_app.prices import PriceService, StaticProvider
root=Path({str(tmp_path)!r})
render_app(root, demo=False, ignore_selection=True, price_service=PriceService(StaticProvider(root/'demo_prices.json')))
''',default_timeout=20).run()


def test_existing_actual_account_normal_tabs_and_views(tmp_path):
    _,journal,_=invented(tmp_path)
    save_journal(tmp_path,journal,None)
    app=launch_actual(tmp_path)
    assert not app.exception
    assert app.session_state['main_tabs']=='Overview'
    for tab in ['Positions','Exposure','Futures','Overview','Rebalance']:
        activate(app,tab)
        assert not app.exception, [(e.message) for e in app.exception]
    activate(app,'Futures')
    for view in ['Cash & financing','Collateral','Marks & FX','Positions & transactions']:
        by_label(app.get('button_group'),'Account workspace').set_value(view).run()
        assert not app.exception
    assert not (tmp_path/'futures-sandbox.json').exists()


def test_add_future_from_normal_position_dialog(tmp_path):
    invented(tmp_path)
    app=launch_actual(tmp_path)
    activate(app,'Positions')
    by_label(app.button,'Add position').click().run()
    by_label(app.get('button_group'),'Position type').set_value('Future').run()
    assert not app.exception
    by_label(app.text_input,'Fill price').set_value('1000').run()
    by_label(app.text_input,'Account name').set_value('Invented futures account').run()
    by_label(app.selectbox,'Cash source').set_value('position-0').run()
    by_label(app.button,'Review changes').click().run()
    assert not app.exception
    by_label(app.button,'Save reviewed changes').click().run()
    assert not app.exception
    journal,_=load_journal(tmp_path)
    assert len(journal.positions)==1 and len(journal.cash_links)==1
    assert not (tmp_path/'futures-sandbox.json').exists()
    restarted=launch_actual(tmp_path)
    assert not restarted.exception
    assert 'Futures' in [t.label for t in restarted.tabs]


def test_normal_lifecycle_settlement_partial_close_roll_and_manual_tax(tmp_path):
    from datetime import date
    from portfolio_app.account_journal import replay
    _, journal, _ = invented(tmp_path)
    save_journal(tmp_path, journal, None)
    app = launch_actual(tmp_path)
    activate(app, 'Futures')

    def post():
        by_label(app.button, 'Review changes').click().run()
        assert not app.exception
        by_label(app.button, 'Save reviewed changes').click().run()
        assert not app.exception
        return replay(load_journal(tmp_path)[0])

    by_label(app.text_input, 'Settlement price').set_value('1010').run()
    state = post()
    assert state.cash['a', 'EUR'] == 20192
    by_label(app.selectbox, 'Action').set_value('Increase / reduce position').run()
    by_label(app.number_input, 'Signed trade quantity').set_value(-1).run()
    by_label(app.text_input, 'Execution price').set_value('1020').run()
    state = post()
    assert state.positions['p'].quantity == 1 and state.cash['a', 'EUR'] == 20292
    by_label(app.selectbox, 'Action').set_value('Roll position').run()
    by_label(app.date_input, 'New contract maturity').set_value(date(2027, 6, 18)).run()
    by_label(app.text_input, 'New contract execution price').set_value('1030').run()
    state = post()
    assert state.positions['p'].quantity == 0 and sum(p.quantity for p in state.positions.values()) == 1
    assert state.cash['a', 'EUR'] == 20392
    by_label(app.selectbox, 'Action').set_value('Tax payment / withholding').run()
    by_label(app.text_input, 'Amount (EUR)').set_value('25').run()
    state = post()
    assert state.cash['a', 'EUR'] == 20367
    restarted = launch_actual(tmp_path)
    assert not restarted.exception


def test_normal_list_opens_actual_position_in_account_workspace(tmp_path):
    from portfolio_app.positions import read_snapshot
    from portfolio_app.position_list import list_context, position_rows
    from portfolio_app.actual_portfolio import journal_positions
    from portfolio_app.portfolio import prepare_portfolio
    service, journal, _ = invented(tmp_path)
    save_journal(tmp_path, journal, None)
    snapshot = read_snapshot(tmp_path/'holdings.csv')
    valued = journal_positions(prepare_portfolio(snapshot.holdings, service),journal,service)
    assert next(row for row in position_rows(valued) if row['id']=='p')['quantity'] == 2
    app = launch_actual(tmp_path)
    activate(app, 'Positions')
    app.session_state['position_edit_open_request'] = dict(id='p',context=list_context(tmp_path/'holdings.csv'),revision=snapshot.revision,action='edit')
    app.run()
    assert not app.exception
    assert app.session_state['positions_workflow'] == 'Accounts & futures'
    assert by_label(app.selectbox, 'Contract position').value == 'p'
