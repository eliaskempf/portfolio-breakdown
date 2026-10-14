"""Explicitly invented funded strategies; no working data or network access."""
from dataclasses import replace
import json
import pytest
from portfolio_app.futures import FuturePosition, close_contract, funded_strategy_positions, futures_scenarios, summarize_future
from portfolio_app.futures_workspace import load_sandbox, save_sandbox
from portfolio_app.holdings import DataError
from portfolio_app.position_list import overview_position_rows
from test_portfolio_exposure import CONFIG, invented, calculate
from test_portfolio_exposure_ui import synthetic_workspace
from test_futures_ui import launch
from test_ui import activate, by_label


def strategy(**kwargs):
    return FuturePosition(reference_capital_eur=10000., mark_price=510., closed_pnl_eur=300., fees_eur=50., bucket_id='core', **kwargs)


def test_funding_is_wealth_not_gain_or_exposure_and_reconciles_once():
    p = strategy()
    result = calculate(future=p)
    assert p.lifetime_gain == 1250.
    assert p.capital_value == 11250.
    assert p.reference_return_pct == 12.5
    assert result.net_equity == 111250.
    assert result.capital.value.sum() == result.net_equity
    assert result.exposures.value.sum() == result.net_equity
    assert result.settlement_adjustment == 0
    for funding in [-3000., 2000.]:
        adjusted = replace(p, net_funding_eur=funding)
        other = calculate(future=adjusted)
        assert other.net_equity == result.net_equity + funding
        assert adjusted.lifetime_gain == p.lifetime_gain
        assert adjusted.reference_return_pct == p.reference_return_pct
        assert other.net_exposure == result.net_exposure
        assert other.net_multiple == pytest.approx(result.net_exposure / other.net_equity)


def test_close_and_new_entry_preserve_lifetime_gain_and_remaining_cash():
    p = strategy(net_funding_eur=500.)
    closed = close_contract(p)
    assert closed.contracts == closed.notional == closed.pnl == 0
    assert closed.closed_pnl_eur == 1300
    assert closed.capital_value == p.capital_value
    assert closed.lifetime_gain == p.lifetime_gain
    rolled = replace(closed, contracts=1, entry_price=520., mark_price=520.)
    assert rolled.capital_value == closed.capital_value
    assert rolled.reference_return_pct == p.reference_return_pct
    assert replace(rolled, mark_price=525.).lifetime_gain == p.lifetime_gain + 500
    assert calculate(future=closed).net_equity == calculate(future=p).net_equity
    assert calculate(future=closed).net_exposure == 90000


def test_display_and_scenarios_include_cash_without_shocking_cash():
    p = strategy(net_funding_eur=500.)
    frame = invented()
    display = funded_strategy_positions(frame, p, CONFIG)
    rows = overview_position_rows(display, CONFIG, future=p)
    row = next(row for row in rows if row['id'] == 'future:overlay')
    assert row['value'] == p.capital_value
    assert row['returnPct'] == 12.5
    assert sum(row['allocation'] for row in rows) == pytest.approx(100.)
    result = calculate(future=p)
    curve = futures_scenarios(summarize_future(frame, p), p, [0., -.1], portfolio_result=result)
    assert curve.iloc[0]['With futures (EUR)'] == result.net_equity
    assert curve.iloc[1]['With futures (EUR)'] == result.net_equity - .1*p.notional
    negative = replace(p, closed_pnl_eur=-20000.)
    assert negative.capital_value < 0
    assert calculate(future=negative).futures_capital_value < 0


@pytest.mark.parametrize('fields', [dict(reference_capital_eur=0), dict(net_funding_eur=float('nan')), dict(fees_eur=-1), dict(reference_capital_eur=True)])
def test_invalid_funding_rejected(fields):
    with pytest.raises(DataError):
        replace(strategy(), **fields)


