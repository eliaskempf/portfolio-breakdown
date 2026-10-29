"""Period controls use synthetic observations and never invent endpoint prices."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from portfolio_app.account_journal import Journal
from portfolio_app.history_observations import HistoryScope, Observation, ObservationStore, CoverageReview
from portfolio_app.history_view import history_view, period_start
from portfolio_app.security_history import SecurityBook, SecurityEvent, history_fingerprint


def fixture():
    book=SecurityBook('holding','invented',('10','10','EUR'),
        records=(SecurityEvent('open','opening','2024-01-01',quantity=D(10),amount=D(100),currency='EUR'),),active_ids=('open',))
    journal=Journal(version=2,securities=(book,));scope=HistoryScope(('holding',))
    prices=(('2024-01-01','10'),('2024-06-07','11'),('2024-06-10','12'),('2024-06-14','13'))
    store=ObservationStore(tuple(Observation(str(i),'price','holding',stamp,D(price),'EUR') for i,(stamp,price) in enumerate(prices)),
        (CoverageReview('review',scope,'2024-01-01','2024-06-16',history_fingerprint(journal)),))
    return journal,scope,store


def test_calendar_periods_and_available_history_limit():
    earliest=date(2020,1,1)
    assert period_start('1M',date(2024,3,31),earliest)==date(2024,2,29)
    assert period_start('1Y',date(2024,2,29),earliest)==date(2023,2,28)
    assert period_start('YTD',date(2025,3,1),earliest)==date(2025,1,1)
    assert period_start('5Y',date(2024,3,1),earliest)==earliest
    assert period_start('All',date(2024,3,1),earliest)==earliest


def test_now_axis_uses_latest_observation_without_weekend_fill():
    journal,scope,store=fixture()
    view=history_view(journal,scope,store,'All',today=date(2024,6,16))
    assert view.today=='2024-06-16' and view.end=='2024-06-14'
    assert view.result.ending_value==130
    assert len(view.points)==4
    assert '2024-06-08' not in {p['Date'] for p in view.points}
    assert '2024-06-16' not in {p['Date'] for p in view.points}
    assert view.result.money_weighted is not None


def test_period_shortens_window_and_incomplete_input_date_remains_gap():
    journal,scope,store=fixture()
    other=SecurityBook('other','other-invented',('1','10','EUR'),records=(SecurityEvent('other-open','opening','2024-01-01',quantity=D(1),amount=D(10),currency='EUR'),),active_ids=('other-open',))
    journal=replace(journal,securities=(*journal.securities,other));scope=HistoryScope(('holding','other'))
    store=replace(store,observations=(*store.observations,Observation('other-price','price','other','2024-06-07',D(20),'EUR'),Observation('other-price-end','price','other','2024-06-14',D(21),'EUR')))
    view=history_view(journal,scope,store,'1M',today=date(2024,6,16))
    assert view.requested_start=='2024-05-16' and view.start=='2024-06-07'
    assert next(p for p in view.points if p['Date']=='2024-06-10')['Value'] is None
    assert view.missing == (('2024-06-10', ('other: missing price on 2024-06-10',)),)
    assert view.result.money_weighted is None  # No complete coverage for the combined scope.


def test_full_history_return_and_hover_basis_do_not_change_with_chart_period():
    journal,scope,store=fixture()
    full=history_view(journal,scope,store,'All',today=date(2024,6,16))
    month=history_view(journal,scope,store,'1M',today=date(2024,6,16))
    assert month.all_result == full.all_result
    assert month.all_start == full.all_start == '2024-01-01'
    assert full.result.gain == D(30)
    assert month.result.gain == D(20)
    assert month.points[-1]['Total gain'] == 30
    assert month.points[-1]['Invested capital'] == 100
    by_date={p['Date']:p for p in full.points}
    for point in month.points:
        if point['Date'] in by_date:
            assert point == by_date[point['Date']]


def test_missing_opening_price_does_not_shorten_all_history_return():
    journal,scope,store=fixture()
    store=replace(store,observations=store.observations[1:])
    view=history_view(journal,scope,store,'1M',today=date(2024,6,16))
    assert view.useful
    assert view.result.money_weighted is not None
    assert view.all_start == '2024-01-01'
    assert view.all_result.money_weighted is None
    assert view.all_result.opening_value is None
    assert all(p['Invested capital'] == 100 for p in view.points)
    assert view.points[-1]['Total gain'] == 30


def test_snapshot_only_is_not_useful_history_and_no_prehistory_zero_is_invented():
    journal,scope,store=fixture()
    store=replace(store,observations=store.observations[:1])
    view=history_view(journal,scope,store,'All',today=date(2024,6,16))
    assert not view.useful
    assert len(view.points) == 1
    assert view.points[0]['Date'] == '2024-01-01'
    assert view.points[0]['Value'] == 100


def test_shorter_coverage_review_cannot_enable_all_history_return():
    journal,scope,store=fixture()
    store=replace(store,reviews=(replace(store.reviews[0],start='2024-06-01'),))
    view=history_view(journal,scope,store,'1M',today=date(2024,6,16))
    assert view.result.money_weighted is not None
    assert view.all_result.money_weighted is None
    assert view.all_result.flow_reasons


def test_ranges_covering_entire_holding_preserve_full_history_boundaries():
    journal, scope, store = fixture()
    store = replace(store, observations=store.observations[1:])
    for period in ('YTD', '1Y', '3Y', '5Y', 'All'):
        view = history_view(journal, scope, store, period, today=date(2024, 6, 16))
        assert view.covers_full_history
        assert view.start == view.all_start == '2024-01-01'
        assert view.result == view.all_result
        assert view.all_known.amount == 30
        assert view.result.time_weighted is None  # Missing opening price cannot be bypassed.
    short = history_view(journal, scope, store, '1M', today=date(2024, 6, 16))
    assert not short.covers_full_history
    assert short.result.gain == 20


@pytest.mark.parametrize('snapshot', [True, False])
def test_long_range_headline_shows_total_known_gain(tmp_path, snapshot):
    from portfolio_app.account_journal import save_journal
    from portfolio_app.history_observations import save_observations
    from streamlit.testing.v1 import AppTest
    journal, scope, store = fixture()
    book = journal.securities[0]
    journal = replace(journal, securities=(replace(book, records=(replace(book.records[0], amount=D(80)),)),))
    store = replace(store, reviews=(replace(store.reviews[0], journal_fingerprint=history_fingerprint(journal)),))
    if not snapshot:
        events = (SecurityEvent('open', 'opening', '2024-01-01'),
                  SecurityEvent('buy', 'buy', '2024-01-02', quantity=D(10), price=D(8), currency='EUR', charges_known=True))
        journal = replace(journal, securities=(replace(book, records=events, active_ids=('open', 'buy')),))
        store = replace(store, reviews=(), observations=(*store.observations,
            Observation('buy-price', 'price', 'holding', '2024-01-02', D(10), 'EUR')))
    save_journal(tmp_path, journal, None)
    save_observations(tmp_path, store, None)
    app = AppTest.from_string(f'''
from pathlib import Path
from datetime import date
import streamlit as st
from portfolio_app.account_journal import load_journal
from portfolio_app.history_observations import load_observations, HistoryScope
from portfolio_app.history_view import history_view
from portfolio_app.history_ui import _render_history_results
root = Path({str(tmp_path)!r})
journal, _ = load_journal(root)
store, revision = load_observations(root)
scope = HistoryScope(('holding',))
period = st.session_state.get('test_period', '1Y')
view = history_view(journal, scope, store, period, today=date(2024, 6, 16))
_render_history_results(root, journal, scope, key='test_', include_inputs=False,
    prepared=dict(view=view, observations=store, revision=revision))
''').run()
    for period in ('1Y', '5Y', 'All', '1M'):
        app.segmented_control[0].set_value(period).run()
        assert not app.exception
        assert app.metric[0].delta == ('+€20.00' if period == '1M' and snapshot else '—' if period == '1M' else '+€50.00')
        assert (app.metric[2].value != '—') == snapshot
        if not snapshot:
            assert any(c.value == 'History not confirmed' for c in app.caption)


@pytest.mark.parametrize('reviewed', [True, False])
def test_chart_reuses_ledger_states_without_changing_dated_results(monkeypatch, reviewed):
    import portfolio_app.personal_performance as performance
    from portfolio_app.gain_accounting import summarize_known_gain
    journal, scope, store = fixture()
    if not reviewed:
        store = replace(store, reviews=())
    calls = []
    original = performance.replay_securities
    def counted(*args, **kwargs):
        calls.append(kwargs.get('as_of'))
        return original(*args, **kwargs)
    monkeypatch.setattr(performance, 'replay_securities', counted)
    view = history_view(journal, scope, store, 'All', today=date(2024, 6, 16))
    assert len(calls) == len(set(calls)), 'Each dated ledger is replayed only once per chart'
    for point in view.points:
        stamp = point['Date']
        independent = performance.calculate_history(journal, scope, store, view.all_start, stamp)
        known = summarize_known_gain(journal, scope, store, stamp)
        assert point['Value'] == independent.ending_value
        assert point['Total gain'] == known.amount
        assert point['Invested capital'] == known.capital
        assert point['TWR'] == independent.time_weighted
    assert view.result == performance.calculate_history(journal, scope, store, view.start, view.end)


@pytest.mark.parametrize('reviewed', [True, False])
def test_summary_omits_only_chart_points(reviewed):
    journal, scope, store = fixture()
    if not reviewed:
        store = replace(store, reviews=())
    full = history_view(journal, scope, store, '1M', today=date(2024, 6, 16))
    summary = history_view(journal, scope, store, '1M', today=date(2024, 6, 16), include_points=False)
    assert summary == replace(full, points=())


def test_chart_computes_annualized_return_only_for_headline_intervals(monkeypatch):
    import portfolio_app.personal_performance as performance
    journal, scope, store = fixture()
    calls = []
    original = performance.xirr
    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(performance, 'xirr', counted)
    view = history_view(journal, scope, store, 'All', today=date(2024, 6, 16))
    assert len(calls) == 1
    assert view.all_result.money_weighted is not None
    assert view.points[-1]['TWR'] == pytest.approx(.3)


def test_replay_reuses_price_only_dates_but_preserves_separate_fee_dates(monkeypatch):
    import portfolio_app.personal_performance as performance
    from portfolio_app.security_history import Charge, replay_securities
    events = (SecurityEvent('open', 'opening', '2024-01-01'),
        SecurityEvent('buy', 'buy', '2024-01-03', quantity=3, price=10, currency='EUR',
                      charges=(Charge(D(2), 'EUR', '2024-01-06'),), charges_known=True),
        SecurityEvent('sell', 'sell', '2024-01-09', quantity=1, price=12, currency='EUR', charges_known=True))
    book = SecurityBook('holding', 'invented', ('0', '', ''), records=events, active_ids=tuple(e.id for e in events))
    journal = Journal(version=2, securities=(book,))
    calculation = performance.HistoryCalculation(journal, ObservationStore())
    calls = []
    def counted(*args, **kwargs):
        calls.append(kwargs.get('as_of'))
        return replay_securities(*args, **kwargs)
    monkeypatch.setattr(performance, 'replay_securities', counted)
    # Deliberately ask out of order to catch future data leaking into earlier days.
    for day in (10, 8, 2, 5, 9, 4, 1, 7, 3, 6):
        stamp = f'2024-01-{day:02}'
        assert calculation.securities(stamp) == replay_securities(journal.securities, as_of=stamp)
    assert not calls  # Dated states advance one timeline, without full replays.
    assert calculation.securities('2024-01-05').positions['holding'].quantity == 3
    assert len(calculation.securities('2024-01-05').cash) == 1
    assert len(calculation.securities('2024-01-06').cash) == 2
    assert calculation.securities('2024-01-09').positions['holding'].quantity == 2
