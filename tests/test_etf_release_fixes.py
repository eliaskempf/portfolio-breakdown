"""Public fund identifiers with deliberately invented compositions; no live data."""
from datetime import date
from types import SimpleNamespace
import json

import pandas as pd
import pytest

from portfolio_app import amundi, ishares
from portfolio_app.etf import fund_breakdown, load_funds, matching_fund
from portfolio_app.etf_discovery import Discovery, ISHARES_CATALOG, source_from_url
from portfolio_app.etf_setup import prepare_draft, save_draft
from portfolio_app.etf_sources import Source, download, install_snapshot, refresh_snapshot, retrieve_snapshot
from portfolio_app.holdings import DataError
from portfolio_app.instruments import InstrumentSearch, catalog_search, result_groups
from portfolio_app.stock_exposure import stock_exposure
from test_etf_discovery import Provider, holdings_json, metadata, point, invented_isin


@pytest.mark.parametrize('isin,ticker', [('DE0005933931', 'EXS1.DE'), ('IE00BDBRDM35', 'EUNA.DE')])
@pytest.mark.parametrize('remote', ['empty', 'mutualfund', 'offline', 'duplicate'])
def test_verified_search_alias_survives_remote_failure(monkeypatch, tmp_path, isin, ticker, remote):
    def search(*args, **kwargs):
        if remote == 'offline':
            raise OSError('Invented outage')
        quotes = [] if remote == 'empty' else [dict(symbol=ticker if remote == 'duplicate' else 'INVENTED',
                                                    quoteType='ETF' if remote == 'duplicate' else 'MUTUALFUND')]
        return SimpleNamespace(quotes=quotes)
    monkeypatch.setattr('portfolio_app.instruments.yf.Search', search)
    result = InstrumentSearch(tmp_path).search(isin)
    assert len(result) == 1 and result[0].ticker == ticker
    assert result[0].isin == isin and result[0].currency == 'EUR'
    assert result_groups(result)[0]['kind'] == 'ETF'
    assert catalog_search(ticker)[0].isin == isin


class AliasProvider(Provider):
    def __init__(self, isin, *, inconsistent=False):
        super().__init__()
        self.isin = isin
        self.facts = metadata(isin=isin)
        if inconsistent:
            self.holdings = bond_payload()

    def __call__(self, url):
        if url == ISHARES_CATALOG:
            return json.dumps({'999999': dict(isin=self.isin, productView=['etf'],
                productPageUrl='/uk/individual/en/products/999999/invented-fund', localExchangeTicker='DIFFERENT')}).encode()
        return super().__call__(url)


@pytest.mark.parametrize('isin,ticker', [('DE0005933931', 'EXS1.DE'), ('IE00BDBRDM35', 'EUNA.DE')])
def test_alias_discovery_verifies_issuer_and_matches_saved_snapshot(tmp_path, isin, ticker):
    provider = AliasProvider(isin)
    resolver = Discovery(provider, identity_lookup=lambda _: pytest.fail('Verified alias must not need suggestions'))
    resolved, source = resolver.resolve({'ticker': ticker})
    assert resolved == isin and ticker in source.tickers
    fund = install_snapshot(tmp_path, isin, source=source, fetch=provider)
    assert matching_fund({'ticker': ticker}, [fund]) is not None
    provider.facts = metadata(isin=invented_isin(44))
    with pytest.raises(DataError, match='No unique'):
        resolver.resolve({'ticker': ticker})


def bond_payload(*, consistent=False):
    # Twelve invented bonds; each carries 2% of whole-fund NAV. The partial
    # export's own market-value sum must never become the NAV denominator.
    points = {k: point(v) for k, v in {
        'asOfDate': 20260102, 'issueName': [f'Invented bond {i}' for i in range(12)],
        'assetClass': ['Fixed Income'] * 12, 'marketValue': [20.] * 12,
        'holdingPercent': [100 / 12 if consistent else 2.] * 12,
        'ticker': [f'ISSUER{i}' for i in range(12)], 'marketCurrencyCode': ['EUR'] * 12,
        'isin': [invented_isin(i + 10) for i in range(12)],
        'maturityDate': [20300102] * 12,
    }.items()}
    return json.dumps({'currencyCode': 'EUR', 'componentsByNameMap': {'holdings': {
        'containersByNameMap': {'all': {'dataPointsByNameMap': points}}}}}).encode()


