"""Synthetic CSV decimals with distinct pandas 3.0 and 3.1 fingerprints."""
from decimal import Decimal
from io import StringIO
import json
import pickle
import warnings

import pandas as pd
import pyarrow as pa
import pytest

from portfolio_app.ledger.account_journal import Journal
from portfolio_app.ledger.history_commands import book_for
from portfolio_app.ledger.security_history import SecurityBook, SecurityEvent, effective_holdings
from portfolio_app.performance.cost_basis import FIELD, active_components, component, encode_components, supplied_conversion
from portfolio_app.performance.performance import ValuationCostCache, position_performance
from portfolio_app.portfolio.balances import patch_holdings
from portfolio_app.portfolio.csv_summary import ATTRIBUTE, source_for
from portfolio_app.portfolio.holdings import metadata_dimensions, parse_holdings
from portfolio_app.portfolio.positions import read_snapshot, save_position
from portfolio_app.portfolio.purchases import read_opening

QUANTITY = '10.00000000000000091'
PRICE = '12.0100000000000009'
OLD = ('10', '12.01', 'USD')
NEW = ('10.000000000000002', '12.010000000000002', 'USD')


def text(quantity=QUANTITY, price=PRICE):
    return ('id,name,position_key,shares,acquisition_price,acquisition_currency\n'
            f'invented,Invented,synthetic,{quantity},{price},USD\n')


def details(summary):
    part = component(summary[0], '125', 'USD', '2024-01-02')
    supplied_conversion(part, 'EUR', rate=Decimal('0.8'))
    return json.dumps(dict(version=1, summary=summary, components=[part]))


def linked(summary):
    opening = SecurityEvent('opening', 'opening', '2024-01-02', quantity=Decimal('10'),
                            amount=Decimal('125'), currency='USD')
    return SecurityBook('synthetic', 'invented', summary, records=(opening,), active_ids=('opening',))


@pytest.mark.parametrize('summary', [OLD, NEW])
def test_source_snapshots_follow_rows_and_new_records_save_the_exact_decimals(summary):
    rows = parse_holdings(text()).set_index('id', drop=False).copy()
    rows[FIELD] = details(summary)
    row = rows.loc[['invented']].iloc[0]
    assert source_for(row) is source_for(rows.iloc[0])
    assert rows.attrs[ATTRIBUTE] is row.attrs[ATTRIBUTE]
    parts = active_components(row)
    assert parts[0]['date'] == '2024-01-02'
    assert parts[0]['conversions']['EUR']['amount'] == '100.0'
    assert book_for(Journal(), row).source_summary == (QUANTITY, PRICE, 'USD')
    assert json.loads(encode_components(parts, row))['summary'] == [QUANTITY, PRICE, 'USD']
    ledger = effective_holdings(rows, (linked(summary),))
    assert ledger.iloc[0].acquisition_price == 12.5
    assert active_components(ledger.iloc[0])[0]['amount'] == '125'


@pytest.mark.parametrize('summary', [(OLD[0], NEW[1], 'USD'), (NEW[0], OLD[1], 'USD')])
def test_mixed_parser_generations_cannot_authorize_csv_costs(summary):
    rows = parse_holdings(text())
    rows[FIELD] = details(summary)
    assert active_components(rows.iloc[0])[0]['date'] == ''


def test_changed_live_row_cannot_replay_source_metadata():
    rows = parse_holdings(text())
    rows[FIELD] = details(OLD)
    rows.loc[0, 'acquisition_price'] = 13
    assert active_components(rows.iloc[0])[0]['conversions'] == {}


def test_source_metadata_is_private_in_arrow_and_plain_text_and_safe_across_copies():
    rows = parse_holdings(text())
    rows.attrs['reporting_currency'] = 'EUR'
    assert ATTRIBUTE not in rows.columns and ATTRIBUTE not in metadata_dimensions(rows)
    assert PRICE not in rows.to_csv(index=False) and PRICE not in rows.to_json()
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        table = pa.Table.from_pandas(rows, preserve_index=False)
        saved_attrs = json.loads(table.schema.metadata[b'pandas'])['attributes']
        assert saved_attrs == {ATTRIBUTE: 'parsed CSV', 'reporting_currency': 'EUR'}
        assert PRICE.encode() not in table.schema.metadata[b'pandas']
    roundtrip = pickle.loads(pickle.dumps(rows))
    assert source_for(roundtrip.iloc[0]) == source_for(rows.iloc[0])
    assert source_for(table.to_pandas().iloc[0]) is None


