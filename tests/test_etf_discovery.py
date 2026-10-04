"""Invented provider responses and imported holdings; no live network or working data."""
from dataclasses import replace
from datetime import date, datetime, timezone
import json

import pandas as pd
import pytest

from portfolio_app.etf import FundSnapshot, expand_etfs, load_funds, matching_fund, validate_constituents
from portfolio_app.etf_discovery import Discovery, ISHARES_CATALOG, ISHARES_GERMAN_CATALOG, DWS_SITEMAP, source_from_url
from portfolio_app.etf_refresh import RefreshCoordinator, read_json
from portfolio_app.etf_setup import prepare_draft, save_draft, snapshot_writer
from portfolio_app.etf_sources import install_snapshot, refresh_snapshot, Source
from portfolio_app.fund_summary import maturity_band, composition_summary
from portfolio_app.holdings import DataError
from portfolio_app.imports import ImportOptions, normalize_table, combine_drafts, save_import
from portfolio_app.instruments import valid_isin
from portfolio_app.ishares import parse_holdings_json
from portfolio_app.positions import read_snapshot
from portfolio_app.stock_exposure import stock_exposure


def invented_isin(number):
    prefix = 'ZZ' + str(number).zfill(9)
    return next(prefix + str(n) for n in range(10) if valid_isin(prefix + str(n)))


FUND = invented_isin(999)
BOND_A, BOND_B = invented_isin(1), invented_isin(2)
PRODUCT = 'https://www.ishares.com/uk/individual/en/products/999999/invented-fund'


def point(value, **kw):
    return {'value': value, **kw}


def metadata(isin=FUND, structure='Physical', asset_class='Fixed Income'):
    return json.dumps({'fundName': 'Invented fund', 'componentsByNameMap': {
        'keyFundFacts': {'dataPointsByNameMap': {k: point(v, name=k) for k, v in {
            'isin': isin, 'productStructure': structure, 'assetClass': asset_class}.items()}},
        'fundamentalsAndRisk': {'dataPointsByNameMap': {
            'modelOad': point(2.5, name='modelOad', asOfDate=20260102)}},
        'exposureBreakdowns': {'containersByNameMap': {'rating': {'dataPointsByNameMap': {
            'type': point(['AAA', 'Unrated']), 'fund': point([60., 40.]), 'asOf': point(20260102)}}}},
    }}).encode()


def holdings_json():
    values = {'asOfDate': 20260102, 'issueName': ['Invented issuer bond one', 'Invented issuer bond two', 'Liquidity', 'Liability'],
              'assetClass': ['Fixed Income', 'Fixed Income', 'Money Market', 'Cash'],
              'marketValue': [600., 300., 120., -20.], 'holdingPercent': [60., 30., 12., -2.],
              'ticker': ['ISSUER', 'ISSUER', 'MM', 'CASH'], 'marketCurrencyCode': ['EUR'] * 4,
              'isin': [BOND_A, BOND_B, '', ''], 'maturityDate': [20270102, 20360102, None, None],
              'sectorName': ['Government', 'Government', '', ''], 'countryOfRisk': ['Invented country'] * 4}
    return json.dumps({'currencyCode': 'EUR', 'componentsByNameMap': {'holdings': {'containersByNameMap': {
        'all': {'dataPointsByNameMap': {k: point(v) for k, v in values.items()}}}}}}).encode()


class Provider:
    def __init__(self):
        self.calls = []
        self.facts = metadata()
        self.holdings = holdings_json()

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        if url in {ISHARES_CATALOG, ISHARES_GERMAN_CATALOG}:
            return json.dumps({'999999': {'isin': FUND, 'wkn': '000999', 'productView': ['etf'],
                'productPageUrl': '/uk/individual/en/products/999999/invented-fund'}}).encode()
        if url == DWS_SITEMAP:
            return b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" />'
        if 'component=holdings&' in url:
            return self.holdings
        if 'component=keyFundFacts' in url:
            return self.facts
        if any(host in url for host in ['ssga.com', 'vanguard.co.uk', 'amundietf.lu']):
            raise OSError('Synthetic fixture: provider unavailable')
        raise AssertionError('Unexpected request: ' + url)