def test_funded_ui_roundtrip_negative_chart_and_closed_position(tmp_path):
    _, sandbox = synthetic_workspace(tmp_path)
    _, revision = load_sandbox(sandbox)
    save_sandbox(sandbox, strategy(), revision)
    app = launch(sandbox)
    assert not app.exception
    by_label(app.number_input, 'Net additional funding (€)').set_value(500.).run()
    by_label(app.button, 'Save mock position').click().run()
    assert not app.exception and not app.error
    assert load_sandbox(sandbox)[0].capital_value == 11750.
    activate(app, 'Overview')
    assert not app.exception
    assert by_label(app.metric, 'Portfolio value').value == '€111,750.00'
    by_label(app.selectbox, 'Category').set_value('core').run()
    assert not app.exception
    assert by_label(app.metric, 'Category value').value == '€71,750.00'
    by_label(app.get('button_group'), 'Overview view').set_value('Performance').run()
    assert not app.exception
    activate(app, 'Futures')
    by_label(app.button, 'Close saved contract and retain cash').click().run()
    assert not app.exception and not app.error
    assert load_sandbox(sandbox)[0].capital_value == 11750.
    by_label(app.number_input, 'Previous contracts P&L (€)').set_value(-20000.).run()
    by_label(app.button, 'Save mock position').click().run()
    activate(app, 'Overview')
    by_label(app.get('button_group'), 'Overview view').set_value('Allocation').run()
    by_label(app.get('button_group'), 'Allocation measure').set_value('Capital allocation').run()
    assert not app.exception
    traces = [trace for chart in app.get('plotly_chart') for trace in json.loads(chart.proto.spec)['data']]
    assert any(trace['type'] == 'bar' and -9550 in trace['x'] for trace in traces)
    from test_ui import list_frame
    assert list_frame(app, 'Positions').set_index('id').loc['future:overlay', 'value'] == -9550


def test_future_category_survives_display_name_changes():
    from portfolio_app.allocation import Allocation
    from portfolio_app.portfolio_exposure import calculate_portfolio_exposure, strategy_exposure
    renamed = Allocation(tuple(replace(bucket, name='Renamed allocation') if bucket.id == 'core' else bucket for bucket in CONFIG.buckets))
    p = strategy()
    before = calculate(future=p)
    after = calculate_portfolio_exposure(invented(), future=p, allocation=renamed)
    assert before.net_equity == after.net_equity
    assert after.exposures.loc[after.exposures.source_position_id.eq('future:overlay'), 'bucket_id'].item() == 'core'
    summary = strategy_exposure(after, renamed).set_index('ID')
    assert summary.loc['core', 'Category'] == 'Renamed allocation'
    assert summary.loc['core', 'Exposure (EUR)'] == 60000 + p.notional


def test_futures_beta_integration_category_and_account_filters(tmp_path):
    _, sandbox = synthetic_workspace(tmp_path)
    p,revision=load_sandbox(sandbox)
    save_sandbox(sandbox,replace(p,reference_capital_eur=10000.),revision)
    app=launch(sandbox,open_futures=False)
    by_label(app.get('button_group'),'Overview view').set_value('Analytics').run()
    by_label(app.button,'Calculate risk').click().run()
    assert not app.exception
    beta=by_label(app.metric,'Beta').value
    assert beta!='—'
    table=next(item.value for item in app.dataframe if 'Beta contribution' in item.value)
    assert table.Investment.str.contains('FMWN future').any()
    by_label(app.selectbox,'Category').set_value('core').run()
    assert not app.exception
    assert by_label(app.metric,'Beta contribution').value!='—'
    accounts=by_label(app.multiselect,'Accounts')
    accounts.set_value([x for x in accounts.value if not x.startswith('__future_overlay__')]).run()
    assert not app.exception
    table=next(item.value for item in app.dataframe if 'Beta contribution' in item.value)
    assert not table.Investment.str.contains('FMWN future').any()
