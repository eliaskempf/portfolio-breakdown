"""Invented MSCI-shaped responses; unit tests never request live index data."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from portfolio_app.history import HistoryResult, HistoryService
from portfolio_app.msci_history import MarketHistoryProvider, parse_levels


def payload(currency='EUR'):
    return dict(msci_index_code='990100', index_variant_type='NETR', ISO_currency_symbol=currency,
                indexes={'INDEX_LEVELS':[{'calc_date':20260103,'level_eod':101.5},
                                         {'calc_date':20260102,'level_eod':100.0}]})


@pytest.mark.parametrize('currency', ['EUR','USD','GBP'])
@pytest.mark.parametrize('period', ['1mo','6mo','1y','5y','max'])
def test_provider_requests_exact_msci_net_variant_and_sorts(currency,period):
    calls=[]
    def fetch(params):
        calls.append(params)
        return payload(currency)
    result=MarketHistoryProvider(fetch=fetch,today=lambda:date(2026,1,31)).history(f'MSCI:990100:NETR:{currency}',period)
    assert result.currency==currency and result.prices==(100.,101.5)
    assert result.dates==('2026-01-02','2026-01-03')
    assert calls[0]['index_codes']=='990100' and calls[0]['index_variant']=='NETR'
    assert calls[0]['currency_symbol']==currency and calls[0]['data_frequency']=='DAILY'
    assert calls[0]['end_date']=='20260131'
    if period=='max':
        assert calls[0]['start_date']=='20000101' and 'earlier history is not included' in result.note


@pytest.mark.parametrize('field,value', [('msci_index_code','892400'),('index_variant_type','STRD'),('ISO_currency_symbol','USD')])
def test_mismatched_identity_is_rejected(field,value):
    raw=payload();raw[field]=value
    with pytest.raises(ValueError,match='metadata'):
        parse_levels(raw,'EUR',date(2026,1,1),date(2026,1,31))


@pytest.mark.parametrize('mutation', ['empty','duplicate','nonfinite','negative','boolean','out_of_range'])
def test_invalid_observations_rejected(mutation):
    raw=deepcopy(payload());rows=raw['indexes']['INDEX_LEVELS']
    if mutation=='empty':
        rows.clear()
    elif mutation=='duplicate':
        rows.append(rows[0].copy())
    elif mutation=='out_of_range':
        rows[0]['calc_date']=20270101
    else:
        rows[0]['level_eod']={'nonfinite':float('nan'),'negative':-1,'boolean':True}[mutation]
    with pytest.raises(ValueError):
        parse_levels(raw,'EUR',date(2026,1,1),date(2026,1,31))


def test_failure_keeps_verified_cached_index_without_etf_fallback(tmp_path):
    calls=[];failed=[False];now=[datetime(2026,1,31,tzinfo=timezone.utc)]
    def fetch(params):
        if failed[0]:
            raise ConnectionError('Synthetic outage')
        return payload()
    yahoo=SimpleNamespace(history=lambda *a: calls.append(a))
    provider=MarketHistoryProvider(fetch=fetch,today=lambda:date(2026,1,31),yahoo=yahoo)
    service=HistoryService(provider,tmp_path,now=lambda:now[0])
    first=service.get('MSCI:990100:NETR:EUR')
    assert first.status=='fresh'
    failed[0]=True;now[0]+=timedelta(hours=2)
    stale=service.get('MSCI:990100:NETR:EUR')
    assert stale.status=='stale' and stale.prices==first.prices and not calls
    with pytest.raises(ValueError):
        provider.history('MSCI:990100:STRD:EUR','1y')


def test_ordinary_history_still_uses_yahoo():
    calls=[]
    expected=HistoryResult(note='Synthetic ordinary history')
    yahoo=SimpleNamespace(history=lambda *args:(calls.append(args) or expected))
    provider=MarketHistoryProvider(fetch=lambda _:pytest.fail('Unexpected MSCI request'),yahoo=yahoo)
    assert provider.history('SYNTHETIC','1y') is expected
    assert calls==[('SYNTHETIC','1y')]
