"""Synthetic monitoring arithmetic; never reads working portfolio data or providers."""
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd
import pytest

from portfolio_app.holdings import DataError
from portfolio_app.monitoring import Rule, SeriesSpec, MarketSeries, Evaluation, evaluate_series, indicator_frame, evaluate_account
from portfolio_app.monitoring_market import yahoo_series

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


def rule(**kwargs):
    return Rule('invented', 'Invented rule', series=SeriesSpec('INVENTED',currency='EUR'), window=3, **kwargs)


def history(values=(100., 110., 120.), dates=('2026-10-07','2026-10-08','2026-10-09'), **kwargs):
    return MarketSeries(dates, values, (True,)*len(dates), 'EUR', NOW.isoformat(), quality='valid', **kwargs)


def test_completed_sessions_include_current_close_and_use_200_observations_not_days():
    r = rule()
    e = evaluate_series(r, history(), now=NOW)
    assert e.reference == 110
    assert e.value == pytest.approx(100*(120/110-1))
    dates = tuple(x.date().isoformat() for x in pd.bdate_range(end='2026-10-09', periods=200))
    series = history(tuple(float(x) for x in range(1,201)), dates)
    assert evaluate_series(replace(r,window=200),series,now=NOW).reference == 100.5
    assert evaluate_series(replace(r,window=201),series,now=NOW).quality == 'unavailable'


def test_developing_day_does_not_enter_default_sma():
    s = replace(history((100.,110.,120.,90.), ('2026-10-07','2026-10-08','2026-10-09','2026-10-12')),
                complete=(True,True,True,False), quote=90., quote_at=NOW.isoformat())
    closed = evaluate_series(rule(),s,now=NOW)
    assert closed.price == 120 and closed.reference == 110
    intraday = evaluate_series(rule(cadence='intraday'),s,now=NOW)
    assert intraday.price == 90 and intraday.reference == 110
    developing = evaluate_series(rule(cadence='intraday',developing=True),s,now=NOW)
    assert developing.reference == pytest.approx((110+120+90)/3)


def test_ten_month_sma_and_missing_months_never_forward_fill():
    dates = tuple(x.date().isoformat() for x in pd.date_range('2025-12-31','2026-09-30',freq='ME'))
    s = history(tuple(100.+i for i in range(10)), dates)
    r = replace(rule(period='months'),window=10)
    assert evaluate_series(r,s,now=NOW).quality == 'stale'  # source itself is 12 days old
    r = replace(r,max_age_minutes=30*1440)
    assert evaluate_series(r,s,now=NOW).reference == 104.5
    gap = replace(s,dates=dates[:4]+dates[5:],closes=s.closes[:4]+s.closes[5:],complete=(True,)*9)
    assert evaluate_series(r,gap,now=NOW).quality == 'unavailable'


def test_monthly_reference_stays_fixed_until_next_month_and_developing_option():
    s = history((90.,100.,110.,150.), ('2026-07-31','2026-08-31','2026-09-30','2026-10-09'), quote=180., quote_at=NOW.isoformat())
    r = rule(period='months',cadence='intraday')
    assert evaluate_series(r,s,now=NOW).reference == 100
    assert evaluate_series(replace(r,developing=True),s,now=NOW).reference == 130
    monthly = evaluate_series(replace(r,cadence='monthly'),s,now=NOW)
    assert monthly.price == 110 and monthly.observed_at == '2026-09-30'


def test_no_signal_from_stale_currency_mismatch_or_incomplete_history():
    s = history()
    assert evaluate_series(rule(),replace(s,quality='stale'),now=NOW).quality == 'stale'
    assert evaluate_series(rule(),replace(s,currency='USD'),now=NOW).quality == 'unavailable'
    with pytest.raises(DataError):
        indicator_frame(rule(),replace(s,dates=('2026-10-07',)*3),now=NOW)
    with pytest.raises(DataError):
        Rule('r','Bad MSCI',series=SeriesSpec('MSCI:990100:NETR:EUR'),cadence='intraday')
    with pytest.raises(DataError):
        rule(developing=True)


def test_equality_and_approaching_band_are_percentage_points():
    from portfolio_app.monitoring import condition
    r = rule()
    assert [condition(v,r) for v in (-2.,-1.5,-1.,-.9)] == ['triggered','approaching','approaching','normal']
    r = replace(r,direction='above',threshold=60,warning_band=5)
    assert [condition(v,r) for v in (60,58,55,54)] == ['triggered','approaching','approaching','normal']


def test_yahoo_finalization_subunits_adjustment_and_real_quote_timestamp():
    frame = pd.DataFrame({'Close':[100.,110.,120.], 'Adj Close':[80.,90.,120.]},index=pd.to_datetime(['2026-10-08','2026-10-09','2026-10-12']))
    metadata = dict(currency='GBp',exchangeTimezoneName='Europe/London',regularMarketPrice=123.,regularMarketTime=NOW.timestamp(),
                    currentTradingPeriod={'regular':{'end':datetime(2026,10,12,16,tzinfo=timezone.utc).timestamp()}})
    result = yahoo_series(frame,metadata,'close',NOW)
    assert result.currency == 'GBP' and result.quote == 1.23
    assert result.complete == (True,True,False)
    assert result.quote_at == NOW.isoformat()
    assert yahoo_series(frame,metadata,'adjusted',NOW).closes == (.8,.9,1.2)
    later = NOW.replace(hour=17)
    assert yahoo_series(frame,metadata,'close',later).complete == (True,)*3
    metadata['currentTradingPeriod']['regular']['end'] = pd.Timestamp('2026-10-12T17:00:00+01:00')
    metadata['regularMarketTime'] = pd.Timestamp(NOW)
    normalized = yahoo_series(frame,metadata,'close',later)
    assert normalized.complete == (True,)*3 and normalized.quote_at == NOW.isoformat()
    del metadata['currentTradingPeriod']
    assert yahoo_series(frame,metadata,'close',later).complete[-1] is False


