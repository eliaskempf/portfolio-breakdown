"""All examples are invented; no working portfolio or external services are read."""
from dataclasses import replace
from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import pytest

from portfolio_app.holdings import DataError, metadata_dimensions
from portfolio_app.import_readers import (
    detect_text_format,
    excel_sheets,
    read_table,
    suggest_mapping,
)
from portfolio_app.imports import (
    ImportOptions,
    combine_drafts,
    link_listing,
    normalize_table,
    parse_number,
    save_import,
)
from portfolio_app.instruments import Instrument
from portfolio_app.positions import read_snapshot
from portfolio_app.prices import PriceService, UnavailableProvider
from portfolio_app.valuation import value_holdings


def options(**kwargs):
    return ImportOptions({'name': 'Name', 'shares': 'Units', 'wkn': 'WKN'}, **kwargs)


def draft(rows=None, **kwargs):
    rows = rows or [['Invented fund', '1,25', '000123']]
    return combine_drafts([normalize_table(pd.DataFrame(rows, columns=['Name', 'Units', 'WKN']), options(**kwargs))])


@pytest.mark.parametrize('text,encoding,delimiter', [
    ('Name;Units;WKN\nErfundener Bär;1,25;000123\n', 'utf-8-sig', ';'),
    ('Name;Units;WKN\nErfundener Bär;1,25;000123\n', 'cp1252', ';'),
    ('Name\tUnits\tWKN\nInvented fund\t1,25\t000123\n', 'utf-16', '\t'),
    ('Name,Units,WKN\nInvented fund,1.25,000123\n', 'utf-8-sig', ','),
])
def test_text_readers_preserve_identifiers(text, encoding, delimiter):
    content = text.encode(encoding)
    detected = detect_text_format(content)
    assert detected.delimiter == delimiter
    result = read_table(content, encoding=detected.encoding, delimiter=detected.delimiter)
    assert result.iloc[0].WKN == '000123'


def test_header_and_blank_rows_are_not_silently_discarded():
    table = read_table(b'Report\nName;Units;WKN\nInvented;1;000123\n\n', header_row=2)
    result = normalize_table(table, options(), first_row=3)
    assert result.included == 2
    assert 'row 4' in result.issues[0]
    result = normalize_table(table, options(), included=[True, False])
    assert not result.issues and result.excluded == 1
    with pytest.raises(DataError):
        read_table(b'Name;Units\nInvented;1;unexpected\n')


