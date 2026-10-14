"""Synthetic companion funds and isolated shocks; no working data or providers."""
from dataclasses import replace
from datetime import date
import base64
import json
import numpy as np
import pandas as pd
import pytest
from portfolio_app.etf import FundSnapshot
from portfolio_app.futures import FuturePosition, funded_strategy_positions
from portfolio_app.portfolio_exposure import calculate_portfolio_exposure
from portfolio_app.exposure_presentation import company_move, exposure_positions, companion_nodes
from portfolio_app.strategic import strategic_tree
from portfolio_app.charts import sort_allocation_nodes
from portfolio_app.leverage_settings import MarginLiability, FundFactor
from test_portfolio_exposure import invented, CONFIG


def fixture():
    frame = invented()
    frame['within_bucket_target'] = 1.
    frame.loc[0, 'ticker'] = 'SYNTH-WORLD'
    frame.loc[1, 'isin'] = 'ZZ0000000001'
    fund = FundSnapshot('invented-world', 'Invented world fund', 'ZZ0000000000', ('SYNTH-WORLD',),
        date(2026, 1, 1), 'Synthetic snapshot', pd.DataFrame([
            dict(constituent_id='proxy-company', name='Invented company', ticker='', isin='ZZ0000000001', weight=.4, sector='Invented sector', country='Invented country'),
            dict(constituent_id='second', name='Invented second company', ticker='', isin='ZZ0000000002', weight=.4),
        ]), equity_fund=True)
    future = FuturePosition(bucket_id='core', proxy_position_id='fund', reference_capital_eur=20000.)
    return frame, fund, future


def calculate(frame, fund, future, **kwargs):
    return calculate_portfolio_exposure(frame, [fund], future=future, allocation=CONFIG, **kwargs)


@pytest.mark.parametrize('direction', [1, -1])
def test_proxy_conserves_wealth_notional_gross_and_merges_direct_equity(direction):
    frame, fund, future = fixture()
    future = replace(future, contracts=direction)
    result = calculate(frame, fund, future)
    assert result.net_equity == 120000
    assert result.exposures.value.sum() == result.capital.value.sum() == 120000
    overlay = result.exposures.loc[result.exposures.source_type.eq('future')]
    assert overlay.exposure_eur.sum() == direction * 50000
    assert result.gross_exposure == 140000
    assert overlay.loc[overlay.asset_id.eq('stock'), 'exposure_eur'].item() == direction * 20000
    assert overlay.loc[overlay.asset_id.eq('future-other:FMWN'), 'exposure_eur'].item() == pytest.approx(direction * 10000)
    assert overlay.method.str.contains('ETF holdings proxy').all()
    pnl, pct = company_move(result, 'stock', -5.)
    assert pnl == pytest.approx((30000 + 24000 + direction * 20000) * -.05)
    assert pct == pytest.approx(pnl / 120000 * 100)
    renamed = frame.assign(name=['Renamed ETF', 'Renamed direct share', 'Renamed cash'])
    assert calculate(renamed, fund, future).net_exposure == result.net_exposure


def test_cash_and_missing_proxy_weights_remain_unresolved_index_exposure():
    frame, fund, future = fixture()
    constituents = fund.constituents.copy()
    constituents.loc[1, 'instrument_type'] = 'cash'
    result = calculate(frame, replace(fund, constituents=constituents), future)
    rows = result.exposures.loc[result.exposures.source_type.eq('future')]
    assert rows.exposure_eur.sum() == 50000
    assert rows.loc[rows.asset_id.eq('future-other:FMWN'), 'exposure_eur'].sum() == 30000
    assert 'second' not in set(rows.asset_id)
    for invalid in ['', 'deleted']:
        unknown = calculate(frame, fund, replace(future, proxy_position_id=invalid))
        assert unknown.exposures.loc[unknown.exposures.source_type.eq('future'), 'asset_id'].tolist() == ['index:FMWN']
    adapted = calculate(frame, fund, future, fund_factors=(FundFactor('fund', 2., 'Invented', '2026-01-01'),))
    assert adapted.exposures.loc[adapted.exposures.source_type.eq('future'), 'asset_id'].tolist() == ['index:FMWN']


