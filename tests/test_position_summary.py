"""Shared position headlines use invented histories and offline charts only."""
from dataclasses import replace
import json
import pytest

from streamlit.testing.v1 import AppTest

from portfolio_app.ledger.account_journal import save_journal
from portfolio_app.performance.history_observations import HistoryScope, save_observations
from portfolio_app.performance.personal_performance import calculate_history
from portfolio_app.interface.shared.presentation import return_percent
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
from portfolio_app.interface.views.positions.position_detail import render_position_detail
from portfolio_app.portfolio.positions import read_snapshot
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


@pytest.mark.parametrize('retired_by', ['another-position', 'workspace-switch'])
def test_retired_history_selector_ignores_queued_callback(tmp_path, monkeypatch, retired_by):
    from copy import deepcopy
    from portfolio_app.interface.views.positions.position_ui import request_position
    app, _ = launch(tmp_path)
    by_label(app.get('button_group'), 'History type').set_value('Price').run()
    assert app.session_state['position_history_preferences'] == {'holding': 'Price'}
    widget = by_label(app.get('button_group'), 'History type')
    metadata = app.session_state._state._state._new_widget_state.widget_metadata[widget.proto.id]
    state = dict(position_edit_selected='holding', position_edit_history_mode_holding='Price',
                 position_history_preferences={'holding': 'Price', 'other': 'Holdings'})
    monkeypatch.setattr('portfolio_app.interface.views.positions.position_detail.st.session_state', state)
    if retired_by == 'another-position':
        request_position('other', local=False)
    else:
        state.clear()
    assert 'position_edit_history_mode_holding' not in state
    before = deepcopy(state)
    # Streamlit snapshots changed callbacks before dispatch. An earlier opener
    # can retire this widget before its already-queued callback is invoked.
    metadata.callback(*(metadata.callback_args or ()), **(metadata.callback_kwargs or {}))
    assert state == before


def test_headline_watcher_updates_only_on_new_evidence_or_day(tmp_path, monkeypatch):
    from datetime import date, timedelta
    import streamlit as st
    from portfolio_app.interface.shared import background_ui, dialog_runtime, input_cache
    from portfolio_app.performance import position_summary
    from portfolio_app.performance.history_observations import FILE

    calls, reads, intervals = [], [], []
    active = [True]
    revision = ['first']
    def register(*, run_every):
        assert run_every == 2
        def capture(fn):
            calls.append(fn)
            return lambda: fn(intervals.append)
        return capture
    monkeypatch.setattr(background_ui, 'status_fragment', register)
    monkeypatch.setattr(dialog_runtime, 'local_fragment_updates', lambda: lambda fn: fn)
    def read(directory):
        reads.append(directory)
        return None, revision[0]
    monkeypatch.setattr(input_cache, 'observation_snapshot', read)
    class Rerender(Exception):
        pass
    def rerender():
        raise Rerender
    monkeypatch.setattr(st, 'rerun', rerender)

    today = date.today()
    path = tmp_path / FILE
    path.write_text('first', encoding='utf-8')
    position_summary._watch_summary_evidence(tmp_path, input_cache.file_stamp(path), revision[0], today, lambda: active[0])
    tick, = calls
    tick(intervals.append)
    assert not reads
    path.write_text('same revision', encoding='utf-8')
    tick(intervals.append)
    tick(intervals.append)
    assert reads == [tmp_path]
    revision[0] = 'second'
    path.write_text('changed', encoding='utf-8')
    with pytest.raises(Rerender):
        tick(intervals.append)
    active[0] = False
    tick(intervals.append)
    assert intervals == [None] and reads == [tmp_path, tmp_path]
    active[0] = True
    with pytest.raises(Rerender):
        position_summary._watch_summary_evidence(tmp_path, input_cache.file_stamp(path), revision[0],
                                                 today - timedelta(days=1), lambda: True)


def test_headline_owner_survives_chart_switches_and_retires_with_the_dialog(monkeypatch):
    import streamlit as st
    from portfolio_app.interface.views.positions.position_detail import _position_view_guard
    state = dict(portfolio_workspace_context='first', position_edit_selected='invented',
                 position_edit_action='Details', position_edit_dialog=True)
    monkeypatch.setattr(st, 'session_state', state)
    active = _position_view_guard('invented', include_view=False)
    original_chart = _position_view_guard('invented')
    state['position_edit_history_mode_invented'] = 'Price'
    assert active() and not original_chart()
    for key, replacement in [('position_edit_selected', 'other'), ('position_edit_action', 'Edit position'),
                             ('portfolio_workspace_context', 'second'), ('position_edit_dialog', False)]:
        before = state[key]
        state[key] = replacement
        assert not active()
        state[key] = before
