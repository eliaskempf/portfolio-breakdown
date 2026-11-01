"""Shared position headlines use invented histories and offline charts only."""
from dataclasses import replace
import json
import pytest

from streamlit.testing.v1 import AppTest

from portfolio_app.account_journal import save_journal
from portfolio_app.history_observations import HistoryScope, save_observations
from portfolio_app.personal_performance import calculate_history
from portfolio_app.presentation import return_percent
from test_personal_performance import journal, observed
from test_ui import by_label


def launch(tmp_path, *, history=True, complete=True, estimated=False):
    (tmp_path/'holdings.csv').write_text('position_key,id,name,ticker,shares,acquisition_price,acquisition_currency\n'
        'holding,invented,Invented holding,SYNTH,15,5.333333333333333,EUR\n')
    j, scope = journal(), HistoryScope(('holding',))
    book = j.securities[0]
    j = replace(j, version=5, securities=(replace(book, records=tuple(
        replace(e, estimated=estimated) if e.kind == 'buy' else e for e in book.records)),))
    store = observed(j, scope)
    if not complete:
        store = replace(store, reviews=())
    if history:
        save_journal(tmp_path, j, None)
        save_observations(tmp_path, store, None)
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.position_detail import render_position_detail
from portfolio_app.positions import read_snapshot
path = Path({str(tmp_path)!r})
row = read_snapshot(path/'holdings.csv').holdings.assign(
    current_value_reporting=300., unrealized_gain_reporting=220., cost_basis_reporting=80., return_pct=275.).iloc[0]
render_position_detail(row, path, demo=True)
''', default_timeout=15).run()
    return app, calculate_history(j, scope, store, '2024-01-01', '2025-01-01')


def headlines(app):
    assert not app.exception
    return [(m.label, m.value, m.delta) for m in app.metric]


@pytest.mark.parametrize('estimated', [False, True])
def test_personal_headlines_are_identical_across_charts_ranges_and_tabs(tmp_path, estimated):
    app, result = launch(tmp_path, estimated=estimated)
    expected = [('Position value', '€300.00', '+€220.00'), ('Invested capital', '€80.00', ''),
                ('Annualized return', return_percent(100*result.money_weighted), '')]
    assert headlines(app) == expected
    assert ('estimates' in by_label(app.metric, 'Position value').proto.help) == estimated
    assert ('Estimated return' in by_label(app.metric, 'Annualized return').proto.help) == estimated
    assert not any(c.value.startswith('Estimated') for c in app.caption)
    by_label(app.get('button_group'), 'History type').set_value('Price').run()
    assert headlines(app) == expected
    by_label(app.get('button_group'), 'Period').set_value('1M').run()
    assert headlines(app) == expected
    by_label(app.get('button_group'), 'History type').set_value('Holdings').run()
    by_label(app.get('button_group'), 'Period').set_value('All').run()
    assert headlines(app) == expected
    figure = json.loads(app.get('plotly_chart')[0].proto.spec)
    assert figure['layout']['meta']['historyInspection']['metrics'] == []
    assert 'Total gain:' in figure['data'][0]['text'][-1]
    assert 'TWR:' in figure['data'][0]['text'][-1]
    by_label(app.get('button_group'), 'Position detail view').set_value('Details').run()
    assert headlines(app) == expected
    details = app.dataframe[0].value.set_index('Position').Details
    assert details['Return on cost'] == return_percent(275)
    assert details['Unrealized gain'] == '+€220.00'


def test_summary_only_uses_recorded_cost_and_leaves_annualized_return_unknown(tmp_path):
    app, _ = launch(tmp_path, history=False)
    assert headlines(app) == [('Position value', '€300.00', '+€220.00'),
        ('Invested capital', '€80.00', ''), ('Annualized return', '—', '')]
    assert not any(c.label == 'History type' for c in app.get('button_group'))
    assert any(c.value == 'Unrealized gain only' for c in app.caption)


def test_applied_history_shows_gain_and_return_without_confirmation(tmp_path):
    app, result = launch(tmp_path, complete=False)
    before = headlines(app)
    assert before[0] == ('Position value', '€300.00', '+€220.00')
    assert before[2] == ('Annualized return', return_percent(100*result.money_weighted), '')
    by_label(app.get('button_group'), 'History type').set_value('Price').run()
    assert headlines(app) == before
    assert not any(c.value == 'History not confirmed' for c in app.caption)
