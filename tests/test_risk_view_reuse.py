"""Risk presentation reuses only the current selection and the actual market evidence read."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pandas as pd

from portfolio_app.analytics.risk_data import DEFAULT_BENCHMARK, DemoRiskHistoryProvider, MarketRiskHistoryProvider
from portfolio_app.interface.views.analytics import risk_updates


def test_risk_reuse_checks_revised_histories_weights_scope_and_retry_deadlines(tmp_path, monkeypatch):
    session = {}
    monkeypatch.setattr(risk_updates.st, 'session_state', session)
    provider = DemoRiskHistoryProvider()
    calls = []
    down = {'SYNTH-B'}
    def fetch(self, ticker, years):
        calls.append(ticker)
        if ticker in down:
            raise ValueError('Invented missing history')
        return provider.fetch(ticker, years)
    monkeypatch.setattr(MarketRiskHistoryProvider, 'fetch', fetch)
    holdings = pd.DataFrame(dict(id=['a', 'b'], name=['Invented A', 'Invented B'], ticker=['SYNTH-A', 'SYNTH-B'],
        shares=1., current_value_reporting=[100., 200.], instrument_type='equity', quote_currency='EUR'))
    def read(positions=holdings, **kwargs):
        return risk_updates.view_risk(positions, tmp_path, False, DEFAULT_BENCHMARK, 3, **kwargs)
    first, sources = read()
    assert first.status == 'partial' and set(first.excluded) == {'b'} and 'fresh' in set(sources.Status)
    warm, cached = read(holdings.assign(price_age_hours=1.25))
    assert warm is first and 'fresh' not in set(cached.Status)
    assert calls == [DEFAULT_BENCHMARK + ':EUR', 'SYNTH-A', 'SYNTH-B']
    assert cached.attrs == sources.attrs

    # Another instance corrects adjusted prices without changing the fetched time.
    cache = session['analytics_risk_snapshot'].histories.cache
    path = cache._path('adjusted-daily-v1:SYNTH-A:3')
    evidence = json.loads(path.read_text())
    dates = pd.DatetimeIndex(evidence['data']['dates'])
    closing = dates[(dates.dayofweek == 4) & (dates < pd.Timestamp.today().normalize())][-1]
    evidence['data']['prices'][dates.get_loc(closing)] *= 1.25
    path.write_text(json.dumps(evidence))
    revised, _ = read()
    assert revised.beta != first.beta and len(calls) == 3
    assert read()[0] is revised

    failure_path = cache._path('adjusted-daily-v1:SYNTH-B:3')
    evidence = json.loads(failure_path.read_text())
    evidence['attempted_at'] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    failure_path.write_text(json.dumps(evidence))
    down.clear()
    recovered, _ = read()
    assert recovered.status == 'complete' and calls[3:] == ['SYNTH-B']
    heavier = holdings.copy()
    heavier.loc[1, 'current_value_reporting'] *= 2
    reweighted, _ = read(heavier)
    assert reweighted.beta != recovered.beta
    assert read(heavier)[0] is reweighted
    subset, _ = read(heavier, scoped=True, portfolio_result=SimpleNamespace(net_equity=700.))
    assert subset.scoped and subset.portfolio_beta_contribution == subset.beta * 500 / 700
    changed_base, _ = read(heavier, scoped=True, portfolio_result=SimpleNamespace(net_equity=900.))
    assert changed_base.portfolio_beta_contribution == changed_base.beta * 500 / 900
    refreshed, _ = read(heavier, refresh=True)
    assert calls[4:] == [DEFAULT_BENCHMARK + ':EUR', 'SYNTH-A', 'SYNTH-B']
    assert refreshed.beta != reweighted.beta and read(heavier)[0] is refreshed
    next_day = risk_updates.date.today() + timedelta(days=1)
    monkeypatch.setattr(risk_updates, 'date', SimpleNamespace(today=lambda: next_day))
    tomorrow, _ = read(heavier)
    assert tomorrow is not refreshed and read(heavier)[0] is tomorrow
    assert len(calls) == 7


def test_actual_risk_reuse_observes_proxy_positions_outside_the_selection(tmp_path, monkeypatch):
    from test_actual_risk import actual, future, row
    from portfolio_app.funds.leverage_settings import FundFactor, LeverageSettings, save_leverage_settings
    monkeypatch.setattr(risk_updates.st, 'session_state', {})
    monkeypatch.setattr(MarketRiskHistoryProvider, 'fetch', DemoRiskHistoryProvider.fetch)
    positions = actual([row('cash', 'cash', 100., 'EUR'), future(proxy_position_id='proxy')])
    path = tmp_path / 'holdings.csv'
    def save_proxy(ticker):
        path.write_text('id,name,shares,instrument_type,position_key,ticker\n'
                        f'proxy,Invented proxy,1,etf,proxy,{ticker}\n')
    def read():
        return risk_updates.view_risk(positions, tmp_path, False, DEFAULT_BENCHMARK, 3,
            portfolio_result=SimpleNamespace(net_equity=100.))[0]
    save_proxy('SYNTH-A')
    first = read()
    assert first.status == 'complete' and read() is first
    save_proxy('SYNTH-B')
    revised = read()
    assert revised.status == 'complete' and revised.beta != first.beta and read() is revised
    save_leverage_settings(tmp_path, LeverageSettings(fund_factors=(FundFactor('proxy', None),)), None)
    excluded = read()
    assert excluded.status == 'partial' and 'future:synthetic' in excluded.excluded
