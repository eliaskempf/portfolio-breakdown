"""Invented provider responses: no live requests, market caches or personal data."""
from datetime import datetime, timezone
import json

import pytest
from portfolio_app.derivative_market import CATALOG, make_contract
from portfolio_app.eurex_quotes import fetch_quote, monthly_notation, parse_quote, HOST
from portfolio_app.holdings import DataError

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def invented_response(code='FMWN'):
    product = CATALOG[code]
    contract = make_contract(code, '2026-12-18')
    metadata = dict(sourceIdentifier='invented-notation', normalizedSymbol=f'{code}/2612',
        market={'name': 'Eurex', 'id': 6}, contractSize=str(product.multiplier),
        settlement={'valueUnit': {'id': 42, 'code': product.currency}},
        lifeCycle={'maturity': {'interval': 'monthly', 'date': '2026-12-21T00:00:00Z'},
                   'expiration': {'month': 12, 'year': 2026}},
        **{'class': {'symbol': code, 'isin': product.product_isin}})
    quote = dict(sourceIdentifier='invented-notation', idNotation='invented-notation',
        currency={'id': 42}, market={'id': 6}, quality='DLY',
        latest={'time': '2026-10-15T11:45:00Z', 'price': 1000, 'tradingVolume': 2},
        accumulated={'tradingVolume': 20})
    return contract, metadata, quote


@pytest.mark.parametrize('code', CATALOG)
def test_exact_contract_currency_time_and_delay(code):
    contract, meta, raw = invented_response(code)
    result = parse_quote(contract, 'invented-notation', meta, raw, now=NOW)
    assert result.contract_id == contract.id
    assert result.quote_at == '2026-10-15T11:45:00+00:00'
    assert result.observed_at == NOW.isoformat()
    assert result.delay_minutes == 15 and result.kind == 'mark'
    assert result.date == '2026-10-15'


@pytest.mark.parametrize('where,path,value', [
    ('meta', ('class','isin'), 'WRONG'), ('meta', ('class','symbol'), 'WRONG'),
    ('meta', ('settlement','valueUnit','code'), 'USD'),
    ('meta', ('lifeCycle','expiration','month'), 3),
    ('meta', ('lifeCycle','maturity','interval'), 'daily'),
    ('meta', ('contractSize',), '25'), ('meta', ('normalizedSymbol',), 'FMWN/2703'),
    ('raw', ('idNotation',), 'nearby-contract'), ('raw', ('currency','id'), 999),
    ('raw', ('latest','time'), ''), ('raw', ('latest','time'), '2026-10-15T13:00:00Z'),
    ('raw', ('latest','time'), '2026-10-15T11:45:00'),
    ('raw', ('latest','price'), 0), ('raw', ('latest','price'), 'NaN'),
    ('raw', ('latest','tradingVolume'), None), ('raw', ('accumulated','tradingVolume'), 0),
    ('raw', ('quality',), 'UNKNOWN'),
])
def test_rejects_unverified_identity_time_or_settlement_like_price(where, path, value):
    contract, meta, raw = invented_response()
    target = meta if where == 'meta' else raw
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(DataError):
        parse_quote(contract, 'invented-notation', meta, raw, now=NOW)


def test_maturity_bridge_is_unique_monthly_series_not_nearby_or_daily():
    contract, _, _ = invented_response()
    row = dict(idNotation='invented-notation', idMaturityInterval=1, dateMaturity='2026-12-21T00:00:00Z')
    assert monthly_notation({'contracts': [row, dict(row, idMaturityInterval=3)]}, contract) == 'invented-notation'
    for rows in ([row, row], [dict(row, idMaturityInterval=3)], [dict(row, dateMaturity='2027-03-22')]):
        with pytest.raises(DataError):
            monthly_notation({'contracts': rows}, contract)


def test_fetch_verifies_canonical_expiry_and_uses_only_exact_notation():
    contract, meta, quote = invented_response()
    product = CATALOG['FMWN']
    html = '<script data-product="33934">' + json.dumps(dict(isin=product.product_isin,
        factsetHostUrl=HOST, connectionToken='invented-public-session')) + '</script>'
    html += '<script data-product="33934">{"productId":"33934","busDate":"20261014"}</script>'
    statistics = dict(meta=dict(productCode='FMWN', isin=product.product_isin),
        header={'tradingDates':['14-10-2026 12:00']}, dataRows=[dict(contractType='M', date='20261218')])
    calls = []
    class Session:
        def __init__(self, token): assert token == 'invented-public-session'
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, path, **params):
            calls.append((path, params))
            if path == 'customer/contract/get':
                return {'contracts': [dict(idNotation='invented-notation', idMaturityInterval=1, dateMaturity='2026-12-21')]}
            assert params == dict(identifier='invented-notation', identifierType='idNotation')
            return meta if path == 'future/get' else quote
    def download(url): return html if url == product.source else json.dumps(statistics)
    assert fetch_quote(contract, fetch=download, session=Session, now=NOW).price == 1000
    assert len(calls) == 3
    statistics['dataRows'][0]['date'] = '20261217'
    with pytest.raises(DataError):
        fetch_quote(contract, fetch=download, session=Session, now=NOW)
    assert len(calls) == 3
