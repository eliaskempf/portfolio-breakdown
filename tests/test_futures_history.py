"""Synthetic historical paths: no market downloads or working portfolio data."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from portfolio_app.futures import FuturePosition
from portfolio_app.futures_comparison import ComparisonSettings, simulate_comparison, simulate_trajectory
from portfolio_app.futures_history import HistoricalSettings, historical_projection, quarterly_legs
from portfolio_app.futures_history_data import HISTORY_FILE, parse_estr_history, refresh_history, unpack_history
from portfolio_app.holdings import DataError


def synthetic_history_snapshot():
    dates = pd.bdate_range('2000-01-03', '2003-09-30')
    return dict(version=1, ticker='EUNL.DE', as_of='2003-09-30', fetched_at='2003-10-01T10:00:00+00:00',
                price_dates=dates.strftime('%Y-%m-%d').tolist(), prices=[100.]*len(dates),
                rate_dates=dates.strftime('%Y-%m-%d').tolist(), rates=[0.]*len(dates))


def historical_position(**kwargs):
    return FuturePosition(quote_date='2003-09-30', contract_expiry='2003-12-19', reference_price=100,
                          entry_price=100, mark_price=100, margin_per_contract=1000, maintenance_per_contract=500, **kwargs)


def test_quarters_are_successive_legs_with_shared_roll_boundaries():
    prices, _ = unpack_history(synthetic_history_snapshot())
    for count in (1, 2, 3, 12):
        legs = quarterly_legs(prices, '2003-09-30', count)
        assert len(legs) == count
        assert all(row.end < row.expiry for row in legs.itertuples())
        assert legs.iloc[-1].end == pd.Timestamp('2003-09-18')
        assert legs.start.iloc[1:].tolist() == legs.end.iloc[:-1].tolist()
    with pytest.raises(DataError, match='cover'):
        quarterly_legs(prices.iloc[-40:], '2003-09-30', 3)


def test_flat_index_zero_rates_returns_only_costs_charged_per_side():
    prices, rates = unpack_history(synthetic_history_snapshot())
    settings = HistoricalSettings(quarters=3)
    curve, legs, result = historical_projection(historical_position(contracts=2), ComparisonSettings(financing_source='ecb', financing_spread=0., scale_margin=False, roll_fee_per_side=2, roll_slippage_points=.01), settings, prices, rates)
    assert curve.futures.iloc[0] == 9994  # Two contracts × (€2 + €1 slippage).
    assert curve.futures.iloc[-1] == 10000-2*3*2*3  # Six sides, two contracts.
    assert curve.cash.eq(10000).all()
    assert result['exit_date'] is None and len(legs) == 3


def test_positive_financing_flat_spot_loses_carry_without_booking_roll_gaps():
    prices, rates = unpack_history(synthetic_history_snapshot())
    rates[:] = .05
    curve, legs, _ = historical_projection(historical_position(), ComparisonSettings(financing_source='ecb', financing_spread=0., scale_margin=False), HistoricalSettings(quarters=3), prices, rates)
    expected = 10000 + 100*(legs.exit_points-legs.entry_points).sum()
    assert curve.futures.iloc[-1] == pytest.approx(expected)
    assert curve.futures.iloc[-1] < 10000 < curve.cash.iloc[-1]
    days = (curve.date.iloc[-1]-curve.date.iloc[0]).days
    assert curve.cash.iloc[-1] == pytest.approx(10000*(1+.05/360)**days)


def test_margin_exit_never_reenters_after_recovery_and_short_reverses_profit():
    prices, rates = unpack_history(synthetic_history_snapshot())
    prices.loc['2003-08-01':'2003-08-10'] = 50
    settings = HistoricalSettings(quarters=1)
    curve, _, result = historical_projection(historical_position(), ComparisonSettings(financing_source='ecb', financing_spread=0., scale_margin=False, comparison_capital=1500), settings, prices, rates)
    assert result['status'] == 'Maintenance-floor exit'
    assert result['exit_date'] == pd.Timestamp('2003-08-01')
    assert curve.futures.iloc[-1] == -3500  # Loss beyond collateral is preserved.
    short, _, result = historical_projection(historical_position(contracts=-1), ComparisonSettings(financing_source='ecb', financing_spread=0., scale_margin=False, comparison_capital=1500), settings, prices, rates)
    assert short.futures.max() == 6500 and short.futures.iloc[-1] == 1500
    assert result['exit_date'] is None


def test_current_margin_cannot_be_reopened_at_roll_without_initial_funding():
    prices, rates = unpack_history(synthetic_history_snapshot())
    settings = HistoricalSettings(quarters=2)
    curve, legs, result = historical_projection(historical_position(), ComparisonSettings(financing_source='ecb', financing_spread=0., scale_margin=False, comparison_capital=1010, roll_fee_per_side=4), settings, prices, rates)
    assert result['status'] == 'Insufficient opening margin at roll'
    assert result['exit_date'] == legs.iloc[0].end
    assert curve.futures.iloc[-1] == 1002
    empty, _, result = historical_projection(historical_position(), ComparisonSettings(financing_source='ecb', financing_spread=0., scale_margin=False, comparison_capital=1000, roll_fee_per_side=4), settings, prices, rates)
    assert empty.futures.isna().all() and not result['eligible']


def test_rates_are_observed_over_time_and_missing_coverage_is_rejected():
    prices, rates = unpack_history(synthetic_history_snapshot())
    rates.loc['2003-08-01':] = .04
    curve, _, _ = historical_projection(historical_position(contracts=0), ComparisonSettings(financing_source='ecb', financing_spread=0., scale_margin=False), HistoricalSettings(quarters=1), prices, rates)
    charged_days = (curve.date.iloc[-1]-pd.Timestamp('2003-08-01')).days
    assert curve.cash.iloc[-1] == pytest.approx(10000*(1+.04/360)**charged_days)
    with pytest.raises(DataError, match='coverage'):
        historical_projection(historical_position(), ComparisonSettings(financing_source='ecb', financing_spread=0., scale_margin=False), HistoricalSettings(quarters=1), prices, rates.iloc[-10:])
    with pytest.raises(DataError, match='gap'):
        historical_projection(historical_position(), ComparisonSettings(financing_source='ecb', financing_spread=0., scale_margin=False), HistoricalSettings(quarters=1),
                              prices.drop(prices.loc['2003-08-01':'2003-08-20'].index), rates)


def test_forecast_time_chart_matches_terminal_sensitivity_same_paths():
    position = historical_position()
    settings = ComparisonSettings(comparison_capital=10000, capital_min=5000, capital_max=10000,
                                  fees_per_contract=12, cash_rate=.01, horizon_days=79, levels=2)
    line = simulate_trajectory(position, settings, paths=100)
    result = simulate_comparison(position, settings, paths=100).iloc[-1]
    assert line.day.iloc[-1] == 79
    assert line['median'].iloc[-1] == pytest.approx(result['Futures median end value (EUR)'])
    assert line.cash.iloc[-1] == pytest.approx(result['Cash benchmark end value (EUR)'])
    assert line.breach.iloc[-1] == pytest.approx(result['Margin breach (%)'])
    no_trade = simulate_trajectory(position, replace(settings, comparison_capital=500), paths=10)
    assert no_trade['median'].isna().all() and no_trade.cash.notna().all()


def test_ecb_history_identity_percent_conversion_and_duplicate_rejection():
    content = 'KEY,TIME_PERIOD,OBS_VALUE\nEST.B.EU000A2X2A25.WT,2003-01-02,1.25\n'
    result = parse_estr_history(content)
    assert result.iloc[0] == .0125
    with pytest.raises(DataError):
        parse_estr_history(content.replace('.WT', '.TT'))
    with pytest.raises(DataError):
        parse_estr_history(content + content.splitlines()[1]+'\n')


def test_history_refresh_is_atomic_when_rate_coverage_is_incomplete(tmp_path):
    from portfolio_app.risk_data import AdjustedHistory
    prices, rates = unpack_history(synthetic_history_snapshot())
    class Service:
        def get(self, ticker, years, refresh):
            assert ticker == 'EUNL.DE' and years == 5 and refresh
            return AdjustedHistory(prices, 'EUR')
    def csv(series):
        return 'KEY,TIME_PERIOD,OBS_VALUE\n' + ''.join(
            f'EST.B.EU000A2X2A25.WT,{stamp:%Y-%m-%d},{100*value}\n' for stamp, value in series.items())
    refresh_history(tmp_path, '2003-09-30', service=Service(), fetch=lambda url: csv(rates))
    before = (tmp_path / HISTORY_FILE).read_bytes()
    with pytest.raises(DataError, match='cover'):
        refresh_history(tmp_path, '2003-09-30', service=Service(), fetch=lambda url: csv(rates.iloc[-10:]))
    assert (tmp_path / HISTORY_FILE).read_bytes() == before


def test_shared_manual_cash_and_financing_apply_to_historical_reconstruction():
    prices, rates = unpack_history(synthetic_history_snapshot())
    rates[:] = .10  # Deliberately different from shared manual assumptions.
    comparison = ComparisonSettings(financing_source='manual', financing_rate=0.,
        benchmark_source='manual', savings_rate=.02, cash_rate=0., roll_fee_per_side=3.,
        comparison_capital=14000., scale_margin=False)
    curve, _, result = historical_projection(historical_position(), comparison, HistoricalSettings(quarters=1), prices, rates)
    days = (curve.date.iloc[-1]-curve.date.iloc[0]).days
    assert curve.futures.iloc[-1] == 14000-6
    assert curve.cash.iloc[-1] == pytest.approx(14000*(1+.02/360)**days)
    assert result['exit_date'] is None


def test_shared_spread_matches_manual_carry_but_does_not_increase_cash_yield():
    prices, rates = unpack_history(synthetic_history_snapshot())
    rates[:] = .01
    settings = HistoricalSettings(quarters=3)
    shared = ComparisonSettings(financing_spread=.0075, scale_margin=False)
    curve, legs, result = historical_projection(historical_position(), shared, settings, prices, rates)
    manual, manual_legs, _ = historical_projection(historical_position(),
        replace(shared, financing_source='manual', financing_rate=.0175), settings, prices, rates)
    pd.testing.assert_frame_equal(curve, manual)
    pd.testing.assert_frame_equal(legs, manual_legs)
    plain, _, _ = historical_projection(historical_position(),
        replace(shared, financing_spread=0.), settings, prices, rates)
    assert curve.futures.iloc[-1] < plain.futures.iloc[-1]
    pd.testing.assert_series_equal(curve.cash, plain.cash)
    assert result['exit_date'] is None


def test_spread_uses_historical_ecb_dates_not_saved_current_rate_or_future_data():
    prices, rates = unpack_history(synthetic_history_snapshot())
    rates[:] = .01
    rates.loc['2003-08-01':] = .02
    settings = HistoricalSettings(quarters=1)
    shared = ComparisonSettings(financing_spread=.0075, financing_rate=.15, scale_margin=False)
    curve, legs, _ = historical_projection(historical_position(), shared, settings, prices, rates)
    # Independent carry reconstruction uses the preceding rate, even on a rate-change day.
    anchor_spot = 100 / (1+.0275/360)**80
    entry = float(legs.iloc[0].entry_points)
    for day in [pd.Timestamp('2003-08-01'), pd.Timestamp('2003-08-04')]:
        rate = .0175 if day.day == 1 else .0275
        price = anchor_spot * (1+rate/360)**((legs.iloc[0].expiry-day).days)
        assert curve.set_index('date').loc[day, 'futures'] == pytest.approx(10000+100*(price-entry))
    extended = pd.concat([rates, pd.Series([.19], index=pd.to_datetime(['2003-10-01']))])
    again, _, _ = historical_projection(historical_position(), shared, settings, prices, extended)
    pd.testing.assert_frame_equal(curve, again)


def test_index_history_uses_same_starting_cash_and_holds_through_futures_exit():
    prices, rates = unpack_history(synthetic_history_snapshot())
    prices.loc['2003-08-01':'2003-08-10'] = 50.
    prices.loc['2003-08-11':] = 120.
    settings = ComparisonSettings(financing_source='manual', financing_rate=0.,
                                  scale_margin=False, comparison_capital=1500.)
    curve, _, result = historical_projection(historical_position(), settings,
                                            HistoricalSettings(quarters=1), prices, rates)
    assert result['exit_date'] is not None
    assert curve['index'].iloc[0] == 1500.
    assert curve['index'].iloc[-1] == 1800.
    assert curve.set_index('date').loc['2003-08-01', 'index'] == 750.
    short, _, _ = historical_projection(historical_position(contracts=-1),
        replace(settings, financing_rate=.05, roll_fee_per_side=10.), HistoricalSettings(quarters=1), prices, rates)
    pd.testing.assert_series_equal(curve['index'], short['index'])
