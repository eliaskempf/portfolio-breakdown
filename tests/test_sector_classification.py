"""Invented values and fixtures; no live requests or personal data."""
from copy import deepcopy
from dataclasses import replace

import pandas as pd
import pytest

from portfolio_app.aggregation import classify_exposures
from portfolio_app.demo import create_demo_data, LIVE_EXAMPLES
from portfolio_app.etf import load_funds, fund_classifications
from portfolio_app.exposure_analysis import prepare_exposures
from portfolio_app.holdings import load_holdings
from portfolio_app.prices import PriceService, StaticProvider
from portfolio_app.sector_classification import NO_SECTOR, sector_classifications
from portfolio_app.taxonomy import load_classifications, paths_for, UNCLASSIFIED
from portfolio_app.valuation import value_holdings


@pytest.mark.parametrize('expanded', [False, True])
def test_demo_separates_sector_inapplicability_from_unknown_without_dropping_value(tmp_path, expanded):
    create_demo_data(tmp_path)
    holdings = load_holdings(tmp_path / 'holdings.csv')
    funds = load_funds(tmp_path / 'etfs')
    saved = load_classifications(tmp_path / 'classifications.yaml')
    classes = fund_classifications(saved, funds, holdings)
    before = deepcopy(classes)
    resolved = sector_classifications(classes, holdings, funds)
    assert classes == before
    valued = value_holdings(holdings, PriceService(StaticProvider(tmp_path / 'demo_prices.json')))
    exposures = prepare_exposures(valued, funds, holdings, lookthrough=expanded)
    allocations = classify_exposures(exposures, resolved, 'sector')
    totals = allocations.groupby(allocations.path.map(lambda path: path[0])).value.sum()
    assert totals.sum() == pytest.approx(93184.35)
    assert totals[NO_SECTOR] == pytest.approx(sum(LIVE_EXAMPLES[key][0] for key in ('money-market', 'gold', 'bitcoin', 'ethereum')))
    assert totals['Unclassified'] == pytest.approx(44963.27 * .015 + 15954.84 * .01 if expanded else 60918.11)
    assert set(allocations.loc[allocations.path.map(lambda path: path[0] == NO_SECTOR), 'path']) == {
        (NO_SECTOR, 'Money market'), (NO_SECTOR, 'Gold'), (NO_SECTOR, 'Crypto')}
    overnight = exposures.loc[exposures.source_instrument.eq('money-market')]
    assert len(overnight) == 1  # One benchmark exposure, not collateral companies.
    if expanded:
        assert overnight.instrument_type.item() == 'overnight_rate'
        assert totals['Unclassified'] / totals.sum() < .01


def test_sector_fallback_preserves_overrides_and_unknown_assets():
    holdings = pd.DataFrame([
        dict(id='cash', instrument_type='cash'), dict(id='crypto', instrument_type='crypto'),
        dict(id='bond', instrument_type='bond'), dict(id='fund', instrument_type='etf'),
        dict(id='gold', instrument_type='physical', price_source='gold_spot'),
        dict(id='physical', instrument_type='physical'), dict(id='etc', instrument_type='etc'),
        dict(id='company', instrument_type='equity'),
    ])
    original = {'cash': {'sector': (('Treasury',),)}, 'crypto': {'labels': (('Invented theme',),)},
                'bond': {'sector': (('Industrials',),)}}
    result = sector_classifications(original, holdings, [])
    assert result['cash'] == original['cash']
    assert result['bond'] == original['bond']
    assert result['crypto']['labels'] == original['crypto']['labels']
    assert result['crypto']['sector'] == ((NO_SECTOR, 'Crypto'),)
    assert result['gold']['sector'] == ((NO_SECTOR, 'Gold'),)
    assert 'labels' not in result['gold']
    for asset in ('fund', 'physical', 'etc', 'company'):
        assert paths_for(result, asset, 'sector') == (UNCLASSIFIED,)


def test_provider_sector_wins_and_substitute_basket_is_not_a_sector_source(tmp_path):
    create_demo_data(tmp_path)
    holdings = load_holdings(tmp_path / 'holdings.csv')
    overnight = next(f for f in load_funds(tmp_path / 'etfs') if f.asset_class == 'money_market')
    overnight = replace(overnight, basket=pd.DataFrame([dict(constituent_id='collateral', instrument_type='equity', sector='Technology')]))
    result = sector_classifications({}, holdings, [overnight])
    assert result['money-market']['sector'] == ((NO_SECTOR, 'Money market'),)
    assert 'collateral' not in result
    asset = overnight.constituents.constituent_id.item()
    provider = {asset: {'sector': (('Explicit provider category',),)}}
    assert sector_classifications(provider, holdings, [overnight])[asset] == provider[asset]


def test_conflicting_special_classifications_remain_unknown():
    holdings = pd.DataFrame([dict(id='asset', instrument_type='crypto')])
    saved = {'asset': {'asset_class': (('Commodities', 'Gold'),)}}
    assert paths_for(sector_classifications(saved, holdings, []), 'asset', 'sector') == (UNCLASSIFIED,)
