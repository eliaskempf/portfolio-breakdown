"""The public demo is deterministic, offline, valued and ready to rebalance."""
import pytest

from portfolio_app.allocation import analysis_targets, load_allocation, macro_table
from portfolio_app.demo import create_demo_data, LIVE_EXAMPLES
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
    assert valued.current_value_reporting.sum() == pytest.approx(93184.35)
    assert valued.target_allocation.tolist() == pytest.approx([.42, .18, .25, .10, .03, .02])
    summary = macro_table(valued, config).set_index('Bucket')
    assert summary['Current portfolio %'].to_dict() == pytest.approx({'Equities': 65.374, 'Money market': 21.937, 'Gold': 8.540, 'Crypto': 4.149}, abs=.001)
    assert summary['Gap (pp)'].to_dict() == pytest.approx({'Equities': 5.374, 'Money market': -3.063, 'Gold': -1.460, 'Crypto': -.851}, abs=.001)
    gains = position_performance(valued).set_index('id').unrealized_gain_reporting
    assert set(gains[gains > 0].index) == {'world', 'money-market', 'gold', 'bitcoin'}
    assert set(gains[gains < 0].index) == {'emerging', 'ethereum'}
    assert valued.current_value_reporting.notna().all()
    values = valued.set_index('id').current_value_reporting
    assert values.to_dict() == pytest.approx({asset: entry[0] for asset, entry in LIVE_EXAMPLES.items()})
    assert values['world'] / (values['world'] + values['emerging']) == pytest.approx(.73809, abs=.00001)
    assert values['bitcoin'] / (values['bitcoin'] + values['ethereum']) == pytest.approx(.58576, abs=.00001)
    # A zero-tolerance sell/buy plan reaches all targets while conserving capital.
    plan = minimum_trades(prepare_rebalance(valued, tolerance=0))
    assert plan.within_bands and plan.deviation_before > 0
    assert plan.deviation_after == pytest.approx(0, abs=1e-6)
    assert plan.buy_count > 0 and plan.sell_count > 0 and plan.new_money == 0
    contribution = portfolio_contribution(valued, config, 5000., eligible_ids=valued.position_id.tolist())
    assert contribution.after.current_value_reporting.sum() + contribution.unallocated_cash == pytest.approx(98184.35)


def test_demo_breakdown_conserves_each_source_and_labels_synthetic_weights(tmp_path):
    create_demo_data(tmp_path)
    holdings = load_holdings(tmp_path / 'holdings.csv')
    valued = value_holdings(holdings, PriceService(StaticProvider(tmp_path / 'demo_prices.json')))
    funds = load_funds(tmp_path / 'etfs')
    assert {fund.isin for fund in funds} == {'IE00BJ0KDQ92', 'IE00BKM4GZ66', 'LU0290358497'}
    assert all('Synthetic demo' in fund.source and 'invented' in fund.notes for fund in funds)
    intact = prepare_exposures(valued, funds, holdings, lookthrough=False)
    expanded = prepare_exposures(valued, funds, holdings, lookthrough=True)
    assert len(expanded) > len(intact)
    assert expanded.groupby('source_position_id').value.sum().to_dict() == pytest.approx(
        valued.set_index('position_id').current_value_reporting.to_dict())
    assert expanded.loc[expanded.source_type.eq('etf_other'), 'value'].sum() == pytest.approx(44963.27 * .015 + 15954.84 * .01)
    assert set(expanded.loc[expanded.direct_or_indirect.eq('direct'), 'asset_id']) == {'gold', 'bitcoin', 'ethereum'}


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
    missing.loc[0, 'fx_to_reporting'] = float('nan')
    assert not initialize_live_demo(live, missing)
    assert (live / 'holdings.csv').read_bytes() == original
    assert live_demo_pending(live)
    assert initialize_live_demo(live, valued)
    assert not live_demo_pending(live)
    holdings = load_holdings(live / 'holdings.csv')
    config = load_allocation(live / 'allocation.yaml', holdings)
    assert analysis_targets(holdings, config).target_allocation.tolist() == pytest.approx([.42, .18, .25, .10, .03, .02])
    valued = value_holdings(holdings, prices)
    assert valued.set_index('id').current_value_reporting.to_dict() == pytest.approx(
        {k: v[0] for k, v in LIVE_EXAMPLES.items()}, abs=.04)
    assert 93000 < valued.current_value_reporting.sum() < 93500
    gains = position_performance(valued).unrealized_gain_reporting
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


