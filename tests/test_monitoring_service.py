"""Evaluation orchestration with injected synthetic sources."""
from dataclasses import replace
from datetime import timedelta

from portfolio_app.monitoring.monitoring import Evaluation
from portfolio_app.monitoring.monitoring_service import MonitoringService
from portfolio_app.monitoring.monitoring_store import save_rule, load_monitoring, record_evaluations
from test_monitoring import rule, history, NOW


def test_factory_alias_shares_service_and_background_completion(tmp_path, monkeypatch):
    from threading import Event
    from portfolio_app.monitoring import monitoring_service
    from portfolio_app.market.market_data import RequestCoordinator
    from portfolio_app.monitoring.monitoring import SeriesSpec

    (tmp_path / 'parent').mkdir()
    directory = tmp_path / 'invented-demo'
    directory.mkdir()
    alias = tmp_path / 'parent' / '..' / directory.name
    coordinator = RequestCoordinator(workers=1)
    monkeypatch.setattr(monitoring_service, 'coordinator', coordinator)
    monkeypatch.setattr(monitoring_service, '_services', {})
    service = monitoring_service.monitoring_for(alias, demo=True)
    assert monitoring_service.monitoring_for(directory, demo=True) is service
    started = Event()
    original_get = service.market.get
    def get(*args, **kwargs):
        started.set()
        return original_get(*args, **kwargs)
    monkeypatch.setattr(service.market, 'get', get)
    spec = SeriesSpec('MSCI:990100:NETR:EUR', currency='EUR')
    try:
        assert service.browse(spec) is None
        assert started.wait(5), 'Synthetic monitoring request did not start'
    finally:
        coordinator.close()
    assert coordinator.revision(directory.resolve()) == 1
    assert service.browse(spec).dates


def test_catchup_only_completed_closes_after_recorded_observation(tmp_path, monkeypatch):
    from portfolio_app.monitoring import monitoring
    original = monitoring.indicator_frame
    calculated = []
    def counted(*args, **kwargs):
        calculated.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(monitoring, 'indicator_frame', counted)
    r=replace(rule(),created_at='2026-10-01T00:00:00+00:00')
    save_rule(tmp_path,r)
    record_evaluations(tmp_path,r,[Evaluation('2026-10-06',1.,condition='normal')],now=NOW-timedelta(days=5))
    service=MonitoringService(tmp_path,demo=True,now=lambda:NOW)
    series=history((100.,100.,100.,90.,100.,110.),('2026-10-02','2026-10-05','2026-10-06','2026-10-07','2026-10-08','2026-10-09'))
    service.market.get=lambda *a,**kw:series
    service.evaluate()
    doc,_=load_monitoring(tmp_path)
    assert not service.error
    assert [o['evaluation']['observed_at'] for o in doc['observations']] == ['2026-10-06','2026-10-07','2026-10-08','2026-10-09']
    assert doc['events'][0]['recovered'] and doc['events'][0]['resolved']
    assert len(calculated) == 1
    service.evaluate()
    assert len(load_monitoring(tmp_path)[0]['observations']) == 4


def test_intraday_does_not_reconstruct_missed_signals(tmp_path):
    r=rule(cadence='intraday')
    save_rule(tmp_path,r)
    service=MonitoringService(tmp_path,demo=True,now=lambda:NOW)
    service.market.get=lambda *a,**kw:replace(history(),quote=90.,quote_at=NOW.isoformat())
    service.evaluate()
    doc,_=load_monitoring(tmp_path)
    assert len(doc['events']) == 1 and not doc['events'][0]['recovered']
    service.market.get=lambda *a,**kw:replace(history(),quality='stale')
    service.evaluate()
    doc,_=load_monitoring(tmp_path)
    assert doc['states'][r.id]['condition']=='triggered'
    assert doc['states'][r.id]['quality']=='stale'


