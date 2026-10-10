"""Invented contract, rates and paths; no market downloads or working data."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from portfolio_app.futures import FuturePosition
from portfolio_app.futures_comparison import ComparisonSettings, cash_growth, comparison_dates
from portfolio_app.futures_rolling import rolling_calendar, simulate_rolling
from portfolio_app.holdings import DataError


def position():
    return FuturePosition(entry_price=100., mark_price=100., margin_per_contract=2000.,
        maintenance_per_contract=1500., quote_date='2025-01-02', contract_expiry='2025-03-21', reference_price=100.)


def settings(**kwargs):
    return replace(ComparisonSettings(rolling=True, rolling_years=1, volatility=0., financing_rate=0.,
        capital_min=2000., capital_max=10000., levels=3, comparison_capital=6000.), **kwargs)


def test_quarterly_roll_costs_and_same_cash_benchmark():
    s = settings(roll_fee_per_side=5., roll_slippage_points=.02, cash_rate=0.)
    result, path, schedule = simulate_rolling(position(), s, paths=2)
    assert len(schedule) == 4
    assert schedule.iloc[0]['date'] == pd.Timestamp('2025-03-20')
    assert path.iloc[-1]['median'] == 6000 - (2+2*4)*7
    assert path.iloc[-1]['cash'] == pytest.approx(6000*cash_growth(s.savings_rate, 365/365.25))
    selected = result.loc[result['Collateral (EUR)'].eq(s.comparison_capital)].iloc[0]
    assert selected['Futures median end value (EUR)'] == path.iloc[-1]['median']
    assert pd.isna(result.iloc[0]['Margin breach (%)'])  # cannot pay opening fee
    assert path.forced_exit.eq(0).all()


def test_roll_gap_is_not_a_profit_and_financing_decays_between_rolls():
    s = settings(financing_rate=.04, cash_rate=0., scale_margin=False)
    _, path, schedule = simulate_rolling(position(), s, paths=1)
    start, end = map(pd.Timestamp, comparison_dates(s, position()))
    carry = lambda n: (1+.04/360)**n
    spot = 100/carry((pd.Timestamp(position().contract_expiry)-start).days)
    previous = 100.
    pnl = 0.
    expiry = pd.Timestamp(position().contract_expiry)
    for roll in schedule.itertuples():
        pnl += 100*(spot*carry((expiry-roll.date).days)-previous)
        expiry = roll.next_expiry
        previous = spot*carry((expiry-roll.date).days)
    pnl += 100*(spot*carry((expiry-end).days)-previous)
    assert path.iloc[-1]['median'] == pytest.approx(6000+pnl)
    assert pnl < 0


def test_roll_funding_failure_is_distinct_from_maintenance_breach():
    s = settings(comparison_capital=2200., roll_fee_per_side=100., scale_margin=False)
    _, path, _ = simulate_rolling(position(), s, paths=1)
    # Entry leaves 2100; first roll closes to 2000, cannot reopen with 2000+100.
    assert path.iloc[-1]['median'] == 2000
    assert path.iloc[-1]['roll_failure'] == 100
    assert path.iloc[-1]['breach'] == 0
    assert path.iloc[-1]['forced_exit'] == 100


def test_floor_breach_stops_even_if_index_recovers_and_negative_cash_survives():
    s = settings(comparison_capital=4000., roll_fee_per_side=10., scale_margin=False)
    dates, _ = rolling_calendar(position(), s)
    ratios = np.ones((len(dates), 1))
    ratios[1] = .1
    _, path, _ = simulate_rolling(position(), s, index_ratios=ratios)
    assert path.iloc[-1]['median'] == pytest.approx(-5020)
    assert path.iloc[-1]['breach'] == 100
    assert path.iloc[-1]['roll_failure'] == 0


def test_short_margin_scaling_can_trigger_breach_without_fixed_floor_touch():
    s = settings(comparison_capital=4000.)
    p = replace(position(), contracts=-1)
    dates, _ = rolling_calendar(p, s)
    ratios = np.ones((len(dates), 1))
    ratios[1] = 1.23  # balance1700; scaledfloor1845, fixedfloor1500
    _, scaled, _ = simulate_rolling(p, s, index_ratios=ratios)
    _, fixed, _ = simulate_rolling(p, replace(s, scale_margin=False), index_ratios=ratios)
    assert scaled.iloc[-1]['breach'] == 100
    assert fixed.iloc[-1]['forced_exit'] == 0


def test_horizon_prefixes_and_cumulative_probabilities_are_consistent():
    s = settings(volatility=.25)
    _, short, _ = simulate_rolling(position(), s, paths=100)
    _, long, _ = simulate_rolling(position(), replace(s, rolling_years=3), paths=100)
    for column in ['breach', 'roll_failure', 'forced_exit']:
        assert long[column].is_monotonic_increasing
        assert long.iloc[-1][column] >= short.iloc[-1][column]
    common = short.merge(long, on='day', suffixes=('_short', '_long'))
    assert (common.breach_short == common.breach_long).all()
    assert np.allclose(long.breach+long.roll_failure, long.forced_exit)
    pd.testing.assert_frame_equal(long, simulate_rolling(position(), replace(s, rolling_years=3), paths=100)[1])


def test_no_contract_has_only_cash_growth_even_with_execution_costs():
    s = settings(cash_rate=.01, roll_fee_per_side=20., volatility=.5)
    _, path, _ = simulate_rolling(replace(position(), contracts=0), s, paths=2)
    assert path.iloc[-1]['median'] == pytest.approx(6000*cash_growth(.01, 365/365.25))
    assert path.forced_exit.eq(0).all()


def test_long_horizon_dates_validation_and_settings_persistence(tmp_path):
    from portfolio_app.demo import create_demo_data
    from portfolio_app.futures_workspace import fork_futures_workspace, load_comparison, save_comparison
    s = settings(rolling_years=30, index_return=.04, roll_fee_per_side=2.)
    assert comparison_dates(s, position())[1].isoformat() == '2055-01-02'
    sandbox = fork_futures_workspace(create_demo_data(tmp_path/'source'), tmp_path/'sandbox')
    _, revision = load_comparison(sandbox)
    save_comparison(sandbox, s, revision)
    assert load_comparison(sandbox)[0] == s
    for kwargs in [dict(rolling_years=31), dict(index_return=1.), dict(financing_rate=float('nan')),
                   dict(roll_fee_per_side=-1.)]:
        with pytest.raises(DataError):
            settings(**kwargs)
    with pytest.raises(DataError, match='dated'):
        simulate_rolling(replace(position(), contract_expiry='', quote_date=''), s, paths=1)


def test_single_contract_and_rolling_share_price_growth_financing_and_costs():
    from portfolio_app.futures_rolling import simulate_holding
    from portfolio_app.futures_comparison import futures_price_drift
    s = settings(rolling=False, months=1, until_expiry=False, index_return=.06,
                 financing_rate=.03, roll_fee_per_side=2., scale_margin=False)
    _, curve, rolls = simulate_holding(position(), s, paths=1)
    days = curve.day.iloc[-1]
    expected = 6000 + 10000*(np.exp(futures_price_drift(.06, .03)*days/365.25)-1) - 4
    assert curve['median'].iloc[-1] == pytest.approx(expected)
    assert curve['median'].iloc[-1] > 6000 and rolls.empty
    _, rolling, _ = simulate_holding(position(), replace(s, rolling=True), paths=1)
    common = curve.iloc[:-1].merge(rolling, on='day', suffixes=('_single', '_rolling'))
    assert len(common) > 1
    assert common['median_single'].tolist() == pytest.approx(common['median_rolling'].tolist())


def test_single_contract_cannot_silently_continue_past_expiry():
    from portfolio_app.futures_rolling import simulate_holding
    with pytest.raises(DataError, match='end by expiry'):
        simulate_holding(position(), settings(rolling=False, until_expiry=False, months=12), paths=1)


@pytest.mark.parametrize('rolling', [False, True])
@pytest.mark.parametrize('contracts', [1, -1])
@pytest.mark.parametrize('capital', [100., 4000.])
def test_index_comparison_is_fully_funded_same_paths_and_independent_of_futures(rolling, contracts, capital):
    from portfolio_app.futures_rolling import simulate_holding
    s = settings(rolling=rolling, comparison_capital=capital, financing_rate=.03,
                 roll_fee_per_side=10., scale_margin=False)
    p = replace(position(), contracts=contracts)
    dates, _ = rolling_calendar(p, s)
    ratios = np.ones((len(dates), 3))
    ratios[1] = .1  # Long futures exit; index holds through the recovery.
    ratios[2:] = [.8, 1.1, 1.6]
    result, curve, _ = simulate_holding(p, s, index_ratios=ratios)
    assert curve.index_median.iloc[0] == capital
    assert curve.index_median.iloc[-1] == pytest.approx(capital*1.1)
    assert result['Index median end value (EUR)'].to_numpy() == pytest.approx(result['Collateral (EUR)'].to_numpy()*1.1)
    if capital == 100.:
        assert curve['median'].isna().all()
    elif contracts == 1:
        assert curve.breach.iloc[-1] == 100
    _, changed, _ = simulate_holding(p, replace(s, financing_rate=.08, cash_rate=.01,
        roll_fee_per_side=100.), index_ratios=ratios)
    np.testing.assert_allclose(curve.index_median, changed.index_median)
