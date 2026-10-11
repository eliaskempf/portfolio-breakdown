"""Explicitly invented observations; never network or working portfolio data."""
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from portfolio_app.derivative_market import (CATALOG, SettlementCache, discover_contracts,
    fetch_settlement, make_contract, manual_contract, manual_mark, statistics_request)
from portfolio_app.holdings import DataError
from portfolio_app.storage import revision


def synthetic_statistics(product='FMWN'):
    return {'meta': {'productCode': product, 'isin': CATALOG[product].product_isin, 'productType': 'F'},
            'header': {'tradingDates': ['03-01-2001 12:00', '02-01-2001 12:00']},
            'dataRows': [{'date': '20010102', 'contractType': 'D', 'dSettle': 1},
                         {'date': '20010316', 'contractType': 'M', 'dSettle': 410, 'volume': 0},
                         {'date': '20010615', 'contractType': 'M', 'dSettle': 430, 'volume': 0}]}


@pytest.mark.parametrize('product', list(CATALOG))
def test_all_catalog_products_select_exact_maturity_even_without_trades(product):
    contract = make_contract(product, '2001-06-15')
    urls = []
    def fetch(url):
        urls.append(url)
        return json.dumps(synthetic_statistics(product))
    quote = fetch_settlement(contract, '2001-01-02', fetch)
    assert quote.price == Decimal('430')
    assert quote.contract_id == contract.id
    assert quote.date == '2001-01-02'
    assert quote.kind == 'daily_settlement'
    assert 'busdate=20010102' in urls[0]
    assert quote.source == urls[0]


def test_catalog_economics_and_currency_are_not_inferred_from_shared_index_name():
    assert [CATALOG[p].multiplier for p in ('FMWN', 'FMWO', 'FMWB')] == [100, 10, 5]
    assert [CATALOG[p].currency for p in ('FMWN', 'FMWO', 'FMWB')] == ['EUR', 'USD', 'GBP']
    assert [CATALOG[p].multiplier for p in ('FDAX', 'FDXM', 'FDXS')] == [25, 5, 1]
    assert CATALOG['FESX'].return_convention == 'price'
    assert CATALOG['FDAX'].return_convention == 'gross'
    with pytest.raises(TypeError):
        CATALOG['INVENTED'] = CATALOG['FMWN']


@pytest.mark.parametrize('change', ['identity', 'missing', 'ambiguous', 'bad_price', 'wrong_date', 'malformed_row'])
def test_fail_closed_on_unsupported_statistics(change):
    raw = synthetic_statistics()
    if change == 'identity': raw['meta']['productCode'] = 'FMWO'
    if change == 'missing': raw.pop('dataRows')
    if change == 'ambiguous': raw['dataRows'].append(raw['dataRows'][-1])
    if change == 'bad_price': raw['dataRows'][-1]['dSettle'] = 'NaN'
    if change == 'wrong_date': raw['header']['tradingDates'] = ['03-01-2001 12:00']
    if change == 'malformed_row': raw['dataRows'] = [42]
    with pytest.raises(DataError):
        fetch_settlement(make_contract('FMWN', '2001-06-15'), '2001-01-02', lambda _: json.dumps(raw))


def test_mismatched_currency_or_economics_cannot_use_catalog_provider():
    contract = replace(make_contract('FMWN', '2001-06-15'), currency='USD')
    with pytest.raises(DataError, match='specification'):
        fetch_settlement(contract, '2001-01-02', lambda _: pytest.fail('No network request expected'))


def test_final_settlement_is_manual_and_does_not_assume_maturity_calendar():
    contract = make_contract('FMWN', '2001-03-16', final_settlement_date='2001-03-19')
    assert contract.last_trade_date != contract.final_settlement_date
    with pytest.raises(DataError, match='unsupported'):
        fetch_settlement(contract, '2001-03-19', lambda _: json.dumps(synthetic_statistics()))
    observed = manual_mark(contract, '2001-03-19', '425', kind='final_settlement', source='Invented final settlement')
    assert observed.kind == 'final_settlement'
    assert observed.price == Decimal('425')


def test_manual_unknown_contract_works_without_catalog_or_network():
    contract = manual_contract('SYNTHETIC', '2001-06-15', 'GBP', '2', '.5', source='Invented specification')
    observed = manual_mark(contract, '2001-01-02', '100.5')
    assert observed.contract_id == contract.id and observed.kind == 'manual'
    assert contract.multiplier == Decimal('2')


def test_discovery_returns_actual_monthly_dates_not_invented_quarters():
    spec = CATALOG['FDXS']
    html = f'<script data-product="{spec.statistics_id}">{{"productId":"{spec.statistics_id}","busDate":"20010102"}}</script>'
    raw = synthetic_statistics('FDXS')
    raw['dataRows'][-1]['date'] = '20010614'
    contracts = discover_contracts('FDXS', lambda url: json.dumps(raw) if '/api/' in url else html)
    assert [c.expiry for c in contracts] == ['2001-03-16', '2001-06-14']
    with pytest.raises(DataError, match='business date'):
        statistics_request(html.replace(spec.statistics_id, '99999'), spec)


def test_cache_is_exact_date_and_contract_and_failure_retains_saved_quote(tmp_path):
    cache = SettlementCache(tmp_path / '.cache' / 'derivatives')
    contract = make_contract('FMWN', '2001-06-15')
    result = cache.get(contract, '2001-01-02', fetch=lambda _: json.dumps(synthetic_statistics()), as_of='2001-01-03')
    assert result.status == 'fresh'
    assert cache.load(make_contract('FMWN', '2001-03-16'), '2001-01-02') is None
    assert cache.load(contract, '2001-01-03') is None
    cached = cache.get(contract, '2001-01-02', as_of='2001-01-03', fetch=lambda _: pytest.fail('Cache must be used'))
    assert cached.status == 'cached'
    before = {p: p.read_bytes() for p in tmp_path.rglob('*.json')}
    failed = cache.get(contract, '2001-01-02', refresh=True, fetch=lambda _: 'unavailable', as_of='2001-01-03')
    assert failed.status == 'stale' and failed.observation == result.observation
    assert {p: p.read_bytes() for p in tmp_path.rglob('*.json')} == before
    assert cache.get(contract, '2001-01-02', as_of='2001-02-01').status == 'stale'
    assert not (tmp_path / 'accounts.json').exists()
    with pytest.raises(DataError, match='later'):
        cache.get(contract, '2001-01-03', as_of='2001-01-02')


def test_cache_revision_and_corrupt_identity(tmp_path):
    cache = SettlementCache(tmp_path)
    contract = make_contract('FMWN', '2001-06-15')
    quote = manual_mark(contract, '2001-01-02', 430)
    cache.save(contract, quote, None)
    with pytest.raises(DataError, match='changed'):
        cache.save(contract, replace(quote, price=440), None)
    path = next(tmp_path.rglob('*.json'))
    cache.save(contract, replace(quote, price=440), revision(path))
    raw = json.loads(path.read_text())
    raw['observation']['contract_id'] = 'EUREX:SYNTHETIC:2001-06-15'
    path.write_text(json.dumps(raw))
    with pytest.raises(DataError, match='Invalid saved'):
        cache.load(contract, '2001-01-02')
