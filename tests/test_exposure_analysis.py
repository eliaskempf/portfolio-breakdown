"""Synthetic source-selection contracts independent of Streamlit rendering."""
import pandas as pd
import pytest

from portfolio_app.exposure_analysis import prepare_exposures, select_sources
from portfolio_app.etf import load_funds
from portfolio_app.holdings import load_holdings
from portfolio_app.prices import PriceService, StaticProvider
from portfolio_app.valuation import value_holdings
from test_etf_selection import multi_fund_workspace as multi_fund_workspace


def test_filter_before_expansion_conserves_direct_indirect_and_residual(multi_fund_workspace):
    path = multi_fund_workspace
    holdings = load_holdings(path / 'holdings.csv')
    valued = value_holdings(holdings, PriceService(StaticProvider(path / 'demo_prices.json')))
    original = valued.copy(deep=True)
    selection = select_sources(valued, {}, asset_ids=['company', 'world'])
    assert selection.total == 300
    assert selection.selected_total == 200
    assert selection.positions.portfolio_weight.tolist() == [.5, .5]
    funds = load_funds(path / 'etfs')
    exposures = prepare_exposures(selection.positions, funds, holdings, lookthrough=True)
    assert exposures.value.sum() == pytest.approx(200)
    assert exposures.loc[exposures.source_type.eq('etf_other'), 'value'].sum() == 50
    company = exposures.loc[exposures.asset_id.eq('company')]
    assert set(company.direct_or_indirect) == {'direct', 'indirect'}
    assert company.value.sum() == 150
    assert set(exposures.source_position_id) == set(selection.positions.position_id)
    intact = prepare_exposures(selection.positions, funds, holdings, lookthrough=False)
    assert set(intact.asset_id) == {'company', 'world'}
    pd.testing.assert_frame_equal(valued, original)


def test_missing_and_empty_selection_preserve_scope_totals():
    valued = pd.DataFrame({'id': ['a', 'b', 'c'], 'portfolio': ['Core', 'Core', 'Other'],
                           'current_value_eur': [100., float('nan'), 200.]})
    selected = select_sources(valued, {}, scope='Core')
    assert selected.missing == selected.all_missing == 1
    assert selected.total == 300 and selected.selected_total == 100
    # Existing source weights describe priced value; completeness is explicit.
    assert selected.positions.portfolio_weight.iloc[0] == 1.
    assert pd.isna(selected.positions.portfolio_weight.iloc[1])
    empty = select_sources(valued, {}, asset_ids=[])
    assert empty.positions.empty and empty.selected_total == 0
    assert empty.all_missing == 1 and empty.missing == 0