def test_discover_new_fund_without_registry_and_reuse_catalogue():
    provider = Provider()
    resolver = Discovery(provider)
    isin, source = resolver.resolve({'isin': FUND, 'ticker': '', 'instrument_type': ''})
    assert isin == FUND and source.asset_class == 'fixed_income'
    assert source.replication == 'physical'
    assert source.summaries['Credit quality']['rows'][0]['percentage'] == 60
    assert source.summaries['modelOad']['as_of'] == '2026-01-02'
    assert resolver.resolve({'wkn': '000999'})[0] == FUND
    assert provider.calls.count(ISHARES_CATALOG) == 1
    with pytest.raises(DataError, match='No supported official source'):
        resolver.resolve({'isin': invented_isin(888), 'ticker': 'WRONG.DE'})


def test_identity_structure_and_untrusted_urls_rejected():
    provider = Provider()
    with pytest.raises(DataError, match='identity'):
        source_from_url(PRODUCT, provider, expected_isin=invented_isin(4))
    provider.facts = metadata(structure='Synthetic')
    with pytest.raises(DataError, match='replication'):
        source_from_url(PRODUCT, provider)
    for url in ['http://www.ishares.com/products/123', 'https://example.invalid/products/123',
                'https://www.ishares.com@evil.invalid/products/123']:
        with pytest.raises(DataError):
            source_from_url(url, provider)


def test_complete_bonds_keep_security_identities_net_liquidity_and_metadata():
    stamp, frame, notes = parse_holdings_json(holdings_json())
    assert stamp == date(2026, 1, 2)
    assert frame['isin'].tolist() == [BOND_A, BOND_B, '']
    assert frame.weight.tolist() == pytest.approx([.6, .3, .1])
    assert frame.instrument_type.tolist() == ['bond', 'bond', 'cash']
    assert frame.issuer.iloc[:2].tolist() == ['ISSUER', 'ISSUER']
    assert frame.maturity.iloc[0] == '2027-01-02'
    assert 'net liquidity' in notes
    doc = json.loads(holdings_json())
    doc['componentsByNameMap']['holdings']['containersByNameMap']['all']['dataPointsByNameMap']['isin']['value'].pop()
    with pytest.raises(DataError, match='Inconsistent'):
        parse_holdings_json(json.dumps(doc).encode())


def test_install_reload_refresh_preserves_ids_and_metadata(tmp_path):
    provider = Provider()
    fund = install_snapshot(tmp_path / 'etfs', FUND, fetch=provider)
    assert not fund.equity_fund and fund.provider == 'iShares'
    assert load_funds(tmp_path / 'etfs')[0].summaries == fund.summaries
    fund.constituents.loc[0, 'constituent_id'] = 'local-stable-id'
    updated = refresh_snapshot(fund, fetch=provider)
    assert updated.constituents.constituent_id.iloc[0] == 'local-stable-id'
    assert updated.constituents.maturity_band.iloc[1] == '10+ years'
    before = fund.manifest_path.read_bytes()
    provider.holdings = b'broken'
    with pytest.raises(DataError):
        refresh_snapshot(updated, fetch=provider)
    assert fund.manifest_path.read_bytes() == before