def test_account_service_logs_actual_inputs_only_without_backfill(tmp_path):
    from portfolio_app.monitoring.monitoring import Rule
    from test_account_margin import fixture, security_reserve, DAY
    from portfolio_app.ledger.account_journal import Event, append_event
    journal,held,valued=fixture()
    journal=security_reserve(journal,held,'1')
    journal=append_event(journal,Event('debt',DAY,'debt_open',account_id='broker',currency='EUR',amount=700,reference='invented'))
    for column,value in {'price_status':'fresh','fx_status':'identity','price_observed_at': (NOW-timedelta(hours=1)).isoformat(), 'fx_observed_at': NOW.isoformat()}.items():
        valued[column]=value
    r=Rule('ltv','Invented LTV',kind='ltv',account_id='broker',backing=('security',),direction='above',threshold=60,warning_band=5)
    save_rule(tmp_path,r)
    service=MonitoringService(tmp_path,demo=True,now=lambda:NOW)
    from types import SimpleNamespace
    from portfolio_app.ledger.account_journal import replay
    service._account_sources=lambda **kwargs: SimpleNamespace(state=replay(journal))
    service.account_inputs=lambda _, **kwargs: (journal,held,valued,'EUR',{},'2026-10-12T11:00:00Z',False)
    service.evaluate();service.evaluate()
    doc,_=load_monitoring(tmp_path)
    assert len(doc['observations'])==1 and len(doc['events'])==1
    assert doc['events'][0]['evaluation']['value']==70


def account_workspace(directory, *, manual=True):
    import json
    from portfolio_app.ledger.account_journal import Account, Journal, Event, append_events, save_journal
    from portfolio_app.derivatives.account_margin import bind_holding
    from portfolio_app.portfolio.positions import read_snapshot
    from portfolio_app.monitoring.monitoring import Rule
    directory.joinpath('holdings.csv').write_text(
        'id,name,ticker,shares,instrument_type,position_key,manual_price,manual_price_currency,manual_price_date,quantity_unit\n'
        + ('synthetic,Invented security,,10,equity,security,100,EUR,2026-10-12,shares\n' if manual else
           'synthetic,Invented security,INVENTED,10,equity,security,,,,shares\n'))
    directory.joinpath('demo_prices.json').write_text(json.dumps({'prices':{},'fx':{}}))
    held=read_snapshot(directory/'holdings.csv').holdings
    journal=Journal(accounts=(Account('account','Invented account'),Account('unrelated','Other invented account')))
    journal=append_events(journal,(
        Event('loan','2026-10-12','debt_open',account_id='account',currency='EUR',amount=600,reference='invented'),
        Event('other-loan','2026-10-12','debt_open',account_id='unrelated',currency='USD',amount=900,reference='invented'),))
    journal=bind_holding(journal,held,'security','account')
    save_journal(directory,journal,None)
    r=Rule('account-rule','Invented LTV',kind='ltv',account_id='account',backing=('security',),direction='above',threshold=60,warning_band=5)
    save_rule(directory,r)
    return r


def test_real_account_input_path_scopes_fx_and_handles_zero_holdings(tmp_path):
    r=account_workspace(tmp_path)
    service=MonitoringService(tmp_path,demo=True,now=lambda:NOW)
    service.evaluate()
    doc,_=load_monitoring(tmp_path)
    latest=doc['states'][r.id]['latest']
    assert latest['quality']=='valid' and latest['value']==60
    assert latest['inputs']['debt']==600  # unrelated USD loan needs no FX
    assert latest['inputs']['backing']=={'security':1000}
    path=tmp_path/'holdings.csv'
    path.write_text(path.read_text().replace(',,10,equity',',,0,equity'))
    service.evaluate()
    latest=load_monitoring(tmp_path)[0]['states'][r.id]['latest']
    assert latest['quality']=='valid' and latest['condition']=='triggered'
    assert latest['value'] is None and latest['inputs']['asset_value']==0


def test_account_cached_fallback_cannot_clear_trigger_and_manual_refresh_retries(tmp_path):
    r=account_workspace(tmp_path,manual=False)
    service=MonitoringService(tmp_path,now=lambda:NOW)
    calls=[]
    def fetch(spec,**kwargs):
        calls.append(kwargs.get('refresh'))
        return replace(history(),quote=100.,quote_at=NOW.isoformat())
    service.market.get=fetch
    service.evaluate()
    assert load_monitoring(tmp_path)[0]['states'][r.id]['latest']['value']==60
    def fail(spec,**kwargs):
        calls.append(kwargs.get('refresh'))
        return replace(history(),quality='stale')
    service.market.get=fail
    service.evaluate(refresh=True)
    state=load_monitoring(tmp_path)[0]['states'][r.id]
    assert calls==[False,True]
    assert state['quality']=='stale' and state['condition']=='triggered'
    assert state['last_valid']['value']==60


