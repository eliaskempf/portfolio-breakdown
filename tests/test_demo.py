"""The public demo is deterministic, offline, valued and ready to rebalance."""
import pytest

from portfolio_app.allocation import analysis_targets, load_allocation, macro_table
from portfolio_app.demo import create_demo_data
from portfolio_app.etf import load_funds
from portfolio_app.exposure_analysis import prepare_exposures
from portfolio_app.holdings import load_holdings
from portfolio_app.performance import position_performance
from portfolio_app.prices import PriceService, StaticProvider
from portfolio_app.rebalancing import minimum_trades, prepare_rebalance
from portfolio_app.scoped_rebalancing import portfolio_contribution
from portfolio_app.valuation import value_holdings


def test_demo_targets_gaps_performance_and_plans(tmp_path):
    create_demo_data(tmp_path)
    holdings = load_holdings(tmp_path / 'holdings.csv')
    config = load_allocation(tmp_path / 'allocation.yaml', holdings)
    valued = value_holdings(analysis_targets(holdings, config), PriceService(StaticProvider(tmp_path / 'demo_prices.json')))
    assert valued.current_value_eur.sum() == pytest.approx(100000.)
    assert valued.target_allocation.tolist() == pytest.approx([.42, .18, .25, .10, .03, .02])
    summary = macro_table(valued, config).set_index('Bucket')
    assert summary['Current portfolio %'].to_dict() == pytest.approx({'Equities': 61, 'Money market': 24, 'Gold': 11, 'Crypto': 4})
    assert summary['Gap (pp)'].to_dict() == pytest.approx({'Equities': 1, 'Money market': -1, 'Gold': 1, 'Crypto': -1})
    gains = position_performance(valued).set_index('id').unrealized_gain_eur
    assert gains.to_dict() == pytest.approx({'world': 5670, 'emerging': -1472, 'money-market': 352,
                                           'gold': 1947, 'bitcoin': 300, 'ethereum': -240})
    # A zero-tolerance sell/buy plan reaches all targets while conserving capital.
    plan = minimum_trades(prepare_rebalance(valued, tolerance=0))
    assert plan.within_bands and plan.deviation_before > 0
    assert plan.deviation_after == pytest.approx(0, abs=1e-6)
    assert plan.buy_count > 0 and plan.sell_count > 0 and plan.new_money == 0
    contribution = portfolio_contribution(valued, config, 5000., eligible_ids=valued.position_id.tolist())
    assert contribution.after.current_value_eur.sum() + contribution.unallocated_cash == pytest.approx(105000.)


def test_demo_breakdown_conserves_each_source_and_labels_synthetic_weights(tmp_path):
    create_demo_data(tmp_path)
    holdings = load_holdings(tmp_path / 'holdings.csv')
    valued = value_holdings(holdings, PriceService(StaticProvider(tmp_path / 'demo_prices.json')))
    funds = load_funds(tmp_path / 'etfs')
    assert {fund.isin for fund in funds} == {'IE00BJ0KDQ92', 'IE00BKM4GZ66'}
    assert all('Synthetic demo' in fund.source and 'invented' in fund.notes for fund in funds)
    intact = prepare_exposures(valued, funds, holdings, lookthrough=False)
    expanded = prepare_exposures(valued, funds, holdings, lookthrough=True)
    assert len(expanded) > len(intact)
    assert expanded.groupby('source_position_id').value.sum().to_dict() == pytest.approx(
        valued.set_index('position_id').current_value_eur.to_dict())
    assert expanded.loc[expanded.source_type.eq('etf_other'), 'value'].sum() == pytest.approx(45000 * .81 + 16000 * .79)
    assert set(expanded.loc[expanded.direct_or_indirect.eq('direct'), 'asset_id']) == {'money-market', 'gold', 'bitcoin', 'ethereum'}


def test_live_demo_sizes_once_from_quotes_and_preserves_targets_and_edits(tmp_path):
    from portfolio_app.demo import initialize_live_demo, live_demo_pending, LIVE_EXAMPLES
    from portfolio_app.positions import read_snapshot, save_position
    live = create_demo_data(tmp_path / 'live', live=True)
    offline = create_demo_data(tmp_path / 'offline')
    prices = PriceService(StaticProvider(offline / 'demo_prices.json'))
    assert not (live / 'demo_prices.json').exists()
    assert not load_funds(live / 'etfs')  # Never seed invented weights into live mode.
    holdings = load_holdings(live / 'holdings.csv')
    valued = value_holdings(holdings, prices)
    original = (live / 'holdings.csv').read_bytes()
    missing = valued.copy()
    missing.loc[0, 'fx_to_eur'] = float('nan')
    assert not initialize_live_demo(live, missing)
    assert (live / 'holdings.csv').read_bytes() == original
    assert live_demo_pending(live)
    assert initialize_live_demo(live, valued)
    assert not live_demo_pending(live)
    holdings = load_holdings(live / 'holdings.csv')
    config = load_allocation(live / 'allocation.yaml', holdings)
    assert analysis_targets(holdings, config).target_allocation.tolist() == pytest.approx([.42, .18, .25, .10, .03, .02])
    valued = value_holdings(holdings, prices)
    assert valued.set_index('id').current_value_eur.to_dict() == pytest.approx(
        {k: v[0] for k, v in LIVE_EXAMPLES.items()}, abs=.04)
    gains = position_performance(valued).unrealized_gain_eur
    assert (gains > 0).sum() == 4 and (gains < 0).sum() == 2
    snapshot = read_snapshot(live / 'holdings.csv')
    save_position(live / 'holdings.csv', {'shares': '123'}, expected_revision=snapshot.revision,
                  position_id=snapshot.holdings.position_id.iloc[0])
    edited = (live / 'holdings.csv').read_bytes()
    valued['current_price'] *= 2
    assert not initialize_live_demo(live, valued)
    create_demo_data(live, live=True)
    assert (live / 'holdings.csv').read_bytes() == edited
    assert not live_demo_pending(offline)
    assert not initialize_live_demo(offline, valued)
