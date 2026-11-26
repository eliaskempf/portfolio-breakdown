"""Synthetic presentation contracts: scope, identity and incomplete measures."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from portfolio_app.exposure.company_analysis import prepare_company_analysis
from portfolio_app.exposure.company_merges import MergeSettings
from portfolio_app.exposure.exposure_presentation import prepare_market_analysis
from portfolio_app.studies.futures import FuturePosition
from test_company_merges import universe
from test_portfolio_exposure import CONFIG, calculate, invented


def test_scoped_exposure_composition_keeps_whole_portfolio_equity_denominator():
    valued = invented()
    result = calculate(valued, future=FuturePosition(bucket_id='core'))
    before = deepcopy(result.exposures)
    prepared = prepare_market_analysis(result, CONFIG, {}, valued, scope='core')
    root = prepared.table_nodes.loc[prepared.table_nodes.parent_id.eq('')].iloc[0]
    assert root.value == 110000
    assert root.percentage == 1
    assert prepared.equity_percentages[root.node_id] == 110
    assert set(prepared.rows.bucket_id) == {'core'}
    pd.testing.assert_frame_equal(result.exposures, before)


def test_unknown_amounts_remain_unknown_in_tables_and_known_only_in_charts():
    valued = invented()
    valued.loc[0, 'exposure_kind'] = 'unknown'
    result = calculate(valued)
    prepared = prepare_market_analysis(result, CONFIG, {}, valued)
    chart_root = prepared.chart_nodes.loc[prepared.chart_nodes.parent_id.eq('')].iloc[0]
    table_root = prepared.table_nodes.loc[prepared.table_nodes.parent_id.eq('')].iloc[0]
    assert prepared.missing
    assert chart_root.value == 30000
    assert pd.isna(table_root.value)
    assert pd.isna(prepared.equity_percentages[table_root.node_id])
    assert result.net_equity == 100000


@pytest.mark.parametrize('funded', [False, True])
def test_future_source_identity_and_signed_exposure_remain_separate_from_capital(funded):
    valued = invented()
    future = FuturePosition(contracts=-1, reference_capital_eur=20000. if funded else None)
    result = calculate(valued, future=future)
    prepared = prepare_market_analysis(result, CONFIG, {}, valued, view='ETF sources', future=future)
    row = prepared.table_nodes.loc[prepared.table_nodes.label.eq('FMWN future')].iloc[0]
    assert prepared.signed and row.value == -50000
    assert result.net_equity == (120000 if funded else 100000)
    empty = prepare_market_analysis(result, CONFIG, {}, valued, scope='missing')
    assert empty.rows.empty and empty.chart_nodes.empty and empty.table_nodes.empty


def test_company_preparation_merges_reviewed_identities_without_mutating_sources():
    valued, funds = universe(third=True)
    funds[0] = replace(funds[0], proxy_source='Invented proxy source')
    before, original_funds = valued.copy(deep=True), deepcopy(funds)
    snapshots = {row.id: SimpleNamespace(kind=row.instrument_type) for row in valued.itertuples()}
    prepared = prepare_company_analysis(valued, snapshots, funds, {}, MergeSettings(set()), {})
    assert prepared.exposure.companies.Total.tolist() == [200.]
    assert prepared.exposure.stock_value == 250.
    assert prepared.proxy_notes == ('Invented Blue Fund: Invented proxy source',)
    pd.testing.assert_frame_equal(valued, before)
    for fund, original in zip(funds, original_funds):
        pd.testing.assert_frame_equal(fund.constituents, original.constituents)


def test_chart_tickers_use_direct_precedence_across_sources_and_skip_unavailable_symbols(monkeypatch):
    from portfolio_app.exposure.exposure_presentation import with_asset_tickers

    rows = pd.DataFrame({'asset_id': ['a', 'b', 'a', 'b', 'c', 'd'],
                         'asset_name': ['One', 'Two', 'One alias', 'Two', 'Blank', 'Unknown'],
                         'ticker': ['FUND-A', '', 'SECOND', 'LAST', 'OLD', None],
                         'source_note': ['Invented'] * 6}, index=[1, 0, 1, 0, 0, 3])
    holdings = pd.DataFrame({'id': ['a', 'c', 'a'], 'ticker': ['EARLIER', '', 'DIRECT']})
    def no_full_rows(*args, **kwargs):
        pytest.fail('Ticker display must not extract full contribution records')
    monkeypatch.setattr(pd.DataFrame, 'itertuples', no_full_rows)
    displayed = with_asset_tickers(rows, holdings)
    assert displayed.asset_name.tolist() == ['One (DIRECT)', 'Two (LAST)', 'One alias (DIRECT)',
                                             'Two (LAST)', 'Blank', 'Unknown']
    assert rows.asset_name.tolist() == ['One', 'Two', 'One alias', 'Two', 'Blank', 'Unknown']
    pd.testing.assert_frame_equal(displayed.drop(columns='asset_name'), rows.drop(columns='asset_name'))
    for empty in (rows.iloc[:0], rows.iloc[[-1]]):
        pd.testing.assert_frame_equal(with_asset_tickers(empty, holdings), empty)
