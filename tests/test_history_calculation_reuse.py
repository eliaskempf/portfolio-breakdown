"""Synthetic cross-view and dated-gain oracles; no saved workspace input."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pandas as pd
import pytest

from portfolio_app.account_journal import Journal
from portfolio_app.allocation import Allocation, Bucket
from portfolio_app.gain_accounting import current_component_values, summarize_known_gain
from portfolio_app.gain_comparison import known_category_performance
from portfolio_app.gain_components import security_gain_components
from portfolio_app.history_observations import HistoryScope, Observation, ObservationStore
from portfolio_app.history_view import history_view
from portfolio_app.personal_performance import HistoryCalculation
from test_gain_accounting import book, observations, START, END, E
from test_history_account_reuse import mixed_journal


def test_chart_current_gain_and_categories_share_ledgers_but_not_valuation_basis(monkeypatch):
    import portfolio_app.personal_performance as performance
    journal = Journal(version=2, securities=(
        book(E('first', 'opening', START, quantity=10, amount=100, currency='EUR')),
        book(E('second', 'opening', START, quantity=5, amount=50, currency='EUR'), identity='other')))
    scope = HistoryScope(('stock', 'other'))
    obs = observations(journal, scope, [('stock', START, 10), ('stock', END, 12),
                                        ('other', START, 10), ('other', END, 8)])
    current = pd.DataFrame([
        dict(position_id='stock', name='Invented A', bucket_id='a', shares=10,
             current_value_reporting=130, unrealized_gain_reporting=30),
        dict(position_id='other', name='Invented B', bucket_id='b', shares=5,
             current_value_reporting=35, unrealized_gain_reporting=-15)])
    config = Allocation((Bucket('a', 'A'), Bucket('b', 'B')))
    expected = known_category_performance(current, current, config, journal, obs, current, today=date(2025, 1, 1))
    calls = []
    replay = performance.replay_securities
    def counted(*args, **kwargs):
        calls.append(kwargs.get('as_of'))
        return replay(*args, **kwargs)
    monkeypatch.setattr(performance, 'replay_securities', counted)
    calculation = HistoryCalculation(journal, obs)
    chart = history_view(journal, scope, obs, 'All', today=date(2025, 1, 1), calculation=calculation)
    values = current_component_values(current, journal, calculation=calculation)
    gain = summarize_known_gain(journal, scope, obs, END, current_values=values,
                                summary_rows=current, calculation=calculation)
    before = len(calls)
    result = known_category_performance(current, current, config, journal, obs, current,
        today=date(2025, 1, 1), calculation=calculation, current_values=values)
    pd.testing.assert_frame_equal(result, expected)
    assert len(calls) == before  # Comparison consumes the chart's dated states.
    assert chart.all_known.amount == 10 and chart.all_known.value == 160
    assert gain.amount == 15 and gain.value == 165  # Current quotes are distinct evidence.
    assert result.Gain.tolist() == [30, -15]


@pytest.mark.parametrize('target', ['EUR', 'USD'])
def test_recorded_gain_reuse_preserves_fee_dates_corrections_unknowns_and_daily_values(monkeypatch, target):
    import portfolio_app.gain_components as module
    journal = mixed_journal()
    obs = ObservationStore(tuple(
        Observation(f'p{day}', 'price', 'invented-stock', f'2026-01-{day:02}', D(10+day), 'EUR')
        for day in range(1, 13)) + tuple(
        Observation(f'fx{day}', 'fx', 'EUR/USD', f'2026-01-{day:02}', D(2), 'EUR', target='USD')
        for day in range(1, 13)))
    days = (12, 6, 1, 9, 5, 2, 7, 4, 11, 3, 8, 10)
    expected = {day: security_gain_components(journal, 'invented-stock', obs, f'2026-01-{day:02}', target)
                for day in days}
    recorded = module.recorded_security_gains
    calls = []
    def counted(*args):
        calls.append(args[3])
        return recorded(*args)
    monkeypatch.setattr(module, 'recorded_security_gains', counted)
    calculation = HistoryCalculation(journal, obs)
    for day in days:
        actual = security_gain_components(journal, 'invented-stock', obs, f'2026-01-{day:02}', target,
                                          calculation=calculation)
        assert actual == expected[day]
    assert len(calls) == 4  # Opening, purchase, sale/separate fee, unknown income.
    scale = 1 if target == 'EUR' else 2
    assert expected[3].purchase_payments == 20*scale
    assert expected[4].purchase_payments == 21*scale
    assert expected[4].remaining_cost == D('10.5')*scale
    assert expected[4].realized == D('1.5')*scale
    assert expected[9].unrealized - expected[8].unrealized == scale
    assert expected[9].income == 0 and expected[10].income is None
    # A different target and revised observations need separate facts.
    assert security_gain_components(journal, 'invented-stock', obs, '2026-01-09', 'GBP',
                                    calculation=calculation).remaining_cost is None
    missing_fx = replace(obs, observations=tuple(o for o in obs.observations if o.kind != 'fx'))
    assert security_gain_components(journal, 'invented-stock', missing_fx, '2026-01-09', 'USD',
                                    calculation=HistoryCalculation(journal, missing_fx)).purchase_payments is None


@pytest.mark.parametrize('target', ['EUR', 'USD'])
@pytest.mark.parametrize('account_scope', [False, True])
def test_flow_reuse_matches_fresh_intervals_with_opening_day_and_unknown_fx(target, account_scope):
    from portfolio_app.account_journal import Event
    from portfolio_app.personal_performance import external_flows
    journal = mixed_journal()
    journal = replace(journal, events=(*journal.events,
        Event('initial-funding', '2026-01-01', 'cash_flow', 'broker', 'EUR', D(20)),
        Event('later-funding', '2026-01-05', 'cash_flow', 'broker', 'EUR', D(30))))
    scope = HistoryScope(accounts=('broker',)) if account_scope else HistoryScope(('invented-stock',))
    observations = ObservationStore()
    calculation = HistoryCalculation(journal, observations)
    for start in ('2025-12-31', '2026-01-01', '2026-01-03', '2026-01-04'):
        for day in (12, 6, 9, 5, 7, 4, 11, 8, 10):
            end = f'2026-01-{day:02}'
            assert calculation.flows(scope, start, end, target) == external_flows(
                journal, scope, observations, start, end, target)


def test_daily_price_changes_do_not_rebuild_unchanged_flows(monkeypatch):
    import portfolio_app.personal_performance as module
    journal = mixed_journal()
    calculation = HistoryCalculation(journal, ObservationStore())
    scope = HistoryScope(('invented-stock',))
    original = module.external_flows
    calls = []
    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'external_flows', counted)
    for day in range(10, 29):
        flows, reasons = calculation.flows(scope, '2026-01-01', f'2026-01-{day:02}', 'EUR')
        assert flows and reasons  # The unknown charge remains unknown throughout.
    assert len(calls) == 1


def test_category_comparison_does_not_calculate_unused_annualized_returns(monkeypatch):
    import portfolio_app.personal_performance as performance
    journal = Journal(version=2, securities=(book(E('opening', 'opening', START,
                                                        quantity=10, amount=100, currency='EUR')),))
    scope = HistoryScope(('stock',))
    obs = observations(journal, scope, [('stock', START, 10), ('stock', END, 12)])
    rows = pd.DataFrame([dict(position_id='stock', name='Invented', bucket_id='a', shares=10,
                             current_value_reporting=120, unrealized_gain_reporting=20)])
    def unexpected(*args, **kwargs):
        raise AssertionError('Category comparison only displays gain and TWR')
    monkeypatch.setattr(performance, 'xirr', unexpected)
    result = known_category_performance(rows, rows, Allocation((Bucket('a', 'A'),)), journal, obs,
                                        rows, today=date(2025, 1, 1))
    assert result.Gain.tolist() == [20]
    assert result['Return (%)'].tolist() == pytest.approx([20])
