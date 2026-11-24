"""Synthetic cross-view and dated-gain oracles; no saved workspace input."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pandas as pd
import pytest

from portfolio_app.ledger.account_journal import Journal
from portfolio_app.portfolio.allocation import Allocation, Bucket
from portfolio_app.performance.gain_accounting import current_component_values, summarize_known_gain
from portfolio_app.performance.gain_comparison import known_category_performance
from portfolio_app.performance.gain_components import security_gain_components
from portfolio_app.performance.history_observations import HistoryScope, Observation, ObservationStore
from portfolio_app.performance.history_view import history_view
from portfolio_app.performance.personal_performance import HistoryCalculation
from test_gain_accounting import book, observations, START, END, E
from test_history_account_reuse import mixed_journal


def test_account_flow_event_resolution_is_once_per_snapshot(monkeypatch):
    import portfolio_app.performance.personal_performance as module
    journal = mixed_journal()
    original, calls = module.effective_events, []
    def counted(value):
        calls.append(True)
        return original(value)
    monkeypatch.setattr(module, 'effective_events', counted)
    calculation = HistoryCalculation(journal, ObservationStore())
    scope = HistoryScope(accounts=('broker',))
    for day in range(1, 13):
        calculation.flows(scope, '2026-01-01', f'2026-01-{day:02}', 'EUR')
    assert len(calls) == 1


def test_standalone_negative_equity_scan_reuses_its_calculation(monkeypatch):
    import portfolio_app.performance.personal_performance as module
    journal = Journal(version=2, securities=(book(E('opening', 'opening', START, quantity=1, amount=10, currency='EUR')),))
    scope = HistoryScope(('stock',))
    obs = observations(journal, scope, [('stock', START, 10), ('stock', END, 12)])
    def unexpected(*args, **kwargs):
        raise AssertionError('The negative-equity check must use the shared timeline')
    monkeypatch.setattr(module, 'replay_securities', unexpected)
    assert module.calculate_history(journal, scope, obs, START, END).time_weighted == pytest.approx(.2)


def test_unrelated_fx_does_not_add_chart_points_or_missing_prices():
    journal = Journal(version=2, securities=(book(E('opening', 'opening', START, quantity=1, amount=10, currency='EUR')),))
    scope = HistoryScope(('stock',))
    obs = observations(journal, scope, [('stock', START, 10), ('stock', END, 12)])
    extra = Observation('unrelated', 'fx', 'USD/GBP', '2024-06-01', D('.8'), 'USD', target='GBP')
    first = history_view(journal, scope, obs, 'All', today=date(2025, 1, 1))
    other = history_view(journal, scope, replace(obs, observations=(*obs.observations, extra)), 'All', today=date(2025, 1, 1))
    assert first == other


def test_fx_dates_include_foreign_account_cash_and_inverse_rates():
    from portfolio_app.ledger.account_journal import Account, Event
    from portfolio_app.performance.history_valuation import scope_observations
    journal = Journal(accounts=(Account('a', 'Invented'),), events=(Event('open', START, 'cash_open', account_id='a', currency='USD', amount=100),))
    scope = HistoryScope(accounts=('a',))
    obs = ObservationStore((Observation('direct', 'fx', 'USD/EUR', START, '.9', 'USD', target='EUR'),
        Observation('inverse', 'fx', 'EUR/USD', END, '1', 'EUR', target='USD'),
        Observation('other', 'fx', 'GBP/JPY', '2024-06-01', '150', 'GBP', target='JPY')))
    assert [o.id for o in scope_observations(journal, scope, obs, 'EUR')] == ['direct', 'inverse']


def test_scoped_evidence_reads_only_selected_rows_after_indexing():
    from portfolio_app.performance.history_valuation import scope_observations, scope_observation_dates
    from test_daily_twr import example
    journal, scope, _ = example((), quantity=1)
    observations = (
        Observation('old', 'price', 'invented', START, 1, 'JPY'),
        Observation('inverse', 'fx', 'EUR/USD', START, 1, 'EUR', target='USD'),
        Observation('ignored', 'price', 'unrelated', START, 1, 'JPY'),
        Observation('boundary', 'value', scope.key, START, -1, 'EUR', side='after', boundary='flow'),
        Observation('price', 'price', 'SYNTH', END, 2, 'USD'),
        Observation('corrected', 'price', 'invented', START, 2, 'EUR', replaces='old'),
        Observation('obsolete-currency', 'fx', 'JPY/EUR', START, 1, 'JPY', target='EUR'))
    index = ObservationStore(observations).index
    traversals, reads = [], []

    class TrackedRows(tuple):
        def __iter__(self):
            traversals.append(True)
            return super().__iter__()

        def __getitem__(self, offset):
            result = super().__getitem__(offset)
            reads.append(result.id)
            return result

    index.active = TrackedRows(index.active)
    expected = ['inverse', 'boundary', 'price', 'corrected']
    assert [o.id for o in scope_observations(journal, scope, index, 'EUR')] == expected
    dates = scope_observation_dates(journal, scope, index, 'EUR')
    assert dates.chart_dates == {START, END} and dates.negative_equity_dates == dates.boundary_dates == {START}
    assert len(traversals) == 1 and set(reads) == set(expected)


def test_chart_current_gain_and_categories_share_ledgers_but_not_valuation_basis(monkeypatch):
    import portfolio_app.performance.personal_performance as performance
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
    import portfolio_app.performance.gain_components as module
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
    from portfolio_app.ledger.account_journal import Event
    from portfolio_app.performance.personal_performance import external_flows
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
            expected, issues = external_flows(journal, scope, observations, start, end, target)
            assert calculation.flows(scope, start, end, target) == (expected, issues)
            amount, reasons = calculation.dated_flows(scope, target).total(start, end, amount_needed=True)
            assert amount == (None if issues else sum((flow.amount for flow in expected), D(0)))
            assert [(str(r), type(r), getattr(r, 'kind', '')) for r in reasons] == [
                (str(r), type(r), getattr(r, 'kind', '')) for r in issues]


def test_daily_price_changes_do_not_rebuild_unchanged_flows(monkeypatch):
    import portfolio_app.performance.personal_performance as module
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


def test_dense_flow_conversion_and_retention_are_linear(monkeypatch):
    from datetime import timedelta
    import portfolio_app.performance.personal_performance as module
    start = date(2024, 1, 1)
    events = [E('zero', 'opening', start.isoformat())]
    events += [E(f'purchase-{i}', 'buy', (start+timedelta(days=i)).isoformat(),
        quantity=1, amount=10, currency='EUR', charges_known=True) for i in range(1,151)]
    journal = Journal(version=2,securities=(book(*events),))
    scope = HistoryScope(('stock',))
    original, materialized = module.external_flows, []
    def counted(*args, **kwargs):
        materialized.extend(kwargs['security_cash'])
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'external_flows', counted)
    calculation = HistoryCalculation(journal, ObservationStore())
    for i in range(1,151):
        flows, reasons = calculation.flows(scope,start.isoformat(),(start+timedelta(days=i)).isoformat(),'EUR')
        assert not reasons and sum(f.amount for f in flows) == 10*i
    assert len(materialized) == 150
    cached = calculation._position_flows['stock']
    assert sum(len(flows) for flows,_ in cached.buckets.values()) == 150
    for i in range(40):
        calculation.flows(HistoryScope((f'other-{i}',)),start.isoformat(),'2024-01-02','EUR')
    assert len(calculation._flows) == 32


def test_future_flow_fx_conflicts_and_nonflow_issues_do_not_leak_into_early_interval():
    from portfolio_app.performance.personal_performance import external_flows
    from portfolio_app.portfolio.holdings import DataError
    events = (E('zero','opening','2024-01-01'),
        E('buy','buy','2024-01-02',quantity=1,amount=10,currency='EUR',charges_known=True),
        E('unknown-origin','inventory_in','2024-01-03',quantity=1,flow_classification='unresolved'),
        E('foreign','buy','2024-01-04',quantity=1,amount=10,currency='USD',charges_known=True))
    journal = Journal(version=4,securities=(book(*events),))
    scope = HistoryScope(('stock',))
    obs = ObservationStore((Observation('fx-a','fx','USD/EUR','2024-01-04','.9','USD',target='EUR'),
        Observation('fx-b','fx','EUR/USD','2024-01-04','2','EUR',target='USD')))
    calculation = HistoryCalculation(journal,obs)
    for end in ('2024-01-02','2024-01-03'):
        assert calculation.flows(scope,'2024-01-01',end,'EUR') == external_flows(journal,scope,obs,'2024-01-01',end,'EUR')
    with pytest.raises(DataError):
        calculation.flows(scope,'2024-01-01','2024-01-04','EUR')
    assert not calculation.flows(scope,'2024-01-01','2024-01-02','EUR')[1]
    totals = calculation.dated_flows(scope, 'EUR')
    for needed in (True, False):
        amount, issues = totals.total('2024-01-01', '2024-01-03', amount_needed=needed)
        assert amount is None and issues
        for _ in range(2):
            with pytest.raises(DataError):
                totals.total('2024-01-01', '2024-01-04', amount_needed=needed)
        assert totals.total('2024-01-01', '2024-01-02', amount_needed=needed) == (10 if needed else None, ())


def test_category_comparison_does_not_calculate_unused_annualized_returns(monkeypatch):
    import portfolio_app.performance.personal_performance as performance
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


@pytest.mark.parametrize('summary', [False, True], ids=['category-return', 'history-status'])
def test_full_history_scalars_visit_only_required_closes(monkeypatch, summary):
    from datetime import timedelta
    from portfolio_app.performance.history_view import full_history_summary, full_history_time_weighted
    from test_daily_twr import example, trade

    start = date(2024, 1, 1)
    prices = [((start + timedelta(days=i)).isoformat(), 100+i) for i in range(100)]
    journal, scope, store = example((trade('buy', 'buy', '2024-02-01', 131),), quantity=1, prices=prices)
    today = start + timedelta(days=101)
    view = history_view(journal, scope, store, 'All', today=today)
    assert view.all_result.time_weighted is not None
    calculation = HistoryCalculation(journal, store)
    value = calculation.value
    dates = set()
    def observe(scope, stamp, target):
        dates.add(stamp)
        return value(scope, stamp, target)
    monkeypatch.setattr(calculation, 'value', observe)
    needed = {today.isoformat(), prices[-1][0], '2024-01-31', start.isoformat()}
    if summary:
        result = full_history_summary(calculation, scope, today, 'EUR')
        assert result.all_result == view.all_result and result.useful
        needed.add(prices[-2][0])
    else:
        assert full_history_time_weighted(calculation, scope, today, 'EUR') == view.all_result.time_weighted
    assert dates <= needed


@pytest.mark.parametrize('case', ['negative close', 'negative boundary', 'corrected boundary',
                                  'negative other currency', 'unvalued purchase', 'fully sold', 'no prices', 'single close'])
def test_full_history_scalars_preserve_eligibility(case):
    from portfolio_app.performance.history_view import full_history_summary, full_history_time_weighted
    from test_daily_twr import example, trade
    today = date(2024, 1, 10)
    events = ()
    if case == 'fully sold':
        events = (trade('sale', 'sell', '2024-01-07', 110),)
    elif case == 'unvalued purchase':
        events = (trade('buy', 'buy', '2024-01-09', 110),)
    prices = () if case == 'no prices' else (('2024-01-01', 100), ('2024-01-06', 110))
    if case == 'single close':
        prices = prices[:1]
    journal, scope, store = example(events, quantity=1, prices=prices)
    if 'negative' in case or case == 'corrected boundary':
        boundary = {} if case == 'negative close' else dict(side='after', boundary='invented-flow')
        negative = Observation('negative', 'value', scope.key, '2024-01-04', -1,
                               'USD' if case == 'negative other currency' else 'EUR', **boundary)
        extra = [negative]
        if case == 'corrected boundary':
            extra.append(replace(negative, id='correction', replaces='negative', amount=1))
        store = replace(store, observations=(*store.observations, *extra))
    view = history_view(journal, scope, store, 'All', today=today)
    summary = full_history_summary(HistoryCalculation(journal, store), scope, today, 'EUR')
    assert (summary.all_start, summary.all_end, summary.useful, summary.all_result) == (
        view.all_start, view.all_end, view.useful, view.all_result)
    if case in {'no prices', 'single close'}:
        assert not summary.useful
    expected = view.all_result.time_weighted
    assert (expected is not None) == (case in {'fully sold', 'corrected boundary', 'negative other currency', 'single close'})
    assert full_history_time_weighted(HistoryCalculation(journal, store), scope, today, 'EUR') == expected


def test_scalar_twr_still_detects_interior_conflicting_closing_fx():
    from portfolio_app.performance.history_view import full_history_time_weighted
    from portfolio_app.portfolio.holdings import DataError
    from test_daily_twr import example
    days = ('2024-01-01', '2024-01-04', '2024-01-10')
    journal, scope, store = example((), quantity=1, prices=[(day, 100) for day in days])
    store = replace(store, observations=tuple(replace(p, currency='USD') for p in store.observations) +
                    tuple(Observation('fx-'+day, 'fx', 'USD/EUR', day, 1, 'USD', target='EUR') for day in days) +
                    (Observation('inverse', 'fx', 'EUR/USD', days[1], 2, 'EUR', target='USD'),))
    with pytest.raises(DataError, match='Conflicting direct and inverse'):
        full_history_time_weighted(HistoryCalculation(journal, store), scope, date.fromisoformat(days[-1]), 'EUR')


def test_one_way_fx_closes_do_not_require_daily_scope_valuations(monkeypatch):
    from datetime import timedelta
    from portfolio_app.performance.history_view import full_history_time_weighted
    from test_daily_twr import example

    first = date(2024, 1, 1)
    days = [(first + timedelta(days=i)).isoformat() for i in range(90)]
    journal, scope, store = example((), quantity=2, prices=[(day, 100+i) for i, day in enumerate(days)])
    prices = tuple(replace(p, currency='USD') for p in store.observations)
    # Quotes change direction on alternate days. An unrelated reciprocal pair
    # and a before-flow USD quote cannot conflict with a USD closing rate.
    rates = tuple(
        Observation('fx-'+day, 'fx', 'USD/EUR', day, '.5', 'USD', target='EUR') if i % 2
        else Observation('fx-'+day, 'fx', 'EUR/USD', day, '2', 'EUR', target='USD')
        for i, day in enumerate(days))
    other = (Observation('gbp', 'fx', 'GBP/EUR', days[3], 1, 'GBP', target='EUR'),
             Observation('gbp-back', 'fx', 'EUR/GBP', days[3], 3, 'EUR', target='GBP'),
             Observation('before', 'fx', 'EUR/USD', days[3], 3, 'EUR', target='USD',
                         side='before', boundary='invented'))
    store = replace(store, observations=(*prices, *rates, *other))
    calculation = HistoryCalculation(journal, store)
    today = date.fromisoformat(days[-1])
    expected = history_view(journal, scope, store, 'All', today=today).all_result.time_weighted
    assert expected == pytest.approx(.89)
    value, calls = calculation.value, set()
    def observe(scope, stamp, target):
        calls.add(stamp)
        return value(scope, stamp, target)
    monkeypatch.setattr(calculation, 'value', observe)
    assert full_history_time_weighted(calculation, scope, today, 'EUR') == expected
    assert calls <= {days[0], days[-1]}


def test_scalar_twr_checks_reciprocal_fx_carried_into_the_valuation_interval():
    from portfolio_app.performance.history_view import full_history_time_weighted
    from portfolio_app.portfolio.holdings import DataError
    from test_daily_twr import example
    friday, saturday, monday = '2024-01-05', '2024-01-06', '2024-01-08'
    journal, scope, _ = example((), quantity=1)
    book = journal.securities[0]
    journal = replace(journal, securities=(replace(book, records=(replace(book.records[0], date=saturday),)),))
    store = ObservationStore((
        Observation('saturday-price', 'price', 'invented', saturday, 100, 'USD'),
        Observation('monday-price', 'price', 'invented', monday, 110, 'USD'),
        Observation('friday-rate', 'fx', 'USD/EUR', friday, 1, 'USD', target='EUR'),
        Observation('inverse', 'fx', 'EUR/USD', friday, 2, 'EUR', target='USD'),
        Observation('monday-rate', 'fx', 'USD/EUR', monday, 1, 'USD', target='EUR')))
    with pytest.raises(DataError, match='Conflicting direct and inverse'):
        full_history_time_weighted(HistoryCalculation(journal, store), scope, date.fromisoformat(monday), 'EUR')
    corrected = replace(store, observations=(*store.observations,
        Observation('correction', 'fx', 'EUR/USD', friday, 1, 'EUR', target='USD', replaces='inverse')))
    assert full_history_time_weighted(HistoryCalculation(journal, corrected), scope,
                                      date.fromisoformat(monday), 'EUR') == pytest.approx(.1)


def test_rotating_position_flows_visit_only_new_postings_in_the_selected_book(monkeypatch):
    from collections import Counter
    from datetime import timedelta
    import portfolio_app.performance.personal_performance as module
    start = date(2024, 1, 1)
    books = []
    for i in range(48):
        identity = f'invented-{i}'
        events = [E('zero-'+identity, 'opening', start.isoformat())]
        for offset in (1, 61):
            day = (start + timedelta(days=offset+i)).isoformat()
            events.append(E(f'buy-{identity}-{offset}', 'buy', day, quantity=1, amount=10,
                            currency='EUR', charges_known=True))
        books.append(book(*events, identity=identity))
    calculation = HistoryCalculation(Journal(version=2, securities=tuple(books)), ObservationStore())
    read = Counter()
    original = module.external_flows
    def counted(journal, scope, *args, **kwargs):
        for posting in kwargs['security_cash']:
            read[scope.positions[0], posting.position_id] += 1
        return original(journal, scope, *args, **kwargs)
    monkeypatch.setattr(module, 'external_flows', counted)
    for offset, expected in ((50, 10), (110, 20), (50, 10)):
        end = (start + timedelta(days=offset)).isoformat()
        for item in books:
            flows, reasons = calculation.flows(HistoryScope((item.position_id,)), start.isoformat(), end, 'EUR')
            assert not reasons and sum(flow.amount for flow in flows) == expected
    assert read == Counter({(b.position_id, b.position_id): 2 for b in books})
    assert sum(len(item.buckets) for item in calculation._position_flows.values()) == 96
    # An alternate reporting currency replaces the converted facts for that security.
    first_scope = HistoryScope((books[0].position_id,))
    foreign, missing = calculation.flows(first_scope, start.isoformat(), end, 'USD')
    assert missing and all(flow.amount is None for flow in foreign)
    restored, missing = calculation.flows(first_scope, start.isoformat(), end, 'EUR')
    assert not missing and sum(flow.amount for flow in restored) == 10
    assert len(calculation._position_flows) == len(books)


def test_dated_valuations_reuse_corrected_account_opening_dates(monkeypatch):
    from portfolio_app.ledger.account_journal import Account, Event
    import portfolio_app.performance.personal_performance as module
    journal = Journal(accounts=(Account('a', 'Invented cash'), Account('b', 'Invented empty account')),
        events=(Event('eu', START, 'cash_open', 'a', 'EUR', D(25)),
                Event('us', '2024-01-04', 'cash_open', 'a', 'USD', D(0)),
                Event('corrected', '2024-01-06', 'cash_open', 'a', 'USD', D(0), replaces='us')))
    store = ObservationStore()
    scopes = [HistoryScope(accounts=('a',)), HistoryScope(accounts=('b',))]
    days = ('2024-01-07', '2024-01-05', '2024-01-06')
    expected = {(scope, day): module.value_at(journal, scope, store, day, 'EUR') for scope in scopes for day in days}
    visits = []
    class CountedEvents(tuple):
        def __iter__(self):
            visits.append(True)
            return super().__iter__()
    selected = CountedEvents(module.effective_events(journal))
    monkeypatch.setattr(module, 'effective_events', lambda _: selected)
    calculation = HistoryCalculation(journal, store)
    calculation.accounts(days[0])
    visits.clear()
    for (scope, day), result in expected.items():
        assert calculation.value(scope, day, 'EUR') == result
    assert expected[scopes[0], days[1]].amount is None
    assert expected[scopes[0], days[2]].amount == 25
    assert all(expected[scopes[1], day].amount is None for day in days)
    assert not visits


def test_position_capital_tracks_its_payments_and_delayed_charges(monkeypatch):
    from portfolio_app.ledger.account_journal import Account, Event
    from portfolio_app.ledger.security_history import Charge
    import portfolio_app.performance.personal_performance as module
    own = book(E('open', 'opening', START),
               E('buy', 'buy', '2024-01-03', quantity=1, price=10, currency='EUR', charges_known=True,
                 charges=(Charge(D(2), 'EUR', date='2024-01-05'),)))
    other = book(E('other-open', 'opening', START),
                 E('other-buy', 'buy', '2024-01-04', quantity=1, price=20, currency='EUR', charges_known=True),
                 identity='other')
    journal = Journal(version=2, securities=(own, other), accounts=(Account('cash', 'Invented'),),
                      events=(Event('cash-open', START, 'cash_open', 'cash', 'EUR', D(0)),
                              Event('deposit', '2024-01-06', 'cash_flow', 'cash', 'EUR', D(30))))
    scope = HistoryScope(('stock',))
    store = ObservationStore()
    days = [f'2024-01-{day:02}' for day in range(2, 8)]
    expected = [module._period_capital(HistoryCalculation(journal, store), scope, START, day, 'EUR') for day in days]
    rebuilt = []
    capital = module._period_capital
    def counted(calc, scope, start, end, target):
        rebuilt.append(end)
        return capital(calc, scope, start, end, target)
    monkeypatch.setattr(module, '_period_capital', counted)
    calculation = HistoryCalculation(journal, store)
    actual = [calculation.period_capital(scope, START, day, 'EUR') for day in days]
    assert actual == expected
    assert [item.net_contributions for item in actual] == [0, 10, 10, 12, 12, 12]
    assert rebuilt == [days[0], days[1], days[3]]


def test_cashflows_change_capital_without_rechecking_unchanged_ledger_coverage(monkeypatch):
    from portfolio_app.ledger.account_journal import Account, Event
    import portfolio_app.performance.personal_performance as module
    days = [f'2024-01-{day:02}' for day in range(2, 8)]
    journal = Journal(accounts=(Account('cash', 'Invented account'),), events=(
        Event('open', START, 'cash_open', 'cash', 'EUR', D(10)),
        *(Event('flow-'+day, day, 'cash_flow', 'cash', 'EUR', D(1)) for day in days),
        Event('unclassified', days[3], 'cash_adjust', 'cash', 'EUR', D(2))))
    scope, store = HistoryScope(accounts=('cash',)), ObservationStore()
    expected = [module._period_capital(HistoryCalculation(journal, store), scope, START, day, 'EUR') for day in days]
    checks = []
    original = module.coverage_reasons
    def counted(journal, scope, observations, start, end, **kwargs):
        checks.append(end)
        return original(journal, scope, observations, start, end, **kwargs)
    monkeypatch.setattr(module, 'coverage_reasons', counted)
    calculation = HistoryCalculation(journal, store)
    actual = [calculation.period_capital(scope, START, day, 'EUR') for day in days]
    assert actual == expected
    assert [item.net_contributions for item in actual] == [1, 2, 3, None, None, None]
    assert checks == [days[0], days[3]]
    # Navigation can revisit earlier dates and a reclassification creates new evidence.
    for day in reversed(days):
        assert calculation.coverage(scope, START, day) == original(journal, scope, store, START, day)
    classified = replace(store, classifications=(('unclassified', 'income'),))
    assert not HistoryCalculation(journal, classified).coverage(scope, START, days[-1])
    assert calculation.coverage(scope, START, days[-1])


def test_daily_net_contributions_consume_each_posting_once_in_decimal_order(monkeypatch):
    from decimal import localcontext
    from portfolio_app.ledger.account_journal import Account, Event
    from portfolio_app.performance.history_flows import DatedFlows
    days = [f'2024-01-{day:02}' for day in range(2, 24)]
    scope = HistoryScope(accounts=('cash',))
    journal = Journal(accounts=(Account('cash', 'Invented account'),), events=(
        Event('open', START, 'cash_open', 'cash', 'EUR', D(0)),
        Event('fund', START, 'cash_flow', 'cash', 'EUR', D(1000)),
        *(Event(f'{day}-{seq}', day, 'cash_flow', 'cash', 'EUR', D('.4'), sequence=seq)
          for day in days for seq in (1, 2))))
    calculation = HistoryCalculation(journal, ObservationStore())
    visited = []
    original = DatedFlows.interval
    def counted(self, *args, **kwargs):
        flows, reasons = original(self, *args, **kwargs)
        visited.extend(flow.id for flow in flows)
        return flows, reasons
    monkeypatch.setattr(DatedFlows, 'interval', counted)
    with localcontext() as context:
        context.prec = 4
        for day in [START, *days]:
            total = calculation.period_capital(scope, START, day, 'EUR').net_contributions
            # Each 0.4 rounds away when added to the balance. Subtotalling the
            # two payments first would round up a full unit on every date.
            assert total == 1000
    assert len(visited) == len(set(visited)) == 1 + 2 * len(days)


def test_sibling_returns_and_range_capital_use_validated_security_closes(monkeypatch):
    from datetime import timedelta
    from portfolio_app.ledger.security_history import _SecurityReplay, SecurityTimeline
    from portfolio_app.performance.history_view import full_history_time_weighted
    from test_daily_twr import example, trade

    first = date(2024, 1, 1)
    dates = [(first + timedelta(days=i)).isoformat() for i in range(SecurityTimeline.MAX_SNAPSHOTS + 14)]
    events = tuple(trade('buy-'+day, 'buy', day, 10) for day in dates[1:])
    journal, scope, store = example(events, prices=[(day, 10) for day in dates])
    original = journal.securities[0]
    sibling_events = tuple(replace(e, id='sibling-'+e.id) for e in original.records)
    sibling = replace(original, position_id='sibling', records=sibling_events,
                      active_ids=tuple(e.id for e in sibling_events))
    journal = replace(journal, securities=(original, sibling))
    store = ObservationStore(tuple(replace(o, subject='SYNTH') for o in store.observations))
    calculation = HistoryCalculation(journal, store)
    calculation.securities()  # The overview already requested the canonical complete inventory.
    def unexpected_replay(*args, **kwargs):
        pytest.fail('a closing valuation reconstructed previously validated FIFO lots')
    monkeypatch.setattr(_SecurityReplay, 'advance', unexpected_replay)
    for members in (scope, HistoryScope(('sibling',)), HistoryScope(('invented', 'sibling'))):
        assert full_history_time_weighted(calculation, members, date.fromisoformat(dates[-1]), 'EUR') == 0
        assert calculation.capital(members, dates[21], 'EUR').amount == 210 * len(members.positions)


def test_dated_close_shares_fx_across_members_and_preserves_per_member_evidence(monkeypatch):
    import portfolio_app.performance.personal_performance as module
    from portfolio_app.ledger.account_journal import Account, Event
    from test_daily_twr import example

    friday, saturday, monday = '2024-01-05', '2024-01-06', '2024-01-08'
    journal, _, _ = example((), quantity=1)
    accounts = (Account('first', 'Invented first'), Account('second', 'Invented second'))
    journal = replace(journal, accounts=accounts, events=(
        Event('first-open', friday, 'cash_open', 'first', 'USD', 10),
        Event('second-open', friday, 'cash_open', 'second', 'USD', 3),
        Event('eu-open', friday, 'cash_open', 'second', 'EUR', 4)))
    scope = HistoryScope(('invented',), ('first', 'second'))
    prices = tuple(Observation('p-'+day, 'price', 'invented', day, 20, 'USD') for day in (saturday, monday))
    store = ObservationStore((*prices, Observation('friday-fx', 'fx', 'EUR/USD', friday, 2, 'EUR', target='USD')))
    calls, original = [], module.valuation_fx
    def observed(store, source, target, stamp):
        calls.append((source, stamp))
        return original(store, source, target, stamp)
    monkeypatch.setattr(module, 'valuation_fx', observed)
    calc = HistoryCalculation(journal, store)
    weekend, members = calc.value_with_members(scope, saturday, 'EUR')
    assert weekend.amount == D('20.5') and members == [10, 5, D('5.5')]
    assert [(e.subject, e.as_of, e.method) for e in weekend.evidence] == [
        ('invented', saturday, 'close'), *[('EUR/USD', friday, 'closed_fx_session')]*3]
    unsupported, members = calc.value_with_members(scope, monday, 'EUR')
    assert unsupported.amount is None and not members
    assert [str(issue).split(':')[0] for issue in unsupported.reasons] == ['invented', 'first', 'second']
    assert [day for currency, day in calls if currency == 'USD'] == [saturday, monday]


def test_shared_fx_preserves_component_rounding_and_checks_only_used_currencies():
    from decimal import localcontext
    from portfolio_app.ledger.account_journal import Account, Event
    from portfolio_app.performance.personal_performance import value_at
    from portfolio_app.portfolio.holdings import DataError

    accounts = tuple(Account(name, name) for name in ('first', 'second'))
    journal = Journal(accounts=accounts, events=tuple(
        Event('opening-'+a.id, START, 'cash_open', a.id, 'USD', 5) for a in accounts))
    scope = HistoryScope(accounts=('first', 'second'))
    inverse = Observation('inverse', 'fx', 'EUR/USD', START, 3, 'EUR', target='USD')
    with localcontext() as context:
        context.prec = 3
        members = []
        result = value_at(journal, scope, ObservationStore((inverse,)), START, 'EUR', member_amounts=members)
        assert result.amount == D('3.32') and members == [D('1.66'), D('1.66')]
    conflict = ObservationStore((inverse, Observation('direct', 'fx', 'USD/EUR', START, 1, 'USD', target='EUR')))
    with pytest.raises(DataError, match='Conflicting direct and inverse'):
        value_at(journal, scope, conflict, START, 'EUR')
    empty = replace(journal, events=tuple(replace(e, amount=0) for e in journal.events))
    assert value_at(empty, scope, conflict, START, 'EUR').amount == 0
