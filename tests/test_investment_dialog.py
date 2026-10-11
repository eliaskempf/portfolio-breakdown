"""Normal-dialog futures interactions with an entirely invented workspace."""
from datetime import date
from dataclasses import replace

import pytest

from portfolio_app.account_journal import load_journal, replay, save_journal
from portfolio_app.position_list import list_context
from portfolio_app.positions import read_snapshot
from test_accounts_ui import launch_actual
from test_futures_investments import grouped
from test_ui import activate, by_label


def open_investment(app, tmp_path, *, editing=False):
    activate(app,'Positions')
    snapshot=read_snapshot(tmp_path/'holdings.csv')
    app.session_state['position_edit_open_request']=dict(id='investment:synthetic',
        context=list_context(tmp_path/'holdings.csv'),revision=snapshot.revision,
        action='edit' if editing else 'open')
    app.run()
    assert not app.exception
    assert app.session_state['main_tabs']=='Positions'
    assert len(app.get('dialog'))==1


def start(tmp_path, *, short=False):
    _,journal,_=grouped(tmp_path)
    if short:
        journal=replace(journal,events=tuple(replace(e,quantity=-2) if e.id=='open' else e for e in journal.events))
    save_journal(tmp_path,journal,None)
    app=launch_actual(tmp_path)
    open_investment(app,tmp_path)
    return app,journal


def roll_inputs(app, *, closing='1050', opening='1060', quantity=2):
    by_label(app.get('button_group'),'Futures position view').set_value('Roll contracts').run()
    by_label(app.date_input,'Roll date').set_value(date(2026,1,5)).run()
    by_label(app.number_input,'Contracts to roll').set_value(quantity).run()
    by_label(app.text_input,'Closing execution price').set_value(closing).run()
    by_label(app.date_input,'New contract maturity').set_value(date(2027,6,18)).run()
    by_label(app.text_input,'Opening execution price').set_value(opening).run()
    assert not app.exception


def test_popup_matches_normal_details_and_exposure_stays_in_details(tmp_path):
    app,_=start(tmp_path)
    assert by_label(app.metric,'Capital value').value=='€19,992.00'
    assert by_label(app.metric,'Market exposure').value=='€20,000.00'
    assert by_label(app.get('button_group'),'Futures position view').value=='Contract history'
    by_label(app.button,'Close').click().run()
    assert not app.session_state['position_edit_dialog']
    open_investment(app,tmp_path,editing=True)
    assert by_label(app.get('button_group'),'Futures position view').value=='Edit allocation'


@pytest.mark.parametrize('short', [False,True])
def test_partial_roll_review_and_save_keeps_investment_history(tmp_path,short):
    app,original=start(tmp_path,short=short)
    roll_inputs(app,quantity=1)
    by_label(app.text_input,'Total roll fees (EUR)').set_value('12').run()
    by_label(app.button,'Review roll').click().run()
    assert not app.exception
    assert load_journal(tmp_path)[0]==original # Review never posts.
    reviews=[frame.value for frame in app.dataframe if 'Contract' in frame.value and 'Action' in frame.value]
    assert reviews and reviews[0].Contract.str.contains('2027-06-18').any()
    by_label(app.button,'Save roll').click().run()
    assert not app.exception
    saved,_=load_journal(tmp_path)
    state=replay(saved)
    sign=-1 if short else 1
    assert state.positions['p'].quantity==sign
    new=next(position for identity,position in state.positions.items() if identity!='p')
    assert new.quantity==sign
    assert {p.investment_id for p in saved.positions}=={'investment:synthetic'}
    assert state.cash['a','EUR']==20000-8-12+sign*500
    open_investment(app,tmp_path)
    histories=[frame.value for frame in app.dataframe if 'Maturity' in frame.value and 'Status' in frame.value]
    assert len(histories)==1 and len(histories[0])==2


def test_roll_changed_input_invalidates_review_and_bad_maturity_does_not_post(tmp_path):
    app,original=start(tmp_path)
    roll_inputs(app)
    by_label(app.button,'Review roll').click().run()
    assert by_label(app.button,'Save roll')
    by_label(app.text_input,'Opening execution price').set_value('1070').run()
    assert not any(button.label=='Save roll' for button in app.button)
    by_label(app.date_input,'New contract maturity').set_value(date(2027,3,19)).run()
    assert any('later than the current contract' in info.value for info in app.info)
    assert load_journal(tmp_path)[0]==original


def test_dialog_record_transaction_is_scoped_and_refreshes_after_save(tmp_path):
    app,_=start(tmp_path)
    by_label(app.get('button_group'),'Futures position view').set_value('Record transaction').run()
    by_label(app.text_input,'Settlement price').set_value('1010').run()
    by_label(app.button,'Review changes').click().run()
    by_label(app.button,'Save reviewed changes').click().run()
    assert not app.exception
    open_investment(app,tmp_path)
    assert by_label(app.metric,'Capital value').value=='€20,192.00'


def test_dialog_reloads_changed_journal_before_edit(tmp_path):
    from portfolio_app.account_journal import Event,append_events
    app,_=start(tmp_path)
    journal,revision=load_journal(tmp_path)
    journal=append_events(journal,[Event('external-tax','2026-01-03','tax',position_id='p',amount=25)])
    save_journal(tmp_path,journal,revision)
    app.run()
    assert not app.exception
    assert by_label(app.metric,'Capital value').value=='€19,967.00'


def test_pending_cash_in_dialog_is_limited_to_this_investment(tmp_path):
    from portfolio_app.account_journal import Event, PositionSpec, add_position, append_events
    _,journal,contract=grouped(tmp_path)
    journal=add_position(journal,PositionSpec('other','a',contract.id))
    journal=append_events(journal,[
        Event('mine','2026-01-03','fee',position_id='p',amount=5,value_date='2026-01-04'),
        Event('other-fee','2026-01-03','fee',position_id='other',amount=11,value_date='2026-01-04')])
    save_journal(tmp_path,journal,None)
    app=launch_actual(tmp_path)
    open_investment(app,tmp_path)
    by_label(app.get('button_group'),'Futures position view').set_value('Record transaction').run()
    action=by_label(app.selectbox,'Action')
    assert 'Roll position' not in action.options
    action.set_value('Receive pending cash').run()
    claim=by_label(app.selectbox,'Cash claim')
    assert claim.value=='mine' and len(claim.options)==1
    assert '5.00 EUR' in claim.options[0]
    assert load_journal(tmp_path)[0]==journal