def test_account_ltv_uses_canonical_debt_selected_assets_and_fx():
    from test_account_margin import fixture, security_reserve, DAY
    from portfolio_app.account_journal import Event, append_events, MarginRequirement
    journal,held,valued = fixture()
    journal = security_reserve(journal,held,'1')
    journal = append_events(journal, (
        Event('loan',DAY,'debt_open',account_id='broker',currency='EUR',amount=300,reference='invented'),
        Event('negative',DAY,'cash_open',account_id='broker',currency='GBP',amount=-100),))
    r = Rule('ltv','Invented LTV',kind='ltv',account_id='broker',backing=('security',),cash=('USD',),threshold=60,warning_band=10,direction='above')
    e = evaluate_account(r,journal,held,valued,fx_rates={'USD':.9,'GBP':1.2},now=NOW)
    assert e.inputs['debt'] == 420
    assert e.inputs['asset_value'] == 1900
    assert e.value == pytest.approx(420/1900*100)
    assert evaluate_account(r,journal,held,valued,now=NOW).quality == 'unavailable'
    zero = evaluate_account(replace(r,backing=(),cash=()),journal,held,valued,fx_rates={'GBP':1.2},now=NOW)
    assert zero.condition == 'triggered' and zero.value is None
    journal = replace(journal,margin_requirements=(MarginRequirement('req','broker','EUR',450,360,DAY),))
    coverage = evaluate_account(Rule('cov','Coverage',kind='coverage',account_id='broker',threshold=0,warning_band=10),journal,held,valued,now=NOW)
    assert coverage.value == pytest.approx((320-360)/360*100)
    assert coverage.inputs['requirements'][0]['date'] == DAY
    assert coverage.condition == 'triggered'


def test_empty_provider_placeholders_are_not_closing_observations():
    frame = pd.DataFrame({'Open':[100.,float('nan'),110.], 'High':[100.,float('nan'),110.],
        'Low':[100.,float('nan'),110.], 'Close':[100.,float('nan'),110.],
        'Adj Close':[90.,float('nan'),110.], 'Volume':[10,0,12]},
        index=pd.to_datetime(['2026-10-07','2026-10-08','2026-10-09']))
    metadata=dict(currency='EUR',exchangeTimezoneName='Europe/Berlin')
    for basis in ('close','adjusted'):
        result=yahoo_series(frame,metadata,basis,NOW)
        assert result.dates==('2026-10-07','2026-10-09')
        assert '1 empty source rows omitted' in result.note
    frame.loc['2026-10-08','Open']=105.
    with pytest.raises(ValueError,match='Missing closing'):
        yahoo_series(frame,metadata,'close',NOW)
    frame.loc['2026-10-08','Close']=105.
    with pytest.raises(ValueError,match='Missing closing'):
        yahoo_series(frame,metadata,'adjusted',NOW)


def test_provider_does_not_force_lazy_intraday_metadata(monkeypatch):
    from collections.abc import Mapping
    from types import SimpleNamespace
    from portfolio_app.monitoring_market import MonitoringProvider
    import yfinance
    class Metadata(Mapping):
        def __iter__(self):
            raise AssertionError('Enumerating metadata forces unrelated intraday requests')
        def __len__(self): return 3
        def __getitem__(self,key):
            return {'currency':'EUR','exchangeTimezoneName':'UTC','regularMarketTime':NOW.timestamp()}[key]
    frame=pd.DataFrame({'Close':[100.,110.,120.]},index=pd.to_datetime(history().dates))
    monkeypatch.setattr(yfinance,'Ticker',lambda _:SimpleNamespace(history=lambda **kw:frame,history_metadata=Metadata()))
    assert MonitoringProvider().fetch(SeriesSpec('INVENTED'),now=NOW).closes==(100.,110.,120.)


def test_monitoring_catalog_separates_fixed_benchmarks_from_owned_market_assets():
    from portfolio_app.monitoring_catalog import BENCHMARKS, portfolio_instruments
    assert {s.split(':')[1] for s in BENCHMARKS}=={'990100','891800','892400'}
    held=pd.DataFrame([dict(ticker=t,name=n,shares=q,instrument_type=k) for t,n,q,k in [
        ('ZZ','Invented Z',1,'equity'),('AA','Invented A',2,'etf'),('ZZ','Invented Z',3,'equity'),
        ('CASH','Invented cash',1,'cash'),('OLD','Sold asset',0,'equity'),('FUT','Invented derivative',1,'future')]])
    assert list(portfolio_instruments(held))==['AA','ZZ']


def test_alert_copy_describes_condition_and_actual_observation():
    from portfolio_app.monitoring_presentation import alert_details, observation_time
    condition_text, observed=alert_details(rule(),{'value':-3.25})
    assert 'at least 2% below its 3-session SMA' in condition_text
    assert '3.25% below' in observed
    assert observation_time('2026-10-12T14:05:49.034+02:00')=='12 Oct 2026, 12:05 UTC'
