"""Provider parsers tested exclusively with invented exports, never live data."""

from datetime import date
import json
import xml.etree.ElementTree as ET

import pandas as pd
import pytest

from portfolio_app import dws, ishares
from portfolio_app.etf import FundSnapshot, fund_classifications, load_funds
from portfolio_app.etf_sources import SOURCES, Source, install_snapshot, refresh_snapshot
from portfolio_app.holdings import DataError
from portfolio_app.stock_exposure import stock_exposure


def dws_export(weights=(60, 39, 1)):
    columns = [('header', 'ISIN'), ('column_0', 'Name'), ('column_1', '% Weight'),
               ('column_3', 'Country'), ('column_4', 'Industry'), ('column_5', 'Asset class')]
    rows = []
    for i, (identifier, name, kind) in enumerate([
        ('ZZ1111111111', 'Invented First', 'Equities'),
        ('ZZ2222222222', 'Invented Second', 'Depository Receipts'),
        ('_CURRENCYZZZ', 'Invented cash', 'Cash'),
    ]):
        row = {k: {'value': v} for k, v in zip(
            ['header', 'column_0', 'column_3', 'column_4', 'column_5'],
            [identifier, name, 'Invented country', 'Invented sector', kind], strict=True)}
        row['column_1'] = {'value': 'deliberately unused', 'sortValue': weights[i]}
        rows.append(row)
    return json.dumps({'tables': [{'id': 'securitiesheldtable-securitiesholding',
        'columns': [{'key': k, 'value': v} for k, v in columns],
        'values': rows, 'disclaimers': [{'text': '<p>Source: DWS 02/01/2026</p>'}]}]}).encode()


def xml_export(rows=None):
    rows = rows or [
        ['ABC', 'Invented share', 'Invented sector', 'Equity', 'ZZZ 990.00', '99.00', '990', '10', 'AAA'],
        ['ZZZ', 'Cash asset', '', 'Cash', 'ZZZ 20.00', '2.00', '20', '20', 'ZZZ'],
        ['AAA', 'Cash liability', '', 'Cash', 'ZZZ -10.00', '-1.00', '-10', '-10', 'AAA'],
        ['FUT', 'Index future', '', 'Futures', 'ZZZ 0.00', '0.00', '500', '1', 'ZZZ'],
    ]
    ns = ishares.NS
    root = ET.Element(f'{{{ns}}}Workbook')
    for title, content in [('Other sheet', [['Wrong date and rows']]), ('Holdings', [
        ['All'], ['as of', '02/Jan/2026'],
        ['Issuer Ticker', 'Name', 'Sector', 'Asset Class', 'Market Value', 'Weight (%)', 'Notional Value', 'Nominal', 'Market Currency'],
        *rows, [], ['Synthetic footer']])]:
        sheet = ET.SubElement(root, f'{{{ns}}}Worksheet', {f'{{{ns}}}Name': title})
        table = ET.SubElement(sheet, f'{{{ns}}}Table')
        for values in content:
            row = ET.SubElement(table, f'{{{ns}}}Row')
            for value in values:
                cell = ET.SubElement(row, f'{{{ns}}}Cell')
                ET.SubElement(cell, f'{{{ns}}}Data').text = value
    return ET.tostring(root)


def test_dws_full_precision_units_identity_cash_and_partial_residual():
    stamp, frame, _ = dws.parse_holdings(dws_export((60.12345678, 20, 1)))
    assert stamp == date(2026, 1, 2)
    assert frame.weight.iloc[0] == pytest.approx(.6012345678)
    assert frame.weight.sum() == pytest.approx(.8112345678)  # Never scale partial data up.
    assert frame['isin'].tolist() == ['ZZ1111111111', 'ZZ2222222222', '']
    assert frame.instrument_type.tolist() == ['equity', 'equity', 'cash']
    assert frame.ticker.eq('').all()
    with pytest.raises(DataError, match='exceed'):
        dws.parse_holdings(dws_export((60, 50, 1)))
    with pytest.raises(DataError):
        dws.parse_holdings(b'{}')


def test_xml_nets_cash_preserves_fractional_holdings_and_ignores_zero_value_futures():
    stamp, frame, notes = ishares.parse_holdings(xml_export())
    assert stamp == date(2026, 1, 2)
    assert frame.weight.tolist() == pytest.approx([.99, .01])
    assert frame.instrument_type.tolist() == ['equity', 'cash']
    assert frame.iloc[0].source_ticker == 'ABC'
    assert frame['isin'].eq('').all() and frame.ticker.eq('').all()
    assert 'notional' in notes
    # Published rounded weights need not add to 100%, including tiny holdings
    # displayed as 0.00%. Denominator includes every row and net cash.
    rows = [
        ['A', 'Invented large', 'Sector', 'Equity', 'ZZZ 999.96', '100.00', '999.96', '1', 'AAA'],
        ['B', 'Invented tiny', 'Sector', 'Equity', 'ZZZ 0.04', '0.00', '0.04', '1', 'AAA'],
    ]
    _, frame, _ = ishares.parse_holdings(xml_export(rows))
    assert frame.weight.tolist() == pytest.approx([.99996, .00004, 0])