def test_import_isin_without_type_or_price_listing_then_discover(tmp_path):
    table = pd.DataFrame([['Invented fund', '2,5', FUND, '40,00']], columns=['Name', 'Units', 'ISIN', 'Price'])
    options = ImportOptions({'name': 'Name', 'shares': 'Units', 'isin': 'ISIN', 'price': 'Price'},
        defaults={'price_currency': 'EUR', 'price_date': '2026-01-02'}, quantity_unit='units', use_prices=True)
    draft = combine_drafts([normalize_table(table, options)])
    save_import(tmp_path / 'holdings.csv', draft, expected_revision=None)
    before = (tmp_path / 'holdings.csv').read_bytes()
    holdings = read_snapshot(tmp_path / 'holdings.csv').holdings
    assert 'instrument_type' not in holdings and holdings.ticker.eq('').all()
    provider = Provider()
    resolver = Discovery(provider)
    service = RefreshCoordinator(discover=resolver.resolve, install=lambda directory, isin, **kw:
        install_snapshot(directory, isin, fetch=provider, **kw), now=lambda: datetime(2026, 1, 3, tzinfo=timezone.utc))
    assert service.schedule(tmp_path, holdings, [])
    service._workers[str(tmp_path.resolve())].join(5)
    assert not service.running(tmp_path)
    assert (tmp_path / 'holdings.csv').read_bytes() == before
    funds = load_funds(tmp_path / 'etfs')
    assert matching_fund(holdings.iloc[0].to_dict(), funds) is not None
    assert read_json(tmp_path / '.cache/etf-refresh/status.json')[FUND]['status'] == 'checked'


def test_discovery_failure_throttles_and_manual_retry(tmp_path):
    holdings = pd.DataFrame([{'id': 'a', 'name': 'Invented fund', 'shares': 1, 'isin': FUND}])
    calls = []
    def fail(row):
        calls.append(row)
        raise DataError('Invented outage')
    service = RefreshCoordinator(discover=fail, now=lambda: datetime(2026, 1, 3, tzinfo=timezone.utc))
    def finish():
        service._workers[str(tmp_path.resolve())].join(3)
        assert not service.running(tmp_path)
    assert service.schedule(tmp_path, holdings, [])
    finish()
    assert not service.schedule(tmp_path, holdings, [])
    assert service.schedule(tmp_path, holdings, [], force=True)
    finish()
    assert len(calls) == 2 and not list((tmp_path / 'etfs').glob('*'))
    assert not service.schedule(tmp_path, holdings, [], demo=True, force=True)


def test_manual_review_partial_weights_lock_and_stale_edit(tmp_path):
    row = {'isin': FUND, 'name': 'Invented fund'}
    content = f'constituent_id,name,ticker,isin,weight,instrument_type\na,Invented bond,,{BOND_A},0.6,bond\n'.encode()
    draft = prepare_draft(tmp_path, row, content=content, as_of=date(2026, 1, 2), asset_class='fixed_income')
    assert not (tmp_path / 'etfs').exists()
    with snapshot_writer(tmp_path), pytest.raises(DataError, match='running'):
        save_draft(tmp_path, draft)
    fund = save_draft(tmp_path, draft)
    assert fund.constituents.weight.sum() == .6
    with pytest.raises(DataError, match='changed'):
        save_draft(tmp_path, draft)
    assert composition_summary(fund, 'Issuer')['Fund allocation %'].sum() == pytest.approx(100)


def test_manual_snapshot_keeps_source_choice_for_registered_isin(tmp_path, monkeypatch):
    from portfolio_app.etf_refresh import supported
    from portfolio_app.etf_sources import SOURCES, source_for
    provider = Provider()
    source = Discovery(provider).resolve({'isin': FUND})[1]
    monkeypatch.setitem(SOURCES, FUND, source)
    install_snapshot(tmp_path / 'etfs', FUND, source=source, fetch=provider)
    content = f'constituent_id,name,ticker,isin,weight,instrument_type\na,Invented bond,,{BOND_A},0.6,bond\n'.encode()
    draft = prepare_draft(tmp_path, {'isin': FUND, 'name': 'Invented fund'}, content=content,
                          as_of=date(2026, 1, 2), asset_class='fixed_income')
    save_draft(tmp_path, draft)
    fund = load_funds(tmp_path / 'etfs')[0]
    assert not supported(fund) and source_for(fund) is None
    holdings = pd.DataFrame([dict(id='fund', isin=FUND, shares=1)])
    service = RefreshCoordinator(refresh=lambda _: pytest.fail('Manual composition must remain unchanged'))
    assert not service.schedule(tmp_path, holdings, [fund], force=True)
    with pytest.raises(DataError, match='No configured provider'):
        refresh_snapshot(fund, fetch=lambda _: pytest.fail('Manual source must not download'))
    # An explicitly reviewed official source can restore automatic updates.
    save_draft(tmp_path, prepare_draft(tmp_path, {'isin': FUND}, product_url=PRODUCT, fetch=provider))
    assert supported(load_funds(tmp_path / 'etfs')[0])