def test_bond_fallback_preserves_whole_fund_weights_and_explicit_other():
    with pytest.raises(DataError, match='disagree'):
        ishares.parse_holdings_json(bond_payload())
    stamp, frame, notes = ishares.parse_holdings_json(bond_payload(), allow_partial_bonds=True)
    assert len(frame) == 10 and frame.weight.tolist() == pytest.approx([.02] * 10)
    assert frame.maturity.eq('2030-01-02').all() and frame.instrument_type.eq('bond').all()
    assert 'Partial breakdown' in notes
    from portfolio_app.etf import FundSnapshot
    fund = FundSnapshot('invented', 'Invented bonds', invented_isin(55), (), stamp, '', frame)
    assert fund_breakdown(fund).weight.iloc[-1] == pytest.approx(.8)
    from portfolio_app.fund_summary import composition_summary
    summary = composition_summary(fund, 'Issuer')
    assert summary.loc[summary.Issuer.eq('Other'), 'Fund allocation %'].item() == pytest.approx(80)
    _, full, notes = ishares.parse_holdings_json(bond_payload(consistent=True), allow_partial_bonds=True)
    assert len(full) == 13 and full.weight.sum() == pytest.approx(1)
    assert 'Partial breakdown' not in notes


@pytest.mark.parametrize('field,value', [('isin', 'invalid'), ('holdingPercent', -2.), ('marketValue', -20.),
    ('assetClass', 'Unrecognized derivative'), ('issueName', ''), ('maturityDate', 123), ('holdingPercent', float('nan'))])
def test_bond_fallback_does_not_hide_invalid_rows(field, value):
    doc = json.loads(bond_payload())
    points = doc['componentsByNameMap']['holdings']['containersByNameMap']['all']['dataPointsByNameMap']
    points[field]['value'][0] = value
    with pytest.raises(DataError):
        ishares.parse_holdings_json(json.dumps(doc).encode(), allow_partial_bonds=True)


def test_bond_fallback_is_scoped_and_failed_refresh_preserves_files(tmp_path):
    isin = 'IE00BDBRDM35'
    provider = AliasProvider(isin, inconsistent=True)
    fund = install_snapshot(tmp_path, isin, fetch=provider)
    assert len(fund.constituents) == 10 and 'Partial breakdown' in fund.notes
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    provider.holdings = b'{}'
    with pytest.raises(DataError):
        refresh_snapshot(fund, fetch=provider)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    provider = AliasProvider('DE0005933931', inconsistent=True)
    with pytest.raises(DataError, match='disagree'):
        install_snapshot(tmp_path, provider.isin, fetch=provider)


def test_bond_fallback_keeps_missing_isins_as_stable_provider_identities():
    doc = json.loads(bond_payload())
    points = doc['componentsByNameMap']['holdings']['containersByNameMap']['all']['dataPointsByNameMap']
    points['isin']['value'][0:2] = [None, '-']
    content = json.dumps(doc).encode()
    _, frame, _ = ishares.parse_holdings_json(content, allow_partial_bonds=True)
    assert frame['isin'].iloc[:2].tolist() == ['', '']
    assert frame.constituent_id.iloc[:2].str.startswith('ishares:').all()
    assert frame.constituent_id.is_unique and frame.ticker.eq('').all()
    _, repeated, _ = ishares.parse_holdings_json(content, allow_partial_bonds=True)
    assert repeated.constituent_id.tolist() == frame.constituent_id.tolist()


def amundi_payload():
    return {'products': [{'productId': amundi.ISIN, 'characteristics': {
        'ISIN': amundi.ISIN, 'SHARE_MARKETING_NAME': 'Invented overnight fund',
        'BENCHMARK_NAME': amundi.BENCHMARK, 'BASE_CURRENCY': 'EUR',
        'REPLICATION_METHODOLOGY': 'Indirect (Unfunded swap)', 'POSITION_AS_OF_DATE': '2026-01-02',
    }, 'breakDowns': [{'aggregationField': 'FUND_TOP10', 'breakDownData': [
        {'aggregationName': 'Invented substitute security', 'weight': 0., 'adjustedWeight': .15,
         'additionalProperties': {'isin': invented_isin(70), 'currency': 'USD', 'bbg': 'INVENTED'}}]}]}]}


class AmundiProvider:
    def __init__(self):
        self.payload = amundi_payload()

    def __call__(self, url, *, json_body=None):
        assert url == amundi.API_URL and json_body == amundi.request_body()
        return json.dumps(self.payload).encode()