def test_same_live_floats_cannot_reuse_another_sources_dated_costs():
    original = parse_holdings(text())
    row = original.iloc[0]
    rounded = parse_holdings(text(str(row.shares), str(row.acquisition_price)))
    assert original.attrs[ATTRIBUTE] != rounded.attrs[ATTRIBUTE]
    assert source_for(pd.concat([original, rounded]).iloc[0]) is None
    saved = NEW if row.acquisition_price == 12.01 else OLD
    cache = ValuationCostCache()
    for frame, expected in ((original, 100.), (rounded, None), (original, 100.)):
        frame[FIELD] = details(saved)
        frame['current_value_reporting'] = 200.
        frame['current_price'] = 20.
        frame['quote_currency'] = 'USD'
        amount = position_performance(frame, reporting_currency='EUR', cost_cache=cache).cost_basis_reporting.item()
        assert pd.isna(amount) if expected is None else amount == expected


@pytest.mark.parametrize('writer', ['position', 'balance'])
def test_explicit_decimal_edits_replace_earlier_dated_components(writer, tmp_path):
    path = tmp_path / 'holdings.csv'
    data = pd.read_csv(StringIO(text()), dtype=str)
    data[FIELD] = details(OLD)
    data.to_csv(path, index=False)
    snapshot = read_snapshot(path)
    opening = read_opening(path, snapshot.revision, 'synthetic')
    assert active_components(opening)[0]['date'] == '2024-01-02'
    replacement = {'acquisition_price': '12.010000000000002'}
    if writer == 'position':
        save_position(path, replacement, expected_revision=snapshot.revision, position_id='synthetic')
    else:
        patch_holdings(path, {'synthetic': replacement}, expected_revision=snapshot.revision, replacement=True)
    saved = read_snapshot(path).holdings.iloc[0]
    assert active_components(saved)[0]['date'] == ''
    assert json.loads(saved[FIELD])['summary'][1] == replacement['acquisition_price']
    assert ATTRIBUTE not in path.read_text()


def test_legacy_batches_keep_dates_only_for_a_coherent_source_summary():
    frame = parse_holdings(text(*OLD[:2]))
    purchase = dict(date='2024-01-02', shares='10', price='12.01', fees='0')
    batch = dict(version=1, mode='reconcile', saved_at='2024-01-03', currency='USD',
                 purchases=[purchase], result_shares=OLD[0], result_average=OLD[1])
    frame['purchase_history'] = json.dumps([batch])
    assert active_components(frame.iloc[0])[0]['date'] == '2024-01-02'
    frame['purchase_history'] = json.dumps([dict(batch, result_average=NEW[1])])
    assert active_components(frame.iloc[0])[0]['date'] == ''


def test_marked_exact_decimals_reject_a_colliding_edit_and_detached_floats():
    from dataclasses import replace
    from portfolio_app.ledger.account_journal import journal_from_dict, journal_to_dict

    original = parse_holdings(text('2', '8'))
    edited = parse_holdings(text('2', '8.00000000000000001'))
    assert edited.iloc[0].acquisition_price == original.iloc[0].acquisition_price == 8
    row = original.iloc[0]
    parts = [component('2', '15', 'USD', '2024-01-02')]
    document = encode_components(parts, row)
    assert json.loads(document)['source_exact'] is True
    original[FIELD] = edited[FIELD] = document

    new = book_for(Journal(), original.iloc[0])
    assert new.source_exact
    book = replace(linked(('2', '8', 'USD')), source_summary=new.source_summary, source_exact=True)
    persisted = journal_to_dict(Journal(version=5, securities=(book,)))
    restored, = journal_from_dict(persisted).securities
    assert restored.source_exact and persisted['securities'][0]['source_exact']
    assert active_components(original.iloc[0]) == parts
    assert effective_holdings(original.copy(), (restored,)).iloc[0].shares == 10
    assert active_components(edited.iloc[0])[0]['date'] == ''
    detached = pd.DataFrame(original.to_dict('records'))
    assert active_components(detached.iloc[0])[0]['date'] == ''
    for frame in (edited, detached):
        assert effective_holdings(frame, (restored,)).iloc[0].shares == 10

    legacy = replace(restored, source_exact=False)
    assert 'source_exact' not in journal_to_dict(Journal(version=5, securities=(legacy,)))['securities'][0]
    assert effective_holdings(edited, (legacy,)).iloc[0].shares == 10


@pytest.mark.parametrize('prefix', ['', '+'])
def test_spaced_exponent_stores_exact_decimal_and_rejects_colliding_source_edit(prefix):
    from portfolio_app.performance.cost_basis import matches_snapshot, stored_snapshot

    def row(token):
        return parse_holdings(text(prefix + '1e 2', token)).iloc[0]

    original = row(prefix + '8.00000000000000001e 0')
    edited = row(prefix + '8.00000000000000002e 0')
    summary, exact = stored_snapshot(original)
    assert exact and list(map(Decimal, summary[:2])) == [Decimal('100'), Decimal('8.00000000000000001')]
    assert edited.acquisition_price == original.acquisition_price
    book = book_for(Journal(), original)
    assert book.source_exact
    assert matches_snapshot(original, book.source_summary, source_exact=True)
    assert not matches_snapshot(edited, book.source_summary, source_exact=True)