def test_two_sma_entry_exit_reuse_and_recover_observations(tmp_path, monkeypatch):
    from portfolio_app.monitoring import monitoring
    from portfolio_app.monitoring.monitoring_presentation import alert_details

    exit_rule = rule(comparison_window=2, threshold=0, warning_band=0)
    entry_rule = replace(exit_rule, id='entry', direction='above')
    save_rule(tmp_path, exit_rule)
    save_rule(tmp_path, entry_rule)
    dates = ('2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09')
    closes = (110., 100., 90., 120., 120., 60.)
    series = history(closes[:3], dates[:3])
    calls = []
    def fetch(spec, **kwargs):
        calls.append('fetch')
        return series
    calculate = monitoring.indicator_frame
    def frame(*args, **kwargs):
        calls.append('frame')
        return calculate(*args, **kwargs)
    service = MonitoringService(tmp_path, demo=True, now=lambda: NOW)
    monkeypatch.setattr(service.market, 'get', fetch)
    monkeypatch.setattr(monitoring, 'indicator_frame', frame)

    service.evaluate(refresh=True)
    assert calls == ['fetch', 'frame']
    first = load_monitoring(tmp_path)[0]['events']
    assert [(e['rule_id'], e['condition']) for e in first] == [('invented', 'triggered')]
    assert first[0]['evaluation']['inputs']['comparison_sma'] == 95.
    series = replace(series, quality='stale')
    service.evaluate()
    assert load_monitoring(tmp_path)[0]['states'][exit_rule.id]['condition'] == 'triggered'

    series = history(closes, dates)
    service.evaluate()
    service.evaluate()
    doc = load_monitoring(tmp_path)[0]
    exit_events = [e for e in doc['events'] if e['rule_id'] == exit_rule.id]
    assert [e['observed_at'] for e in exit_events] == [dates[2], dates[5]]
    assert exit_events[0]['resolved'] and not exit_events[1]['resolved']
    entry_events = [e for e in doc['events'] if e['rule_id'] == entry_rule.id]
    assert len(entry_events) == 1 and entry_events[0]['recovered'] and entry_events[0]['resolved']
    event = entry_events[0]
    assert event['observed_at'] == dates[3]
    evaluation = event['evaluation']
    assert evaluation['inputs']['comparison_sma'] == 105 and evaluation['price'] == 120
    condition, observed = alert_details(entry_rule, evaluation)
    assert '2-session SMA at or above' in condition
    assert '2-session SMA 105.00 EUR' in observed and '3-session SMA 103.33 EUR' in observed
    assert 'Distance +1.61%' in observed


def test_refresh_reuses_a_source_across_thresholds_only_within_one_pass(tmp_path, monkeypatch):
    from portfolio_app.monitoring import monitoring
    first = rule(threshold=5.)
    second = replace(first, id='second', threshold=15.)
    adjusted = replace(first, id='adjusted', series=replace(first.series, basis='adjusted'))
    other_window = replace(first, id='other-window', window=first.window + 1)
    pair = replace(first, id='pair', comparison_window=2)
    expired = replace(first, id='expired', max_age_minutes=1)
    for item in (first, second, adjusted, other_window, pair, expired):
        save_rule(tmp_path, item)
    service = MonitoringService(tmp_path, demo=True, now=lambda: NOW)
    calls, frames = [], []
    calculate = monitoring.indicator_frame
    def count(rule, series, *, now):
        frames.append((rule.window, rule.comparison_window))
        return calculate(rule, series, now=now)
    monkeypatch.setattr(monitoring, 'indicator_frame', count)
    def fetch(spec, *, refresh):
        calls.append((spec.basis, refresh))
        return history() if spec == first.series else replace(history(), quality='stale')
    service.market.get = fetch
    for refresh in (False, True):
        frames.clear()
        service.evaluate(refresh=refresh)
        states = load_monitoring(tmp_path)[0]['states']
        assert states[first.id]['condition'] == 'normal' and states[second.id]['condition'] == 'triggered'
        assert states[adjusted.id]['quality'] == states[expired.id]['quality'] == 'stale'
        assert states[pair.id]['latest']['inputs']['comparison_sma'] == 115.
        assert states[pair.id]['condition'] == 'triggered'
        assert len(frames) == 3
        assert set(frames) == {(first.window, None), (other_window.window, None), (first.window, 2)}
        assert calls.count(('close', refresh)) == calls.count(('adjusted', refresh)) == 1


