"""Funding replay tests use deliberately invented contracts and price histories."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from portfolio_app.futures import FuturePosition
from portfolio_app.futures_comparison import ComparisonSettings
from portfolio_app.futures_funding import FundingPolicy, FundingStudy, prepare_replay, replay_policy, trailing_volatility
from portfolio_app.futures_funding_data import FundingHistory, load_funding_history, load_funding_settings, save_funding_settings
from portfolio_app.futures_funding_study import historical_windows, paired_window, run_funding_study, short_horizon_moves
from portfolio_app.holdings import DataError


def position(**kw):
    return replace(FuturePosition(mark_price=100., entry_price=100., margin_per_contract=200.,
        maintenance_per_contract=100., quote_date='2025-01-02', contract_expiry='2025-03-21', reference_price=100.), **kw)


def financial(**kw):
    return replace(ComparisonSettings(financing_source='manual', benchmark_source='manual',
        financing_rate=0., savings_rate=0., cash_rate=0., scale_margin=False, historical_drift=False), **kw)


def prices(values, dates=None):
    return pd.Series(values, index=pd.to_datetime(dates) if dates else pd.bdate_range('2001-01-02', periods=len(values)), dtype=float)


def replay(values, policy=None, reserve=1000., settings=None, contract=None, dates=None):
    settings = settings or financial()
    path = prepare_replay(contract or position(), settings, prices(values, dates))
    return replay_policy(path, settings, reserve, policy or FundingPolicy(400.), record=True)


def test_transit_in_wealth_and_arrival_after_breach_same_close_reentry():
    # Day 1: cash falls from 400 to 200; dispatch 200. Day 2: deposit arrives
    # after a drop to 50 liquidates. Cash 248 can re-enter at initial 200 + fee.
    result, log = replay([100, 98.01, 96.51, 96.51], FundingPolicy(400., delay=1), settings=financial(roll_fee_per_side=1.))
    assert log.iloc[1].dispatched == pytest.approx(200.)
    assert log.iloc[1].bank == pytest.approx(400.)
    assert log.iloc[1].transit == pytest.approx(200.)
    assert log.iloc[2].liquidation and log.iloc[2].reentry
    assert log.iloc[2].received == pytest.approx(200.)
    assert log.iloc[2].exit_cost == log.iloc[2].entry_cost == 1
    assert result['liquidations'] == result['reentries'] == 1
    assert result['missed_intervals'] == 0
    assert result['costs'] == 4  # initial, forced exit, re-entry, endpoint
    assert result['wealth'] == pytest.approx(1000+result['market_pnl']+result['carry_pnl']-4)


def test_strict_margin_boundary_and_positive_delay_validation():
    result, log = replay([100, 97, 97], FundingPolicy(400., trigger=None))
    assert log.iloc[1].broker == 100 and not log.iloc[1].liquidation
    for delay in (0, -1, 1.5, True):
        with pytest.raises(DataError, match='delay'):
            FundingPolicy(100., delay=delay)


def test_pending_transfer_is_not_income_and_retained_at_endpoint():
    result, log = replay([100, 98, 98], FundingPolicy(400., delay=3))
    assert result['ending_transit'] == 200
    assert result['wealth'] == 800
    assert log.iloc[-1].broker+log.iloc[-1].bank+log.iloc[-1].transit == result['wealth']
    assert not log.iloc[-1].held and not log.iloc[-1].reentry


def test_gaps_can_exceed_reserve_and_shortfall_does_not_clip_losses():
    result, log = replay([100, 80, 80, 80], reserve=600.)
    assert result['wealth'] == -1400 and result['reserve_shortfall']
    assert log.broker.min() < 0 and result['liquidations'] == 1
    assert result['missed_intervals'] == 2


def test_opening_uplift_and_infeasible_reserve_are_explicit():
    result, log = replay([100, 100], FundingPolicy(50.), settings=financial(roll_fee_per_side=3.))
    assert result['opening_cash'] == 203
    assert log.iloc[0].bank == 797 and log.iloc[0].broker == 200
    result, log = replay([100, 100], FundingPolicy(50.), reserve=150.)
    assert not result['eligible'] and result['wealth'] == 150 and result['costs'] == 0


def test_roll_fees_and_carry_reconcile_without_roll_gap_profit():
    history = prices(np.ones(300)*100)
    s = financial(financing_rate=.04, roll_fee_per_side=2.)
    p = position(margin_per_contract=20., maintenance_per_contract=10.)
    path = prepare_replay(p, s, history)
    result, log = replay_policy(path, s, 8000., FundingPolicy(6000.), record=True)
    expected_pnl = 100*sum(path.settlement[1:]-path.opening[:-1])
    assert result['market_pnl'] == 0
    assert result['carry_pnl'] == pytest.approx(expected_pnl)
    assert result['costs'] == 2*(2+2*path.roll[:-1].sum())
    assert result['wealth'] == pytest.approx(8000+expected_pnl-result['costs'])
    assert not log.liquidation.any()


def test_roll_funding_failure_is_separate_from_maintenance():
    history = prices(np.ones(70)*100)
    s = financial(roll_fee_per_side=10.)
    path = prepare_replay(position(), s, history)
    result, log = replay_policy(path, s, 210., FundingPolicy(210., trigger=None), record=True)
    assert result['roll_failures'] == 1 and result['liquidations'] == 0
    assert log.roll_failure.sum() == 1 and result['reserve_shortfall']


def test_zero_and_short_contracts_and_exact_time_weighted_cash():
    result, log = replay([100, 90, 110], contract=position(contracts=0), settings=financial(cash_rate=.02, savings_rate=.03))
    assert result['costs'] == result['market_pnl'] == result['carry_pnl'] == 0
    assert result['topups'] == result['liquidations'] == 0
    assert result['wealth'] == pytest.approx(1000+result['broker_interest']+result['bank_interest'])
    result, _ = replay([100, 99, 99], contract=position(contracts=-1))
    assert result['market_pnl'] == 100
    result, _ = replay([100, 98, 98], FundingPolicy(400., delay=3))
    assert result['average_broker'] == 300  # balances held over each interval, not transfer trapezoids


def test_volatility_warmup_and_cohort_use_only_past_returns():
    history = prices(100*np.exp(np.arange(600)*.001))
    vol = trailing_volatility(history)
    assert vol.iloc[:20].isna().all() and np.isfinite(vol.iloc[20])
    study = FundingStudy(1000., FundingPolicy(600.), (FundingPolicy(400.),), low_vol_only=True)
    windows, _, metadata = historical_windows(history, study)
    assert windows[0][0] == 20 and metadata['excluded_warmup'] == 20
    changed = history.copy(); changed.iloc[100:] *= np.linspace(1, 2, len(changed)-100)
    pd.testing.assert_series_equal(trailing_volatility(changed).iloc[:100], vol.iloc[:100])
    with pytest.raises(DataError, match='20 prior'):
        prepare_replay(position(), financial(), history.iloc[:30], volatility=vol, volatility_margin=True)


@pytest.mark.parametrize('volatility_margin', [False, True])
def test_cohort_exclusions_do_not_mutate_warmup_counts(volatility_margin):
    # Invented calm observations followed by alternating large moves. Both
    # exclusions must be present, and together account for every covered start.
    moves = np.r_[np.full(50, .0001), np.tile([.03, -.03], 275)]
    history = pd.Series(100*np.exp(np.cumsum(moves)),
                        index=pd.bdate_range('2000-01-03', periods=len(moves)))
    study = FundingStudy(1000., FundingPolicy(500.), (FundingPolicy(300.),),
                         low_vol_only=True, volatility_margin=volatility_margin)
    windows, _, metadata = historical_windows(history, study)
    assert metadata['covered_windows'] == 339
    assert metadata['excluded_warmup'] == 20
    assert metadata['excluded_cohort'] == 288
    assert metadata['window_count'] == len(windows) == 31
    assert metadata['covered_windows'] == (metadata['excluded_warmup']
        + metadata['excluded_cohort'] + metadata['window_count'])


def test_baseline_identity_attribution_and_infeasible_pairs():
    h = prices(100+np.sin(np.arange(275)/8))
    policy = FundingPolicy(400.)
    study = FundingStudy(1000., policy, (policy, FundingPolicy(500., mode='buffer')), step=21)
    history = FundingHistory(h, 'invented', 'Synthetic', 'Invented observations')
    result = run_funding_study(position(), financial(roll_fee_per_side=1.), study, history)
    assert result.windows.query('candidate == 0').delta_wealth.eq(0).all()
    assert result.metadata['study']['baseline']['amount'] == 400.
    rows, _, _ = paired_window(position(), financial(), study, h.iloc[:20], trailing_volatility(h))
    for row in rows:
        assert row['delta_wealth'] == pytest.approx(sum(row['delta_'+c] for c in ('market_pnl','carry_pnl','bank_interest','broker_interest'))-row['delta_costs'])


def test_read_only_import_settings_revision_and_invalid_history(tmp_path):
    path = tmp_path/'invented.csv'
    prices([100, 101, 102]).rename('eur_index').rename_axis('date').to_csv(path)
    before = path.read_bytes()
    loaded = load_funding_history(path)
    assert loaded.prices.iloc[-1] == 102 and path.read_bytes() == before
    study = FundingStudy(900., FundingPolicy(500.), (FundingPolicy(300.),))
    save_funding_settings(tmp_path, study, str(path), None)
    (saved, saved_path), revision = load_funding_settings(tmp_path)
    assert saved == study and saved_path == str(path)
    with pytest.raises(DataError, match='changed'):
        save_funding_settings(tmp_path, study, str(path), None)
    path.write_text('date,eur_index\n2001-01-02,100\n2001-01-02,101\n')
    with pytest.raises(DataError, match='unique'):
        load_funding_history(path)


def test_short_horizon_uses_running_loss_and_direction():
    result = short_horizon_moves(prices([100, 70, 110]), 2)
    assert result['worst_running'] == pytest.approx(-.3)
    assert result['worst_endpoint'] == pytest.approx(.1)
    assert short_horizon_moves(prices([100, 70, 110]), 2, short=True)['worst_running'] == pytest.approx(-.1)


def test_volatility_stress_scales_both_requirements_and_buffer_tracks_margin():
    h = prices(np.linspace(100, 102, 30))
    vol = pd.Series(.3, index=h.index)
    fixed = prepare_replay(position(), financial(), h)
    stressed = prepare_replay(position(), financial(), h, volatility=vol, volatility_margin=True)
    assert np.allclose(stressed.initial, fixed.initial*2)
    assert np.allclose(stressed.maintenance, fixed.maintenance*2)
    _, log = replay_policy(stressed, financial(), 1500., FundingPolicy(500., mode='buffer'), record=True)
    assert log.iloc[0].target == 700.
    assert log.iloc[0].opening_broker == 700.
    assert (log.target-log.maintenance).eq(500).all()


def test_insufficient_pair_is_counted_without_return_or_risk_estimate():
    h = prices(np.ones(300)*100)
    study = FundingStudy(300., FundingPolicy(200.), (FundingPolicy(250., mode='buffer'),), step=21)
    result = run_funding_study(position(), financial(), study, FundingHistory(h, 'invented', 'Synthetic', 'Invented'))
    assert result.summary.iloc[0].paired_windows == 0
    assert result.summary.iloc[0].infeasible_windows == len(result.windows)
    assert result.windows.delta_wealth.isna().all()
    assert pd.isna(result.summary.iloc[0].liquidation_frequency)


def test_pending_transfer_blocks_second_request_and_monthly_sweeps_conserve_wealth():
    result, log = replay([100, 98, 97.9, 97.9, 97.9], FundingPolicy(400., delay=3))
    assert log.iloc[1].dispatched > 0
    assert log.iloc[2:4].dispatched.eq(0).all()
    assert log.iloc[4].received > 0
    result, log = replay([100, 101, 101], FundingPolicy(400., trigger=None),
                        dates=['2001-01-31','2001-02-01','2001-02-02'])
    # Reset follows the translated simulation calendar, not source month names.
    assert result['wealth'] == 1100
    _, log = replay([100]*22+[101,101], FundingPolicy(400., trigger=None))
    assert log.withdrawal.sum() == 100
    assert log.iloc[-1].wealth == 1100
