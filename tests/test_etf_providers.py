"""Invented provider responses only: discovery, pagination, weights and refresh."""
from copy import deepcopy
from datetime import date
from io import BytesIO
import json
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pytest

from portfolio_app import amundi, spdr, vanguard
from portfolio_app.etf_discovery import Discovery, source_from_url
from portfolio_app.etf_sources import install_snapshot, refresh_snapshot, retrieve_snapshot
from portfolio_app.etf import fund_breakdown
from portfolio_app.holdings import DataError
from test_etf_discovery import invented_isin

FUND = invented_isin(901)
STAMP = '2026-01-02'
VG_URL = 'https://www.vanguard.co.uk/professional/product/etf/equity/9999/invented-fund'
SS_URL = 'https://www.ssga.com/de/en_gb/intermediary/etfs/invented-fund'
AM_URL = 'https://www.amundietf.lu/en/individual/products/equity/invented/' + FUND.lower()


def encoded(value):
    return json.dumps(value).encode()


def amundi_document():
    rows = [dict(compositionCharacteristics=dict(date=STAMP, type='EQUITY_ORDINARY', isin=invented_isin(i),
        bbg=f'INVENTED{i}', name=f'Invented company {i}', weight=.05, currency='EUR', sector='Industrials',
        countryOfRisk='Germany'), weight=.05) for i in range(12)]
    return {'products': [dict(productId=FUND, characteristics=dict(ISIN=FUND, SHARE_MARKETING_NAME='Invented Amundi fund',
        REPLICATION_METHODOLOGY='Direct (Physical)', ASSET_CLASS='Equity', POSITION_AS_OF_DATE=STAMP, WKN='000901'),
        composition=dict(totalNumberOfInstruments=len(rows), compositionData=rows))]}


def vg_profile():
    return dict(portId='9999', fundFullName='Invented Vanguard fund', assetClassificationLevel1='Equity',
        polarisPdtTypeIndicator='ETF', etfReplicationMethodology='Physical',
        identifiers=[dict(altId='ISIN', altIdValue=FUND), dict(altId='WKN Code', altIdValue='000901')],
        listings=[dict(identifiers=[dict(altId='Deutsche Boerse Ticker', altIdValue='INVENTED')])])


def vg_row(i):
    return dict(isin=invented_isin(i), sedol1=str(i), securityLongDescription=f'Invented company {i}',
        marketValuePercentage=20., securityType='EQ.STOCK', effectiveDate=STAMP,
        ticker='RAW', gicsSectorDescription='Industrials', bloombergIsoCountry='DE', finalMaturity=None)


class VanguardProvider:
    def __init__(self):
        self.profile = vg_profile()
        self.rows = [vg_row(1), vg_row(2), vg_row(3)]
        self.calls = []
        self.failure = ''

    def __call__(self, url, *, json_body=None, request_headers=None):
        self.calls.append((url, deepcopy(json_body)))
        if url == vanguard.SITEMAP:
            return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{VG_URL}</loc></url></urlset>'.encode()
        if url != vanguard.API_URL:
            raise OSError('Invented provider unavailable')
        assert request_headers == {'x-consumer-id': 'uk2'}
        data = {'funds': [{'profile': self.profile}]}
        if 'borHoldings' in json_body['query']:
            cursor = json_body['variables']['cursor']
            items = self.rows[:2] if cursor is None else self.rows[2:]
            nxt = 'next' if cursor is None or self.failure == 'repeated' else None
            total = len(self.rows) + (1 if self.failure == 'truncated' else 0)
            data['borHoldings'] = [{'holdings': dict(items=items, lastItemKey=nxt, totalHoldings=total)}]
        return encoded({'data': data})


