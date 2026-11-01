"""Evaluation orchestration with injected synthetic sources."""
from dataclasses import replace
from datetime import timedelta

from portfolio_app.monitoring import Evaluation
from portfolio_app.monitoring_service import MonitoringService
from portfolio_app.monitoring_store import save_rule, load_monitoring, record_evaluations
from test_monitoring import rule, history, NOW


def test_factory_alias_shares_service_and_background_completion(tmp_path, monkeypatch):
    from threading import Event
    from portfolio_app import monitoring_service
    from portfolio_app.market_data import RequestCoordinator
    from portfolio_app.monitoring import SeriesSpec

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


def test_catchup_only_completed_closes_after_recorded_observation(tmp_path):
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
    from portfolio_app.monitoring import Rule
    from test_account_margin import fixture, security_reserve, DAY
    from portfolio_app.account_journal import Event, append_event
    journal,held,valued=fixture()
    journal=security_reserve(journal,held,'1')
    journal=append_event(journal,Event('debt',DAY,'debt_open',account_id='broker',currency='EUR',amount=700,reference='invented'))
    for column,value in {'price_status':'fresh','fx_status':'identity','price_age_hours':1.,'fx_age_hours':0.}.items():
        valued[column]=value
    r=Rule('ltv','Invented LTV',kind='ltv',account_id='broker',backing=('security',),direction='above',threshold=60,warning_band=5)
    save_rule(tmp_path,r)
    service=MonitoringService(tmp_path,demo=True,now=lambda:NOW)
    service.account_inputs=lambda _, **kwargs: (journal,held,valued,'EUR',{},'2026-10-12T11:00:00Z',False)
    service.evaluate();service.evaluate()
    doc,_=load_monitoring(tmp_path)
    assert len(doc['observations'])==1 and len(doc['events'])==1
    assert doc['events'][0]['evaluation']['value']==70


def account_workspace(directory, *, manual=True):
    import json
    from portfolio_app.account_journal import Account, Journal, Event, append_events, save_journal
    from portfolio_app.account_margin import bind_holding
    from portfolio_app.positions import read_snapshot
    from portfolio_app.monitoring import Rule
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
