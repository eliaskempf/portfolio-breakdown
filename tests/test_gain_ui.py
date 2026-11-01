"""Shared headlines use invented closed histories and no market network."""
from datetime import date
from dataclasses import replace
from decimal import Decimal as D
import json
import pytest

from portfolio_app.account_journal import Journal, save_journal
from portfolio_app.cost_basis import fingerprint
from portfolio_app.history_observations import CoverageReview, HistoryScope, ObservationStore, save_observations
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import Charge, SecurityBook, SecurityEvent as E, history_fingerprint
from test_ui import launch, by_label, activate


def test_allocation_explains_full_history_twr_gap_even_when_recent_return_is_known(tmp_path):
    from portfolio_app.history_observations import Observation
    (tmp_path/'holdings.csv').write_text(
        'position_key,id,name,ticker,shares,acquisition_price,acquisition_currency\n'
        'synthetic-position,synthetic-asset,Invented investment,SYNTH,2,100,EUR\n')
    (tmp_path/'demo_prices.json').write_text(json.dumps({'prices': {
        'SYNTH':dict(price=110, currency='EUR', observed_at='2026-09-01T12:00:00+00:00')}, 'fx':{}}))
    row = read_snapshot(tmp_path/'holdings.csv').holdings.iloc[0]
    events = (E('open', 'opening', '2024-01-01', quantity=1, amount=100, currency='EUR'),
              E('buy', 'buy', '2024-01-04', quantity=1, price=100, currency='EUR', charges_known=True))
    book = SecurityBook('synthetic-position', 'synthetic-asset', tuple(fingerprint(row)), records=events,
                        active_ids=tuple(e.id for e in events))
    save_journal(tmp_path, Journal(version=2, securities=(book,)), None)
    save_observations(tmp_path, ObservationStore(tuple(Observation(str(i), 'price', 'synthetic-position', stamp, price, 'EUR')
        for i,(stamp,price) in enumerate([('2024-01-01',100), ('2025-01-01',105), (date.today().isoformat(),110)]))), None)
    app = launch(tmp_path)
    activate(app, 'Overview')
    by_label(app.get('button_group'), 'Overview view').set_value('Allocation').run()
    unit_key = next(k for k in app.session_state._state.filtered_state if k.startswith('performance_unit_'))
    app.session_state[unit_key] = '%'
    app.run()
    assert not app.exception
    metric = by_label(app.metric, 'Current value')
    assert metric.delta == '—'
    assert '2024-01-03' in metric.proto.help and 'Invented investment' in metric.proto.help
    assert 'TWR: all recorded history' in metric.proto.help
    assert 'Open Performance' in metric.proto.help


@pytest.mark.parametrize('remove_current_row', [False, True])
@pytest.mark.parametrize('estimated', [False, True])
def test_fully_liquidated_history_has_shared_gain_without_current_quote(tmp_path, remove_current_row, estimated):
    (tmp_path/'holdings.csv').write_text('position_key,id,name,shares,acquisition_price,acquisition_currency\nclosed,synthetic,Invented closed investment,0,,\n')
    (tmp_path/'demo_prices.json').write_text(json.dumps({'prices':{},'fx':{}}))
    row=read_snapshot(tmp_path/'holdings.csv').holdings.iloc[0]
    events=(E('o','opening','2024-01-01'),
        E('b','buy','2024-01-02',quantity=10,price=10,currency='EUR',charges=(Charge(D(2),'EUR'),),charges_known=True),
        E('s','sell','2024-07-01',quantity=10,price=12,currency='EUR',charges=(Charge(D(1),'EUR'),),charges_known=True),
        E('d','distribution','2024-08-01',amount=5,currency='EUR',charges_known=True))
    events = tuple(replace(e, estimated=estimated) if e.kind == 'buy' else e for e in events)
    b=SecurityBook('closed','synthetic',tuple(fingerprint(row)),records=events,active_ids=tuple(e.id for e in events))
    j=Journal(version=5,securities=(b,));scope=HistoryScope(('closed',))
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
    assert ('estimates' in by_label(app.metric, 'Portfolio value').proto.help) == estimated
    assert ('Estimated return' in by_label(app.metric, 'Annualized return').proto.help) == estimated
    assert not any(c.value.startswith('Estimated') for c in app.caption)
    by_label(app.get('button_group'),'Overview view').set_value('Allocation').run()
    assert not app.exception
    assert '+€22.00' in by_label(app.metric,'Current value').delta
    assert ('estimates' in by_label(app.metric, 'Current value').proto.help) == estimated
    assert not any(c.value == 'Estimated gain' for c in app.caption)
    # A new Streamlit session (browser refresh) restores the explicit view.
    reopened = launch(tmp_path)
    activate(reopened, 'Overview')
    assert not reopened.exception
    assert by_label(reopened.get('button_group'), 'Overview view').value == 'Allocation'
    assert '+€22.00' in by_label(reopened.metric, 'Current value').delta
    by_label(app.selectbox,'Category').set_value('unassigned').run()
    assert not app.exception
    assert '+€22.00' in by_label(app.metric,'Current value').delta