@pytest.mark.parametrize(('old', 'new'), [
    (b'99.00', b'60.00'), (b'ZZZ -10.00', b'ZZZ -40.00'),
    (b'Equity', b'Unrecognized derivative'), (b'All', b'Top 10'),
    (b'ZZZ 990.00', b'YYY 990.00'), (b'ZZZ 990.00', b'ZZZ NaN'),
])
def test_xml_rejects_incomplete_unsupported_and_invalid_data(old, new):
    with pytest.raises(DataError):
        ishares.parse_holdings(xml_export().replace(old, new))


def test_local_ticker_never_causes_cross_market_merge():
    rows = [
        ['ABC', 'Invented Alpha', 'Sector', 'Equity', 'ZZZ 500', '50.00', '500', '1', 'AAA'],
        ['ABC', 'Invented Beta', 'Sector', 'Equity', 'ZZZ 500', '50.00', '500', '1', 'BBB'],
    ]
    _, frame, _ = ishares.parse_holdings(xml_export(rows))
    assert frame.constituent_id.is_unique and frame.ticker.eq('').all()


def test_reviewed_nested_fund_is_not_imported_as_a_company(monkeypatch):
    monkeypatch.setattr(ishares, 'NESTED_EQUITY_FUNDS', {
        ('ABC', 'Invented share', 'AAA'): 'ZZ7777777777',
    })
    _, frame, _ = ishares.parse_holdings(xml_export())
    assert frame.weight.tolist() == pytest.approx([.99, .01])
    assert frame.iloc[0].instrument_type == 'etf'
    assert frame.iloc[0].exposure_kind == 'equity'
    assert frame.iloc[0]['isin'] == 'ZZ7777777777'
    # The same local ticker in another currency must not inherit the override.
    _, other, _ = ishares.parse_holdings(xml_export().replace(b'>AAA<', b'>BBB<'))
    assert other.iloc[0].instrument_type == 'equity'
    assert other.iloc[0]['isin'] == ''


@pytest.fixture
def synthetic_source(monkeypatch):
    isin = 'ZZ9999999999'
    source = Source('invented', 'Invented fund', ('INVENTED',), 'Synthetic', 'https://example.invalid/holdings',
                    dws.parse_holdings, 'Invented same-index proxy')
    monkeypatch.setitem(SOURCES, isin, source)
    return isin


def test_install_refresh_proxy_metadata_and_failure_preserve_files(tmp_path, synthetic_source):
    directory = tmp_path / 'etfs'
    fund = install_snapshot(directory, synthetic_source, fetch=lambda _: dws_export())
    assert fund.proxy_source == 'Invented same-index proxy'
    loaded = load_funds(directory)[0]
    assert loaded.notes == fund.notes and loaded.proxy_source == fund.proxy_source
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    for fetch in [lambda _: b'bad', lambda _: dws_export().replace(b'02/01/2026', b'01/01/2026'),
                  lambda _: dws_export().replace(b'02/01/2026', b'02/01/2099')]:
        with pytest.raises(DataError):
            refresh_snapshot(loaded, fetch=fetch)
        assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    refreshed = refresh_snapshot(loaded, fetch=lambda _: dws_export((55, 44, 1)))
    assert refreshed.constituents.weight.iloc[0] == .55
    assert all((directory / name).exists() for name in before)
    assert len(list(directory.glob('*.csv'))) == 2


def test_refresh_does_not_overwrite_concurrent_manifest_edit(tmp_path, synthetic_source):
    fund = install_snapshot(tmp_path, synthetic_source, fetch=lambda _: dws_export())
    def fetch(_):
        fund.manifest_path.write_text(fund.manifest_path.read_text() + '\n# Concurrent edit\n')
        return dws_export()
    with pytest.raises(DataError, match='changed during'):
        refresh_snapshot(fund, fetch=fetch)
    assert 'Concurrent edit' in fund.manifest_path.read_text()


def test_provider_classifications_preserve_local_paths_and_zero_unknown_stock_value():
    stamp, frame, _ = dws.parse_holdings(dws_export())
    zero = frame.iloc[[0]].copy()
    zero['constituent_id'], zero['isin'], zero['weight'], zero['instrument_type'] = 'unknown', '', 0., 'unknown'
    fund = FundSnapshot('invented', 'Invented fund', 'ZZ9999999999', (), stamp, '', pd.concat([frame, zero]), equity_fund=True)
    holdings = pd.DataFrame([{'id': 'held', 'name': 'My name', 'isin': 'ZZ1111111111', 'ticker': 'ABC'}])
    original = {'held': {'sector': (('Local', 'Industry'),)}}
    result = fund_classifications(original, [fund], holdings)
    assert result['held']['sector'] == (('Local', 'Industry'),)
    assert result['held']['geography'] == (('Invented country',),)
    assert 'geography' not in original['held']
    valued = pd.DataFrame([{'id': 'fund', 'position_id': 'p', 'name': 'Invented', 'isin': fund.isin,
                           'current_value_eur': 100., 'instrument_type': 'etf'}])
    exposure = stock_exposure(valued, [fund])
    assert exposure.stock_value == 99.
    assert exposure.companies['Total (EUR)'].sum() == 99.
