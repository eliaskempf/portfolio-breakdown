"""Shared headlines use invented closed histories and no market network."""
from datetime import date
from decimal import Decimal as D
import json
import pytest

from portfolio_app.account_journal import Journal, save_journal
from portfolio_app.cost_basis import fingerprint
from portfolio_app.history_observations import CoverageReview, HistoryScope, ObservationStore, save_observations
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import Charge, SecurityBook, SecurityEvent as E, history_fingerprint
from test_ui import launch, by_label, activate


@pytest.mark.parametrize('remove_current_row', [False, True])
def test_fully_liquidated_history_has_shared_gain_without_current_quote(tmp_path, remove_current_row):
    (tmp_path/'holdings.csv').write_text('position_key,id,name,shares,acquisition_price,acquisition_currency\nclosed,synthetic,Invented closed investment,0,,\n')
    (tmp_path/'demo_prices.json').write_text(json.dumps({'prices':{},'fx':{}}))
    row=read_snapshot(tmp_path/'holdings.csv').holdings.iloc[0]
    events=(E('o','opening','2024-01-01'),
        E('b','buy','2024-01-02',quantity=10,price=10,currency='EUR',charges=(Charge(D(2),'EUR'),),charges_known=True),
        E('s','sell','2024-07-01',quantity=10,price=12,currency='EUR',charges=(Charge(D(1),'EUR'),),charges_known=True),
        E('d','distribution','2024-08-01',amount=5,currency='EUR',charges_known=True))
    b=SecurityBook('closed','synthetic',tuple(fingerprint(row)),records=events,active_ids=tuple(e.id for e in events))
    j=Journal(version=2,securities=(b,));scope=HistoryScope(('closed',))
    save_journal(tmp_path,j,None)
    save_observations(tmp_path,ObservationStore(reviews=(CoverageReview('r',scope,'2024-01-01',date.today().isoformat(),history_fingerprint(j)),)),None)
    if remove_current_row:
        (tmp_path/'holdings.csv').write_text('position_key,id,name,shares,acquisition_price,acquisition_currency\n')
    app=launch(tmp_path);activate(app,'Overview')
    assert not app.exception
    assert by_label(app.get('button_group'),'Overview view').value=='Performance'
    by_label(app.get('button_group'),'Period').set_value('All').run()
    assert by_label(app.metric,'Portfolio value').value=='€0.00'
    assert '+€22.00' in by_label(app.metric,'Portfolio value').delta
    assert by_label(app.metric,'Invested capital').value=='€-22.00'
    assert by_label(app.metric,'Annualized return').value!='—'
    by_label(app.get('button_group'),'Overview view').set_value('Allocation').run()
    assert not app.exception
    assert '+€22.00' in by_label(app.metric,'Current value').delta
    by_label(app.selectbox,'Category').set_value('unassigned').run()
    assert not app.exception
    assert '+€22.00' in by_label(app.metric,'Current value').delta