def synthetic_workbook():
    """A minimal genuine XLSX built without adding a workbook writer dependency."""
    content = BytesIO()
    with ZipFile(content, 'w') as archive:
        archive.writestr('[Content_Types].xml', '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>''')
        archive.writestr('_rels/.rels', '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        archive.writestr('xl/workbook.xml', '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>
<sheet name="Invented holdings" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        archive.writestr('xl/_rels/workbook.xml.rels', '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>''')
        archive.writestr('xl/worksheets/sheet1.xml', '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>Name</t></is></c><c r="B1" t="inlineStr"><is><t>Units</t></is></c><c r="C1" t="inlineStr"><is><t>WKN</t></is></c></row>
<row r="2"><c r="A2" t="inlineStr"><is><t>Invented fund</t></is></c><c r="B2"><v>1234.5</v></c><c r="C2" t="inlineStr"><is><t>000123</t></is></c></row>
</sheetData></worksheet>''')
    return content.getvalue()


def test_real_excel_reader_native_numbers_and_text_identifiers():
    content = synthetic_workbook()
    assert excel_sheets(content) == ['Invented holdings']
    table = read_table(content, excel=True, sheet='Invented holdings')
    assert table.iloc[0].WKN == '000123'
    result = normalize_table(table, options())
    assert not result.issues
    assert result.positions.iloc[0].shares == 1234.5
    with pytest.raises(DataError):
        excel_sheets(b'not a workbook')


def test_ambiguous_quantity_sources_require_explicit_mapping():
    mapping = suggest_mapping(['Wertpapier', 'ISIN', 'Stück/Nennwert Bank', 'Stück/Nennwert FinanzManager'])
    assert mapping['name'] == 'Wertpapier'
    assert mapping['shares'] == ''


@pytest.mark.parametrize('value,decimal,expected', [('1.234,56789', ',', 1234.56789),
                                                   ('1,234.56789', '.', 1234.56789),
                                                   ('0', ',', 0), ('1\u202f234,5', ',', 1234.5)])
def test_numbers(value, decimal, expected):
    assert parse_number(value, decimal) == expected


@pytest.mark.parametrize('value', ['1,234.56', '1.2', 'NaN', 'inf', '-1', '1 EUR', '12.34,5', float('inf')])
def test_invalid_numbers_never_turn_into_zero(value):
    with pytest.raises(DataError):
        parse_number(value)


def test_optional_fields_and_zero_quantity_save_without_network(tmp_path):
    result = draft([['Invented asset', '0', ''], ['Another invented asset', '1,23456789', '']])
    path = tmp_path / 'holdings.csv'
    save_import(path, result, expected_revision=None)
    stored = read_snapshot(path).holdings
    assert stored.shares.tolist() == [0, 1.23456789]
    assert stored.acquisition_price.isna().all()
    assert stored.ticker.eq('').all()
    assert stored.position_key.nunique() == 2
    assert 'import_source' not in metadata_dimensions(stored)
    assert 'wkn' not in metadata_dimensions(stored)
    valued = value_holdings(stored, PriceService(UnavailableProvider()))
    assert valued.current_value_reporting.iloc[0] == 0
    assert pd.isna(valued.current_value_reporting.iloc[1])


def snapshot_draft(currency='EUR', price_date='2026-01-02', unit='units'):
    table = pd.DataFrame([['Invented asset', '2,5', '10,00', '20,00']], columns=['Name', 'Units', 'Price', 'Cost'])
    settings = ImportOptions({'name': 'Name', 'shares': 'Units', 'price': 'Price', 'total_cost': 'Cost'},
                             defaults={'price_currency': currency, 'price_date': price_date, 'acquisition_currency': 'USD'},
                             quantity_unit=unit, use_prices=True)
    return combine_drafts([normalize_table(table, settings)])


def test_snapshot_valuation_and_cost_currency_are_independent(tmp_path):
    path = tmp_path / 'holdings.csv'
    result = snapshot_draft()
    assert not result.issues
    save_import(path, result, expected_revision=None)
    stored = read_snapshot(path).holdings
    assert stored.acquisition_price.iloc[0] == 8
    assert stored.acquisition_currency.iloc[0] == 'USD'
    valued = value_holdings(stored, PriceService(UnavailableProvider()))
    assert valued.current_value_reporting.iloc[0] == 25
    assert valued.price_status.iloc[0] == 'manual'
    assert valued.price_observed_at.iloc[0].startswith('2026-01-02')


def test_foreign_snapshot_without_fx_remains_unknown(tmp_path):
    path = tmp_path / 'holdings.csv'
    save_import(path, snapshot_draft(currency='USD'), expected_revision=None)
    valued = value_holdings(read_snapshot(path).holdings, PriceService(UnavailableProvider()))
    assert pd.isna(valued.current_value_reporting.iloc[0])
    assert 'FX' in valued.valuation_note.iloc[0]


@pytest.mark.parametrize('kwargs', [{'currency': ''}, {'price_date': ''}, {'price_date': '2999-01-01'}, {'unit': 'nominal'}])
def test_invalid_snapshot_metadata_blocks_included_rows(kwargs):
    assert snapshot_draft(**kwargs).issues


def test_row_errors_are_explicit_and_prevent_partial_write(tmp_path):
    path = tmp_path / 'holdings.csv'
    result = draft([['Invented fund', '3', '000123'], ['Total', '3', '']])
    assert len(result.positions) == 1 and result.issues
    with pytest.raises(DataError):
        save_import(path, result, expected_revision=None)
    assert not path.exists()


def test_duplicates_across_files_and_accounts():
    table = pd.DataFrame([['Invented fund', '2', '000123']], columns=['Name', 'Units', 'WKN'])
    a = normalize_table(table, options(defaults={'account': 'Invented A'}), source='File 1')
    b = normalize_table(table, options(defaults={'account': 'Invented B'}), source='File 2')
    combined = combine_drafts([a, b])
    assert not combined.issues
    assert combined.positions.id.nunique() == 1
    assert combined.positions.position_key.nunique() == 2
    duplicate = combine_drafts([a, a])
    assert 'duplicate' in duplicate.issues[0]


def test_repeated_submit_and_nonempty_portfolio_do_not_append(tmp_path):
    path = tmp_path / 'holdings.csv'
    result = draft()
    save_import(path, result, expected_revision=None)
    original = path.read_bytes()
    with pytest.raises(DataError, match='changed'):
        save_import(path, result, expected_revision=None)
    with pytest.raises(DataError, match='empty portfolio'):
        save_import(path, result, expected_revision=read_snapshot(path).revision)
    assert path.read_bytes() == original


def test_external_edit_during_validation_is_detected(tmp_path):
    path = tmp_path / 'holdings.csv'
    external = 'id,name,shares\nexternal,Invented external position,7\n'
    def concurrent_change(_):
        path.write_text(external)
    with pytest.raises(DataError, match='changed'):
        save_import(path, draft(), expected_revision=None, validate=concurrent_change)
    assert path.read_text() == external


def test_existing_empty_csv_backed_up_and_callback_validates_before_save(tmp_path):
    path = tmp_path / 'holdings.csv'
    original = 'id,name,shares\n'
    path.write_text(original)
    seen = []
    save_import(path, draft(), expected_revision=read_snapshot(path).revision, validate=lambda frame: seen.append(len(frame)))
    assert seen == [1]
    assert next((tmp_path / '.backups').iterdir()).read_text() == original


def test_link_listing_preserves_data_and_requires_explicit_switch(tmp_path):
    path = tmp_path / 'holdings.csv'
    save_import(path, snapshot_draft(), expected_revision=None)
    current = read_snapshot(path)
    asset_id = current.holdings.id.iloc[0]
    listing = Instrument('SYNTHETIC', 'Invented listing')
    link_listing(path, asset_id, listing, expected_revision=current.revision)
    linked = read_snapshot(path)
    assert linked.holdings.manual_price.iloc[0] == 10
    assert linked.holdings.ticker.iloc[0] == 'SYNTHETIC'
    link_listing(path, asset_id, listing, expected_revision=linked.revision, use_live_prices=True)
    live = read_snapshot(path).holdings
    assert live.manual_price.isna().all()
    assert live.position_key.tolist() == current.holdings.position_key.tolist()
    assert live.shares.tolist() == current.holdings.shares.tolist()
    assert live.acquisition_price.tolist() == current.holdings.acquisition_price.tolist()
    assert live.bucket_id.tolist() == current.holdings.bucket_id.tolist()
    with pytest.raises(DataError, match='changed'):
        link_listing(path, asset_id, listing, expected_revision=current.revision)


def test_link_changes_all_accounts_and_rejects_isin_mismatch(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('position_key,id,name,shares,isin,ticker,account,bucket_id,within_bucket_target\n'
                    'one,a,Invented position,1,US0378331005,,Invented A,core,0.25\n'
                    'two,a,Invented position,2,US0378331005,,Invented B,core,0.75\n')
    original = path.read_bytes()
    snapshot = read_snapshot(path)
    with pytest.raises(DataError, match='same verified ISIN'):
        link_listing(path, 'a', Instrument('NVDA', 'NVIDIA', isin='US67066G1040'), expected_revision=snapshot.revision)
    assert path.read_bytes() == original
    link_listing(path, 'a', Instrument('AAPL', 'Apple', isin='US0378331005'), expected_revision=snapshot.revision)
    stored = read_snapshot(path).holdings
    assert stored.ticker.tolist() == ['AAPL', 'AAPL']
    assert stored.within_bucket_target.tolist() == [.25, .75]
    assert stored.account.tolist() == ['Invented A', 'Invented B']


def test_identifier_validation_and_conflicting_cost_mappings():
    table = pd.DataFrame([['Invented', '2', 'BAD', '2']], columns=['Name', 'Units', 'ISIN', 'Cost'])
    settings = ImportOptions({'name': 'Name', 'shares': 'Units', 'isin': 'ISIN'})
    assert 'ISIN' in normalize_table(table, settings).issues[0]
    settings = replace(settings, mapping={'name': 'Name', 'shares': 'Units', 'acquisition_price': 'Cost', 'total_cost': 'Cost'})
    assert 'either' in normalize_table(table, settings).issues[0]