def test_wkn_catalogue_product_must_confirm_catalogue_isin():
    provider = Provider()
    provider.facts = metadata(isin=invented_isin(888))
    with pytest.raises(DataError, match='identity'):
        Discovery(provider).resolve({'wkn': '000999'})


def test_maturity_boundaries_and_missing_data():
    assert maturity_band('', date(2026, 1, 2)) == 'Unknown'
    assert maturity_band('2025-01-01', date(2026, 1, 2)) == 'Matured / date needs review'
    assert maturity_band('2026-02-01', date(2026, 1, 2)) == 'Under 1 year'
    assert maturity_band('2030-01-02', date(2026, 1, 2)) == '3–5 years'
    assert maturity_band('2040-01-02', date(2026, 1, 2)) == '10+ years'


def test_bond_types_propagate_and_do_not_enter_company_exposure(tmp_path):
    fund = install_snapshot(tmp_path / 'etfs', FUND, fetch=Provider())
    owned = pd.DataFrame([{'id': 'fund', 'position_id': 'p', 'name': 'Invented fund', 'isin': FUND,
                          'ticker': '', 'shares': 2, 'current_value_eur': 100., 'instrument_type': 'etf'}])
    from portfolio_app.exposures import normalize_exposures
    expanded = expand_etfs(normalize_exposures(owned), [fund], owned)
    assert expanded.value.sum() == pytest.approx(100)
    assert expanded.instrument_type.tolist() == ['bond', 'bond', 'cash']
    assert stock_exposure(owned, [fund]).stock_value == 0
    assert stock_exposure(owned, [fund]).companies.empty


def test_provider_verified_aliases_ignore_ambiguous_suggestions():
    class AliasProvider(Provider):
        def __call__(self, url, **kwargs):
            if url in {ISHARES_CATALOG, ISHARES_GERMAN_CATALOG}:
                return json.dumps({'999999': {'isin': FUND, 'productView': ['etf'],
                    'productPageUrl': '/uk/individual/en/products/999999/invented-fund'}}).encode()
            content = super().__call__(url)
            if 'component=keyFundFacts' in url:
                doc = json.loads(content)
                if 'locale=de_DE' in url:
                    doc['componentsByNameMap']['keyFundFacts']['dataPointsByNameMap']['wkn'] = point('000999', name='wkn')
                doc['componentsByNameMap']['listings'] = {'dataPointsByNameMap': {'ticker': point(['INVENTED'], name='ticker'), 'exchange': point(['Xetra'], name='exchange')}}
                return json.dumps(doc).encode()
            return content
    provider = AliasProvider()
    resolver = Discovery(provider, identity_lookup=lambda _: [FUND])
    assert resolver.resolve({'wkn': '000999'})[1].wkn == '000999'
    assert resolver.resolve({'ticker': 'INVENTED.DE'})[0] == FUND
    with pytest.raises(DataError, match='No unique'):
        resolver.resolve({'wkn': '000888'})
    with pytest.raises(DataError, match='exchange-qualified'):
        resolver.resolve({'ticker': 'INVENTED'})