def xlsx(rows):
    """Minimal real XLSX container, generated without another Excel dependency."""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    sheet = ET.Element('worksheet', xmlns=ns)
    data = ET.SubElement(sheet, 'sheetData')
    for i, values in enumerate(rows, 1):
        row = ET.SubElement(data, 'row', r=str(i))
        for j, value in enumerate(values):
            cell = ET.SubElement(row, 'c', r=f'{chr(65+j)}{i}')
            if isinstance(value, (float, int)):
                ET.SubElement(cell, 'v').text = str(value)
            else:
                cell.set('t', 'inlineStr')
                ET.SubElement(ET.SubElement(cell, 'is'), 't').text = str(value)
    output = BytesIO()
    with ZipFile(output, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Holdings" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', ET.tostring(sheet))
    return output.getvalue()


def ss_rows(bond=False):
    headers = ['ISIN', 'SEDOL', 'Security Name', 'Percent of Fund']
    headers += ['Currency Local', 'Maturity Date', 'Country of Issue'] if bond else ['Currency', 'Trade Country Name', 'Sector Classification']
    rows = [['Fund Name:', 'Invented State Street fund'], ['ISIN:', FUND], ['Ticker Symbol:', 'INVENTED'],
            ['Holdings As Of:', '02-Jan-2026'], [], headers]
    for i in range(12):
        rows.append([invented_isin(i), str(i), f'Invented security {i}', 5., 'EUR',
                     '02-Jan-2030' if bond else 'Germany', 'Germany' if bond else 'Industrials'])
    return rows


class StateStreetProvider:
    def __init__(self):
        self.rows = ss_rows()
        self.calls = []
        self.method = 'Replicated'
        self.asset = 'Equity'
        self.link = '/library-content/products/fund-data/etfs/emea/holdings-daily-emea-en-invented.xlsx'

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        if url == spdr.CATALOG:
            return encoded({'data': {'funds': {'etfs': {'datas': [dict(keywords='Invented ' + FUND, fundUri=SS_URL)]}}}})
        if url == SS_URL:
            return (f'<meta name="ISIN" content="{FUND}"><meta name="assetClass" content="{self.asset}">'
                f'<table><tr><th>Replication Method</th><td>{self.method}</td></tr></table>'
                f'<a href="{self.link}">Daily holdings</a>').encode()
        if 'holdings-daily' in url:
            return xlsx(self.rows)
        raise OSError('Invented provider unavailable')


@pytest.mark.parametrize('provider,url', [(VanguardProvider, VG_URL), (StateStreetProvider, SS_URL)])
def test_provider_discovery_catalogue_cache_install_and_refresh(tmp_path, provider, url):
    fetch = provider()
    resolver = Discovery(fetch)
    isin, source = resolver.resolve({'isin': FUND})
    assert isin == FUND and source.product_url == url
    assert resolver.resolve({'isin': FUND})[0] == FUND
    catalog_url = vanguard.SITEMAP if provider is VanguardProvider else spdr.CATALOG
    calls = [x[0] if isinstance(x, tuple) else x for x in fetch.calls]
    assert calls.count(catalog_url) == 1
    fund = install_snapshot(tmp_path, isin, fetch=fetch, source=source)
    assert fund_breakdown(fund).weight.iloc[-1] == pytest.approx(.4)
    refreshed = refresh_snapshot(fund, fetch=fetch)
    assert refreshed.provider == source.provider
    assert refreshed.constituents.weight.sum() == pytest.approx(.6)
    before = fund.manifest_path.read_bytes()
    if provider is VanguardProvider:
        fetch.rows[0]['effectiveDate'] = '2025-01-01'
    else:
        fetch.rows[1][1] = invented_isin(902)
    with pytest.raises(DataError):
        refresh_snapshot(refreshed, fetch=fetch)
    assert fund.manifest_path.read_bytes() == before


@pytest.mark.parametrize('failure', ['repeated', 'truncated', 'identity', 'date', 'short', 'nan'])
def test_vanguard_rejects_incomplete_or_unsafe_exports(failure):
    fetch = VanguardProvider()
    isin, source = vanguard.source(VG_URL, fetch)
    fetch.failure = failure
    if failure == 'identity':
        fetch.profile['identifiers'][0]['altIdValue'] = invented_isin(99)
    elif failure == 'date':
        fetch.rows[2]['effectiveDate'] = '2026-01-03'
    elif failure in {'short', 'nan'}:
        fetch.rows[0]['marketValuePercentage'] = -1 if failure == 'short' else float('nan')
    with pytest.raises(DataError):
        retrieve_snapshot(isin, source, fetch)


def test_vanguard_same_isin_listings_combine_without_inventing_country_or_ticker():
    rows = [vg_row(1), vg_row(1)]
    rows[1]['bloombergIsoCountry'] = 'US'
    rows[1]['sedol1'] = 'DIFFERENT'
    _, frame, _ = vanguard.parse_holdings(encoded({'isin': FUND, 'items': rows}), expected_isin=FUND)
    assert len(frame) == 1 and frame.weight.item() == pytest.approx(.4)
    assert frame.country.item() == '' and frame.ticker.item() == ''
    assert frame.source_country.item() == ''


def test_vanguard_unsupported_derivatives_are_not_equities():
    rows = [vg_row(1), dict(vg_row(2), securityType='CT.FOREX', marketValuePercentage=-.1)]
    _, frame, notes = vanguard.parse_holdings(encoded({'isin': FUND, 'items': rows}), expected_isin=FUND)
    assert len(frame) == 1 and frame.weight.item() == .2
    assert 'Derivatives' in notes


@pytest.mark.parametrize('bond', [False, True])
def test_spdr_full_weights_and_metadata(bond):
    _, frame, notes = spdr.parse_holdings(xlsx(ss_rows(bond)), expected_isin=FUND, asset_class='fixed_income' if bond else 'equity')
    assert frame.weight.sum() == pytest.approx(.6)
    assert frame.instrument_type.eq('bond' if bond else 'equity').all()
    assert frame.ticker.eq('').all()
    assert frame.market_currency.eq('EUR').all()
    if bond:
        assert frame.maturity.eq('2030-01-02').all()
    else:
        assert frame.country.eq('').all()  # Trading country is not issuer country.
    assert 'not rescaled' in notes


def test_spdr_cash_id_placeholders_remain_distinct():
    rows = ss_rows(True)
    rows += [['-', '-', 'Cash_EUR', .1, 'EUR', '-', ''], ['-', '-', 'Cash_GBP', .2, 'GBP', '-', '']]
    _, frame, _ = spdr.parse_holdings(xlsx(rows), expected_isin=FUND, asset_class='fixed_income')
    assert len(frame[frame.instrument_type.eq('cash')]) == 2
    assert frame.constituent_id.is_unique


def test_spdr_rounding_uses_explicit_partial_weights_and_other():
    rows = ss_rows()
    for row in rows[6:]:
        row[3] = 8.333334
    rows += [['Unassigned', '-', 'Invented derivative', '-', 'EUR', '', ''], [], ['Invented legal footer']]
    _, frame, notes = spdr.parse_holdings(xlsx(rows), expected_isin=FUND, asset_class='equity')
    assert len(frame) == 10 and frame.weight.sum() == pytest.approx(.8333334)
    assert 'Partial holdings' in notes and 'unpublished weights' in notes


@pytest.mark.parametrize('failure', ['identity', 'date', 'schema', 'negative', 'invalid_isin', 'missing_weight'])
def test_spdr_rejects_malformed_export(failure):
    rows = ss_rows()
    if failure == 'identity': rows[1][1] = invented_isin(902)
    elif failure == 'date': rows[3][1] = 'unknown'
    elif failure == 'schema': rows[5][3] = 'Unrelated metric'
    elif failure == 'negative': rows[6][3] = -.1
    elif failure == 'invalid_isin': rows[6][0] = 'not-an-isin'
    elif failure == 'missing_weight': rows[6][3] = '-'
    with pytest.raises(DataError):
        spdr.parse_holdings(xlsx(rows), expected_isin=FUND, asset_class='equity')


def test_amundi_generic_discovery_and_refresh(tmp_path):
    doc = amundi_document()
    def fetch(url, *, json_body=None):
        if url == amundi.API_URL:
            assert json_body['productIds'] == [FUND]
            return encoded(doc)
        if 'getProductPageUrl' in url:
            return AM_URL.removeprefix('https://').encode()
        raise OSError('Invented provider unavailable')
    isin, source = Discovery(fetch).resolve({'isin': FUND})
    assert source.product_url == AM_URL
    fund = install_snapshot(tmp_path, isin, source=source, fetch=fetch)
    assert fund.constituents.weight.sum() == pytest.approx(.6)
    assert refresh_snapshot(fund, fetch=fetch).provider == 'Amundi'


def test_amundi_cash_borrowing_keeps_top_holdings_partial():
    doc = amundi_document()
    item = doc['products'][0]
    rows = item['composition']['compositionData']
    rows.append(dict(compositionCharacteristics=dict(date=STAMP, type='CASH', weight=-.01), weight=-.01))
    item['composition']['totalNumberOfInstruments'] += 1
    _, frame, notes = amundi.parse_holdings(encoded(doc), expected_isin=FUND)
    assert len(frame) == 10 and frame.weight.sum() == pytest.approx(.5)
    assert 'cash borrowing' in notes


@pytest.mark.parametrize('failure', ['count', 'date', 'identity', 'synthetic', 'short', 'nan', 'weight_mismatch'])
def test_amundi_fails_closed_on_changed_export(failure):
    doc = amundi_document()
    item = doc['products'][0]
    row = item['composition']['compositionData'][0]
    if failure == 'count': item['composition']['totalNumberOfInstruments'] += 1
    elif failure == 'date': row['compositionCharacteristics']['date'] = '2026-01-03'
    elif failure == 'identity': item['characteristics']['ISIN'] = invented_isin(902)
    elif failure == 'synthetic': item['characteristics']['REPLICATION_METHODOLOGY'] = 'Indirect (Unfunded swap)'
    elif failure in {'short', 'nan'}:
        row['weight'] = row['compositionCharacteristics']['weight'] = -.1 if failure == 'short' else float('nan')
    elif failure == 'weight_mismatch': row['weight'] = .1
    with pytest.raises(DataError):
        amundi.parse_holdings(encoded(doc), expected_isin=FUND)


@pytest.mark.parametrize('url,provider', [(VG_URL, VanguardProvider), (SS_URL, StateStreetProvider)])
def test_official_product_identity_must_match_position(url, provider):
    with pytest.raises(DataError, match='identity'):
        source_from_url(url, provider(), expected_isin=invented_isin(902))


def test_spdr_rejects_synthetic_and_foreign_download_host():
    fetch = StateStreetProvider()
    fetch.method = 'Swap'
    with pytest.raises(DataError, match='physical'):
        spdr.source(SS_URL, fetch)
    fetch.method = 'Replicated'
    fetch.link = 'https://example.com/holdings-daily-emea-en-invented.xlsx'
    with pytest.raises(DataError, match='Untrusted'):
        spdr.source(SS_URL, fetch)
