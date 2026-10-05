import pandas as pd
import pytest

from portfolio_app.allocation import Allocation, Bucket
from portfolio_app.performance import position_performance
from portfolio_app.strategic import strategic_performance
from portfolio_app.holdings import parse_holdings, metadata_dimensions
from portfolio_app.positions import read_snapshot, save_position
from portfolio_app.display_names import instrument_name


def test_category_performance_uses_total_cost_and_disjoint_source_rows():
    config = Allocation((Bucket('core', 'Core'), Bucket('active', 'Active')))
    rows = pd.DataFrame([
        dict(position_id='a', name='Invented A', shares=1., bucket_id='core', acquisition_price=100., acquisition_currency='EUR', quote_currency='EUR', current_price=150., current_value_reporting=150.),
        dict(position_id='b', name='Invented B', shares=1., bucket_id='core', acquisition_price=300., acquisition_currency='EUR', quote_currency='EUR', current_price=270., current_value_reporting=270.),
        dict(position_id='c', name='Invented C', shares=1., bucket_id='active', acquisition_price=float('nan'), acquisition_currency='EUR', quote_currency='EUR', current_price=50., current_value_reporting=50.),
        dict(position_id='d', name='Invented D', shares=1., bucket_id='active', acquisition_price=0., acquisition_currency='EUR', quote_currency='EUR', current_price=20., current_value_reporting=20.),
    ])
    table = strategic_performance(position_performance(rows), config).set_index('Category')
    assert table.loc['Core', 'Gain'] == 20
    assert table.loc['Core', 'Return (%)'] == 5
    assert table.loc['Active', 'Gain'] == 20
    assert pd.isna(table.loc['Active', 'Return (%)'])
    assert table.loc['Active', 'Status'] == 'Partial'
    assert table.loc['Active', 'Coverage'] == '1 of 2'
    child = strategic_performance(position_performance(rows), config, 'core')
    assert child['Gain'].sum() == table.loc['Core', 'Gain']


def test_short_name_is_instrument_metadata_and_round_trips_across_accounts(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,shares,account\na,Invented Global UCITS ETF Acc,1,First\na,Invented Global UCITS ETF Acc,2,Second\n')
    snapshot = read_snapshot(path)
    save_position(path, {'short_name': 'Invented Global'}, expected_revision=snapshot.revision, position_id='position-0')
    stored = read_snapshot(path).holdings
    assert stored.short_name.tolist() == ['Invented Global', 'Invented Global']
    assert 'short_name' not in metadata_dimensions(stored)
    assert instrument_name(stored.iloc[0]) == 'Invented Global'
    assert stored.name.tolist() == ['Invented Global UCITS ETF Acc'] * 2
    assert instrument_name({'name': 'Invented Global UCITS ETF Acc'}) == 'Invented Global'


def test_existing_instrument_inherits_short_name_for_new_account(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,short_name,shares,account\na,Invented Long Name,Invented,1,First\n')
    snapshot = read_snapshot(path)
    save_position(path, {'id': 'a', 'name': 'Invented Long Name', 'shares': '2', 'account': 'Second'}, expected_revision=snapshot.revision)
    assert read_snapshot(path).holdings.short_name.tolist() == ['Invented', 'Invented']


def test_duplicate_category_labels_and_reserved_names_are_unambiguous():
    from portfolio_app.strategic import category_labels
    config = Allocation((Bucket('a', 'Portfolio'), Bucket('b', 'Portfolio'), Bucket('c', 'Unassigned')))
    labels = category_labels(config)
    assert len(set(labels.values())) == 3
    assert not {'Portfolio', 'Unassigned'} & set(labels.values())