def test_synthetic_basket_is_separate_signed_and_not_company_or_country_exposure(tmp_path):
    from portfolio_app.etf_sources import publish_snapshot
    from portfolio_app.exposures import normalize_exposures
    from portfolio_app.company_merges import inventory
    from portfolio_app.etf import fund_classifications
    rate_isin = invented_isin(333)
    economic = pd.DataFrame([dict(constituent_id='overnight:invented', name='Invented overnight exposure', isin='', ticker='',
        weight=1., instrument_type='overnight_rate', exposure_kind='non_equity', market_currency='EUR')])
    basket = pd.DataFrame([dict(constituent_id='basket:a', name='Invented basket equity', isin=BOND_A, ticker='',
        weight=.8, instrument_type='equity', country='Invented country'),
        dict(constituent_id='basket:b', name='Invented basket bond', isin=BOND_B, ticker='', weight=.21, instrument_type='bond'),
        dict(constituent_id='basket:c', name='Invented short', isin='', ticker='', weight=-.01, instrument_type='bond')])
    fund = FundSnapshot('rate', 'Invented overnight fund', rate_isin, (), date.min, 'https://example.invalid',
        pd.DataFrame(columns=['isin', 'constituent_id']), tmp_path / 'etfs/rate.yaml', asset_class='money_market',
        replication='synthetic', breakdown_basis='economic')
    saved = publish_snapshot(fund, economic, date(2026, 1, 2), basket=basket)
    loaded = load_funds(tmp_path / 'etfs')[0]
    assert loaded.basket.weight.min() == -.01
    with pytest.raises(DataError, match='weights'):
        validate_constituents(basket)
    owned = pd.DataFrame([dict(id='fund', position_id='p', name='Invented overnight fund', isin=rate_isin,
                              ticker='', shares=1., current_value_eur=100., instrument_type='etf')])
    expanded = expand_etfs(normalize_exposures(owned), [loaded], owned)
    assert expanded.asset_id.tolist() == ['overnight:invented'] and expanded.value.sum() == 100
    assert inventory(owned, [loaded], {}) == []
    assert stock_exposure(owned, [loaded]).stock_value == 0
    assert expanded.instrument_type.tolist() == ['overnight_rate']
    assert expanded.exposure_kind.tolist() == ['non_equity']
    assert all('geography' not in labels for labels in fund_classifications({}, [loaded], owned).values())
    assert saved.basket.weight.sum() == pytest.approx(1)


def test_discovery_deduplicates_accounts_and_obeys_other_writer(tmp_path):
    provider = Provider()
    resolver = Discovery(provider)
    holdings = pd.DataFrame([dict(id='fund', name='Invented fund', shares=1, isin=FUND, account=a) for a in ['A', 'B']])
    service = RefreshCoordinator(discover=resolver.resolve, install=lambda directory, isin, **kw:
        install_snapshot(directory, isin, fetch=provider, **kw), now=lambda: datetime(2026, 1, 3, tzinfo=timezone.utc))
    with snapshot_writer(tmp_path):
        assert service.schedule(tmp_path, holdings, [])
        service._workers[str(tmp_path.resolve())].join(3)
        assert not provider.calls
    assert service.schedule(tmp_path, holdings, [])
    service._workers[str(tmp_path.resolve())].join(3)
    assert len(load_funds(tmp_path / 'etfs')) == 1
    assert sum('component=holdings&' in u for u in provider.calls) == 1


def test_old_and_future_drafts_and_local_classification_priority(tmp_path):
    from portfolio_app.etf import fund_classifications
    from datetime import timedelta
    provider = Provider()
    fund = install_snapshot(tmp_path / 'etfs', FUND, fetch=provider)
    for invalid in [date(2020, 1, 1), date.today() + timedelta(days=1)]:
        with pytest.raises(DataError, match='dates'):
            prepare_draft(tmp_path, {'isin': FUND, 'name': 'Invented fund'}, content=fund.constituents.to_csv(index=False).encode(),
                          as_of=invalid, asset_class='fixed_income')
    held = pd.DataFrame([dict(id='direct', name='Invented direct bond', isin=BOND_A, ticker='')])
    result = fund_classifications({'direct': {'geography': (('Custom', 'Place'),)}}, [fund], held)
    assert result['direct']['geography'] == (('Custom', 'Place'),)
    assert result['direct']['issuer'] == (('ISSUER',),)
    assert maturity_band('2027-01-02', date(2026, 1, 2)) == '1–3 years'
    assert maturity_band('2029-01-02', date(2026, 1, 2)) == '3–5 years'
    assert maturity_band('2031-01-02', date(2026, 1, 2)) == '5–10 years'
    assert maturity_band('2036-01-02', date(2026, 1, 2)) == '10+ years'