def test_account_rules_share_one_pass_but_keep_asset_scope_and_freshness(tmp_path, monkeypatch):
    from unittest.mock import patch
    from portfolio_app.derivatives.account_margin import bind_holding
    from portfolio_app.ledger import account_journal
    from portfolio_app.portfolio import positions, valuation

    original = account_workspace(tmp_path, manual=False)
    path = tmp_path / 'holdings.csv'
    with path.open('a') as f:
        f.write('second,Invented second position,INVENTED,5,equity,second,,,,shares\n')
    journal, revision = account_journal.load_journal(tmp_path)
    journal = bind_holding(journal, positions.read_snapshot(path).holdings, 'second', 'account')
    account_journal.save_journal(tmp_path, journal, revision)
    save_rule(tmp_path, replace(original, id='both', backing=('security', 'second')))
    save_rule(tmp_path, replace(original, id='strict', max_age_minutes=1))
    service = MonitoringService(tmp_path, now=lambda: NOW)
    calls, price = [], [100.]
    def fetch(spec, *, refresh):
        calls.append((spec.symbol, refresh))
        return replace(history(), quote=price[0], quote_at=(NOW-timedelta(minutes=2)).isoformat())
    monkeypatch.setattr(service.market, 'get', fetch)
    for expected, both in [(60., 40.), (30., 20.)]:
        with patch.object(account_journal, 'load_journal', wraps=account_journal.load_journal) as journals, \
             patch.object(positions, 'read_snapshot', wraps=positions.read_snapshot) as holdings, \
             patch.object(valuation, 'value_holdings', wraps=valuation.value_holdings) as values:
            service.evaluate(refresh=True)
            assert journals.call_count == holdings.call_count == 1
            assert values.call_count == 2
        states = load_monitoring(tmp_path)[0]['states']
        assert states[original.id]['latest']['value'] == expected
        assert states['both']['latest']['value'] == both
        assert states['strict']['quality'] == 'stale'
        assert calls == [('INVENTED', True)]
        price[0] = 200.
        calls.clear()


def test_account_quote_can_age_out_between_rules_sharing_a_valuation(tmp_path, monkeypatch):
    from portfolio_app.monitoring.monitoring_store import EvaluationWriter

    first = replace(account_workspace(tmp_path, manual=False), revision=2, max_age_minutes=5)
    save_rule(tmp_path, first, expected_rule_revision=1)
    save_rule(tmp_path, replace(first, id='later', revision=1))
    clock = [NOW]
    service = MonitoringService(tmp_path, now=lambda: clock[0])
    service.market.get = lambda *args, **kwargs: replace(history(), quote=100., quote_at=NOW.isoformat())
    original = EvaluationWriter.record
    def record(writer, rule, evaluations, **kwargs):
        result = original(writer, rule, evaluations, **kwargs)
        if rule.id == first.id:
            clock[0] += timedelta(minutes=6)
        return result
    monkeypatch.setattr(EvaluationWriter, 'record', record)
    service.evaluate(refresh=True)
    states = load_monitoring(tmp_path)[0]['states']
    assert states[first.id]['quality'] == 'valid' and states['later']['quality'] == 'stale'


def test_unavailable_account_snapshot_is_attempted_once_and_sma_checks_continue(tmp_path, monkeypatch):
    account = account_workspace(tmp_path)
    save_rule(tmp_path, replace(account, id='other'))
    sma = rule()
    save_rule(tmp_path, sma)
    service = MonitoringService(tmp_path, demo=True, now=lambda: NOW)
    attempts = []
    def fail(**kwargs):
        attempts.append(True)
        raise OSError('Invented read failure')
    monkeypatch.setattr(service, '_account_sources', fail)
    monkeypatch.setattr(service.market, 'get', lambda *args, **kwargs: history())
    service.evaluate()
    states = load_monitoring(tmp_path)[0]['states']
    assert attempts == [True] and not service.error
    assert states[account.id]['quality'] == states['other']['quality'] == 'unavailable'
    assert states[sma.id]['quality'] == 'valid'