def test_demo_geography_and_sector_coverage(tmp_path):
    from portfolio_app.etf import fund_classifications
    from portfolio_app.geography import UNKNOWN, geography_allocations, geography_table, resolve_geography
    from portfolio_app.taxonomy import load_classifications, paths_for, UNCLASSIFIED
    create_demo_data(tmp_path)
    holdings = load_holdings(tmp_path / 'holdings.csv')
    funds = load_funds(tmp_path / 'etfs')
    manual = load_classifications(tmp_path / 'classifications.yaml')
    valued = value_holdings(holdings, PriceService(StaticProvider(tmp_path / 'demo_prices.json')))
    expanded = prepare_exposures(valued, funds, holdings, lookthrough=True)
    classifications = fund_classifications(manual, funds, holdings)
    geography = resolve_geography(holdings, funds, manual)
    named = expanded.loc[expanded.source_type.eq('etf_constituent') & expanded.source_instrument.isin(['world', 'emerging'])]
    assert len(named) == 19
    assert all(paths_for(classifications, asset, 'sector') != (UNCLASSIFIED,) for asset in named.asset_id)
    assert all(geography.paths[asset] != ((UNKNOWN,),) for asset in named.asset_id)
    assert len({paths_for(classifications, asset, 'sector') for asset in named.asset_id}) >= 7
    allocated = geography_allocations(expanded, geography)
    for level in ['Regions', 'Countries']:
        table = geography_table(allocated, level=level, denominator=valued.current_value_reporting.sum(), complete=True).set_index('Category')
        assert table['% of selected portfolio'].sum() == pytest.approx(100)
        assert table.loc[UNKNOWN, '% of selected portfolio'] < 1
        assert table.loc['Money market', 'Value'] == pytest.approx(20441.67)
        assert table.loc['Gold', 'Value'] == pytest.approx(7958.32)
        assert table.loc['Crypto', 'Value'] == pytest.approx(3866.25)
    assert {'United States', 'Europe', 'Asia', 'Africa', 'Oceania', 'Latin America & Caribbean'} <= {
        path[0] for paths in geography.paths.values() for path in paths}
    # Fund domicile and the remaining unidentified fund slice are never countries.
    assert geography.paths['world'] == ((UNKNOWN,),)
    assert geography.paths['emerging'] == ((UNKNOWN,),)
    assert geography.paths['money-market'] == (('Money market',),)


def test_live_demo_provider_installation_and_retry(tmp_path):
    from portfolio_app.etf import fund_classifications
    from portfolio_app.etf_refresh import RefreshCoordinator, read_json
    from portfolio_app.etf_sources import Source, install_snapshot
    from portfolio_app.geography import resolve_geography, UNKNOWN
    offline = create_demo_data(tmp_path / 'offline')
    live = create_demo_data(tmp_path / 'live', live=True)
    fixtures = {fund.isin: fund for fund in load_funds(offline / 'etfs')}
    holdings = load_holdings(live / 'holdings.csv')
    fail = [True]
    def discover(row):
        if fail[0]:
            raise ValueError('Synthetic provider outage')
        fund = fixtures[row['isin']]
        source = Source(fund.fund_id, fund.name, fund.tickers, 'Synthetic injected provider',
                        'https://example.invalid/holdings',
                        lambda content: (fund.as_of, fund.constituents, 'Synthetic injected response'),
                        asset_class=fund.asset_class, replication=fund.replication,
                        breakdown_basis=fund.breakdown_basis)
        return fund.isin, source
    def install(directory, isin, **kwargs):
        return install_snapshot(directory, isin, fetch=lambda url: b'invented', **kwargs)
    service = RefreshCoordinator(discover=discover, install=install)
    assert service.schedule(live, holdings, [], force=True)
    service._workers[str(live.resolve())].join(timeout=5)
    assert not service.running(live)
    assert not load_funds(live / 'etfs')
    records = read_json(live / '.cache/etf-refresh/status.json')
    assert len(records) == 3 and all(record['status'] == 'unavailable' for record in records.values())
    fail[0] = False
    assert service.schedule(live, holdings, [], force=True)
    service._workers[str(live.resolve())].join(timeout=5)
    assert not service.running(live)
    funds = load_funds(live / 'etfs')
    assert len(funds) == 3
    assert all(record['status'] == 'checked' for record in read_json(live / '.cache/etf-refresh/status.json').values())
    classifications = fund_classifications({}, funds, holdings)
    geography = resolve_geography(holdings, funds, {})
    for fund in funds:
        if fund.equity_fund:
            assert all(classifications[row.constituent_id]['sector'] for row in fund.constituents.itertuples())
            assert all(geography.paths[row.constituent_id] != ((UNKNOWN,),) for row in fund.constituents.itertuples())
        else:
            assert fund.breakdown_basis == 'economic'
            assert geography.paths[fund.constituents.iloc[0].constituent_id] == (('Money market',),)
    assert geography.paths['money-market'] == (('Money market',),)
    assert not (live / 'demo_prices.json').exists()