def test_later_wkn_import_attaches_verified_alias_to_existing_snapshot(tmp_path):
    provider = Provider()
    fund = install_snapshot(tmp_path / 'etfs', FUND, fetch=provider)
    # A previous ISIN-only snapshot may not yet know its WKN.
    import yaml
    raw = yaml.safe_load(fund.manifest_path.read_text())
    raw['wkn'] = ''
    fund.manifest_path.write_text(yaml.safe_dump(raw))
    funds = load_funds(tmp_path / 'etfs')
    source = Discovery(provider).resolve({'wkn': '000999'})[1]
    holdings = pd.DataFrame([dict(id='imported', name='Invented WKN import', shares=1, wkn='000999', isin='', ticker='')])
    service = RefreshCoordinator(discover=lambda _: (FUND, source), install=lambda *a, **kw: pytest.fail('Must reuse composition'))
    assert service.schedule(tmp_path, holdings, funds)
    service._workers[str(tmp_path.resolve())].join(3)
    loaded = load_funds(tmp_path / 'etfs')
    assert matching_fund(holdings.iloc[0].to_dict(), loaded) is not None
    pd.testing.assert_frame_equal(funds[0].constituents, loaded[0].constituents)


def test_cash_in_equity_fund_is_not_declared_equity():
    from portfolio_app.exposures import normalize_exposures
    constituents = pd.DataFrame([dict(constituent_id='share', name='Invented share', ticker='', isin='', weight=.9, instrument_type='equity'),
                                 dict(constituent_id='cash', name='Invented cash', ticker='', isin='', weight=.1, instrument_type='cash')])
    fund = FundSnapshot('invented', 'Invented equity fund', FUND, (), date(2026, 1, 2), '', constituents, equity_fund=True)
    holdings = pd.DataFrame([dict(id='fund', position_id='p', name='Invented equity fund', ticker='', isin=FUND,
                                 current_value_eur=100., shares=1., instrument_type='etf', wkn='000999')])
    result = expand_etfs(normalize_exposures(holdings), [fund], holdings)
    assert result.loc[result.exposure_kind.eq('equity'), 'value'].sum() == 90
    assert result.loc[result.exposure_kind.eq('non_equity'), 'value'].sum() == 10
    assert result.wkn.eq('').all()


def test_workspace_copy_preserves_basket_and_refuses_external_basket(tmp_path):
    import yaml
    from portfolio_app.etf_sources import publish_snapshot
    from portfolio_app.workspace import copy_workspace, inventory
    directory = tmp_path / 'synthetic source'
    fund = install_snapshot(directory / 'etfs', FUND, fetch=Provider())
    (directory / 'holdings.csv').write_text(f'id,name,isin,shares\nfund,Invented fund,{FUND},1\n', encoding='utf-8')
    basket = fund.constituents.copy()
    publish_snapshot(fund, fund.constituents, fund.as_of, basket=basket, prior=fund.manifest_path.read_bytes())
    copied = copy_workspace(directory, tmp_path / 'copied')
    assert inventory(directory) == inventory(copied)
    pd.testing.assert_frame_equal(load_funds(directory / 'etfs')[0].basket, load_funds(copied / 'etfs')[0].basket)
    raw = yaml.safe_load(fund.manifest_path.read_text(encoding='utf-8'))
    raw['basket_file'] = '../../external-synthetic.csv'
    fund.manifest_path.write_text(yaml.safe_dump(raw), encoding='utf-8')
    with pytest.raises(DataError, match='inside the workspace'):
        copy_workspace(directory, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()