@pytest.mark.parametrize('changes', [{'shares': 0.}, {'instrument_type': 'equity'}])
def test_closed_or_reclassified_companion_retains_unresolved_index_notional(changes):
    frame, fund, future = fixture()
    for field, value in changes.items():
        frame.loc[frame.position_id.eq('fund'), field] = value
    result = calculate(frame, fund, future)
    rows = result.exposures.loc[result.exposures.source_type.eq('future')]
    assert rows.asset_id.tolist() == ['index:FMWN']
    assert rows.exposure_eur.sum() == future.notional


def test_selected_leverage_cash_unknown_values_and_unassigned_debt():
    frame, fund, future = fixture()
    result = calculate(frame, fund, future)
    assert result.selected_multiple({'core'}) == 110000/80000
    assert result.selected_multiple({'stocks'}) == 1.
    assert result.selected_multiple({'cash'}) == 0.
    assert result.selected_multiple() == 140000/120000
    assert result.selected_multiple({'absent'}) is None
    frame.loc[1, 'current_value_reporting'] = float('nan')
    missing = calculate(frame, fund, future)
    assert missing.selected_multiple() is None
    assert missing.selected_multiple({'core'}) == 110000/80000
    assert missing.selected_multiple({'stocks'}) is None
    for debt in [None, 1000.]:
        assert calculate(*fixture(), liabilities=(MarginLiability('loan', 'Invented loan', debt),)).selected_multiple({'core'}) is None


def test_exposure_projection_and_companion_order_keep_original_capital():
    frame, fund, future = fixture()
    result = calculate(frame, fund, future)
    capital = funded_strategy_positions(frame, future, CONFIG)
    projected = exposure_positions(capital, result, future)
    assert capital.loc[capital.position_id.eq('future:overlay'), 'current_value_reporting'].item() == 20000
    assert projected.loc[projected.position_id.eq('future:overlay'), 'current_value_reporting'].item() == 50000
    nodes = companion_nodes(sort_allocation_nodes(strategic_tree(projected, CONFIG)), future)
    ids = [json.loads(key)[2] for key in nodes.node_id]
    assert ids.index('future:overlay') == ids.index('fund') + 1


def write_proxy(directory, *, fund_ticker='SYNTH-F', direct_ticker='SYNTH-S'):
    import yaml
    _, fund, _ = fixture()
    folder = directory / 'etfs'
    folder.mkdir(exist_ok=True)
    contents = fund.constituents.copy()
    contents.loc[0, 'ticker'] = direct_ticker
    contents.to_csv(folder / 'invented.csv', index=False)
    (folder / 'invented.yaml').write_text(yaml.safe_dump(dict(
        fund_id=fund.fund_id, name=fund.name, isin=fund.isin, tickers=[fund_ticker],
        as_of='2026-01-01', source='Synthetic snapshot', equity_fund=True, holdings_file='invented.csv')))