def test_amundi_install_reload_refresh_and_economic_separation(tmp_path):
    provider = AmundiProvider()
    draft = prepare_draft(tmp_path, {'isin': amundi.ISIN}, fetch=provider)
    saved = save_draft(tmp_path, draft)
    fund = load_funds(tmp_path / 'etfs')[0]
    assert fund.provider == 'Amundi' and fund.breakdown_basis == 'economic'
    assert fund.basket.weight.tolist() == [.15]  # adjustedWeight, not weight=0
    assert fund.constituents.weight.tolist() == [1.]
    assert 'ESTR Compounded Index' in fund.constituents.name.iloc[0]
    assert '+8.5' not in fund.constituents.name.iloc[0]
    owned = pd.DataFrame([dict(id='fund', position_id='p', name='Invented fund', isin=amundi.ISIN,
                              shares=1, current_value_reporting=100., instrument_type='etf')])
    assert stock_exposure(owned, [fund]).stock_value == 0
    from portfolio_app.exposures import normalize_exposures
    from portfolio_app.etf import expand_etfs
    expanded = expand_etfs(normalize_exposures(owned), [fund], owned)
    assert expanded.value.sum() == 100 and expanded.instrument_type.tolist() == ['overnight_rate']
    assert expanded.asset_id.tolist() == ['overnight:eur-estr']
    provider.payload['products'][0]['characteristics']['POSITION_AS_OF_DATE'] = '2026-01-03'
    updated = refresh_snapshot(saved, fetch=provider)
    assert updated.as_of == date(2026, 1, 3)
    before = updated.manifest_path.read_bytes()
    provider.payload['products'][0]['characteristics']['BENCHMARK_NAME'] = 'Unreviewed benchmark'
    with pytest.raises(DataError, match='benchmark'):
        refresh_snapshot(updated, fetch=provider)
    assert updated.manifest_path.read_bytes() == before


@pytest.mark.parametrize('field,value', [('ISIN', invented_isin(90)), ('BASE_CURRENCY', 'USD'),
    ('BENCHMARK_NAME', 'Different index'), ('REPLICATION_METHODOLOGY', 'Physical'), ('POSITION_AS_OF_DATE', 'invalid')])
def test_amundi_identity_and_economic_validation(field, value):
    provider = AmundiProvider()
    provider.payload['products'][0]['characteristics'][field] = value
    with pytest.raises(DataError):
        Discovery(provider).resolve({'isin': amundi.ISIN})


def test_amundi_missing_basket_and_invalid_basket_are_distinct():
    provider = AmundiProvider()
    _, source = source_from_url(amundi.PRODUCT_URL, provider, expected_isin=amundi.ISIN)
    provider.payload['products'][0]['breakDowns'] = []
    _, economic, notes, basket = retrieve_snapshot(amundi.ISIN, source, provider)
    assert basket is None and economic.weight.sum() == 1 and 'unavailable' in notes
    provider.payload = amundi_payload()
    provider.payload['products'][0]['breakDowns'][0]['breakDownData'][0]['adjustedWeight'] = 15.
    with pytest.raises(DataError, match='weights'):
        retrieve_snapshot(amundi.ISIN, source, provider)
    with pytest.raises(DataError, match='identity'):
        source_from_url(amundi.PRODUCT_URL, AmundiProvider(), expected_isin=invented_isin(99))


def test_xtrackers_keeps_distinct_overnight_benchmark():
    # Parser returns an invented substitute basket through the existing GET API.
    source = Source('invented', 'Invented fund', (), 'Xtrackers', 'https://example.invalid',
                    lambda _: ishares.parse_holdings_json(holdings_json()), breakdown_basis='economic')
    _, frame, _, basket = retrieve_snapshot('LU0290358497', source, lambda _: b'unused')
    assert '+8.5' in frame.name.iloc[0] and basket is not None


def test_downloader_keeps_get_default_and_supports_read_only_post(monkeypatch):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return b'invented response'
    def open_request(request, *, timeout):
        calls.append(request)
        assert timeout == 25
        return Response()
    monkeypatch.setattr('portfolio_app.etf_sources.urlopen', open_request)
    download('https://example.invalid')
    download('https://example.invalid', json_body={'productIds': ['invented']})
    assert calls[0].get_method() == 'GET'
    assert calls[1].get_method() == 'POST' and json.loads(calls[1].data) == {'productIds': ['invented']}