def test_companion_ui_sunbursts_and_company_move(tmp_path):
    from test_portfolio_exposure_ui import synthetic_workspace
    from test_futures_ui import launch
    from test_ui import activate, by_label, list_frame
    from portfolio_app.futures_workspace import load_sandbox, save_sandbox
    def chart_values(trace):
        values = trace['values']
        return np.frombuffer(base64.b64decode(values['bdata']), dtype=values['dtype']) if isinstance(values, dict) else values
    _, directory = synthetic_workspace(tmp_path)
    write_proxy(directory)
    future, revision = load_sandbox(directory)
    save_sandbox(directory, replace(future, reference_capital_eur=20000., proxy_position_id='fund'), revision)
    app = launch(directory, open_futures=False)
    assert not app.exception
    assert by_label(app.get('button_group'), 'Allocation measure').value == 'Market exposure'
    by_label(app.get('button_group'), 'Allocation measure').set_value('Capital allocation').run()
    capital = json.loads(app.get('plotly_chart')[0].proto.spec)['data'][0]
    ids = [json.loads(key)[2] for key in capital['ids']]
    fi, ci = ids.index('future:overlay'), ids.index('fund')
    assert fi == ci + 1
    assert capital['marker']['colors'][fi] == capital['marker']['colors'][ci]
    assert capital['marker']['pattern']['shape'][fi] == '/'
    assert capital['marker']['pattern']['bgcolor'][fi] == capital['marker']['colors'][ci]
    assert capital['marker']['pattern']['fillmode'] == 'overlay'
    assert capital['marker']['pattern']['fgcolor'][fi] != capital['marker']['colors'][ci]
    assert chart_values(capital)[fi] == 20000
    capital_rows = list_frame(app, 'Positions')
    assert not any('swatchColor' in row for row in list_frame(app, 'Positions').to_dict('records'))
    by_label(app.get('button_group'), 'Allocation measure').set_value('Market exposure').run()
    exposure = json.loads(app.get('plotly_chart')[0].proto.spec)['data'][0]
    assert exposure['type'] == 'sunburst'
    exposure_ids = [json.loads(key)[2] for key in exposure['ids']]
    assert chart_values(exposure)[exposure_ids.index('future:overlay')] == 50000
    # Only the chart/category summary changes measure; owned capital is stable.
    pd.testing.assert_frame_equal(list_frame(app, 'Positions'), capital_rows)
    from list_helpers import list_data
    columns = list_data(app, 'Positions')['columns']
    assert next(column['label'] for column in columns if column['key'] == 'value') == 'Value (EUR)'
    assert capital_rows.set_index('id').loc['future:overlay', 'value'] == 20000
    by_label(app.selectbox, 'Category').set_value('core').run()
    assert list_frame(app, 'Market exposure')['Exposure'].sum() == 110000
    assert by_label(app.metric, 'Category leverage · net').value == '1.38×'
    activate(app, 'Exposure')
    by_label(app.selectbox, 'Company').set_value('stock').run()
    assert by_label(app.metric, 'Estimated P&L').value == '€-3,700.00'
    assert by_label(app.metric, 'Portfolio impact').value == '-3.08%'
    channels = next(item.value for item in app.dataframe if 'Channel' in item.value)
    assert set(channels.Channel) == {'Direct holdings', 'ETF holdings', 'Futures proxy'}
    for view in ['Themes & sectors', 'Geography', 'ETF sources']:
        by_label(app.get('button_group'), 'Market exposure view').set_value(view).run()
        assert not app.exception
        assert json.loads(app.get('plotly_chart')[0].proto.spec)['data'][0]['type'] == 'sunburst'
    activate(app, 'Futures')
    assert by_label(app.selectbox, 'Companion ETF / holdings proxy').value == 'fund'


def test_provider_name_aliases_do_not_overwrite_shared_company_chart_nodes():
    from portfolio_app.exposure_presentation import canonical_asset_names
    from portfolio_app.aggregation import build_tree
    rows = pd.DataFrame([
        dict(asset_id='shared', asset_name='Invented Company plc', path=('Industry',), value=100.),
        dict(asset_id='shared', asset_name='INVENTED COMPANY', path=('Industry',), value=200.),
        dict(asset_id='other', asset_name='Invented Other', path=('Industry',), value=50.),
    ])
    nodes = build_tree(canonical_asset_names(rows), include_holdings=True)
    assert nodes.loc[nodes.is_leaf, 'value'].sum() == 350.
    assert len(nodes.loc[nodes.kind.eq('holding')]) == 2
    assert nodes.loc[nodes.parent_id.eq(''), 'value'].item() == 350.


def test_unknown_exposure_is_unavailable_in_table_not_zero():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from test_future_proxy_exposure import fixture, calculate
from test_portfolio_exposure import CONFIG
from portfolio_app.portfolio_exposure_ui import render_market_analysis
frame, fund, future = fixture()
frame.loc[1, 'current_value_reporting'] = float('nan')
result = calculate(frame, fund, future)
render_market_analysis(result, CONFIG, {}, frame, [fund], future=future)
''').run()
    assert not app.exception
    table = next(item.value for item in app.dataframe if '% of exposure' in item.value)
    assert table['Exposure (EUR)'].isna().any()
    assert app.warning


def test_companion_chart_preserves_node_order_and_style_indices():
    from portfolio_app.charts import hierarchy_chart
    frame, _, future = fixture()
    values = funded_strategy_positions(frame, future, CONFIG)
    nodes = companion_nodes(sort_allocation_nodes(strategic_tree(values, CONFIG)), future)
    figure = hierarchy_chart(nodes, 'Sunburst', preserve_order=True)
    assert list(figure.data[0].ids) == nodes.node_id.tolist()
    ids = [json.loads(value)[2] for value in figure.data[0].ids]
    assert ids.index('future:overlay') == ids.index(future.proxy_position_id) + 1
