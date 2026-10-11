"""Explicitly synthetic reserve/contract examples; no working data or network."""
from dataclasses import replace
import json

import pandas as pd
import pytest

from portfolio_app.generalized_studies import (
    CashPool, Collateral, InterestRate, Observation, Roll, ScenarioContract,
    ScenarioInput, ScheduledPayment, StudyPolicy, compare_policies, forward_paths,
    funding_cohorts, reconstruct, replay,
)
from portfolio_app.generalized_studies_ui import load_study_settings, save_study_settings
from portfolio_app.holdings import DataError


def scenario(**kwargs):
    defaults = dict(contract=ScenarioContract('SYNTHETIC', 'EUR', 10., '2027-06-18', .5, 'price'),
        quantity=2, initial_price=100., broker=(CashPool('EUR', 1000.),),
        initial_margin=100., maintenance_margin=50., settlement_credit_limit=10000.)
    return ScenarioInput(**(defaults | kwargs))


def path(prices=(100., 110., 105.), fx=None):
    dates = pd.date_range('2027-01-01', periods=len(prices))
    rates = fx or [dict(EUR=1., USD=.8, GBP=1.2)]*len(prices)
    return tuple(Observation(d.date().isoformat(), p, f) for d, p, f in zip(dates, prices, rates))


def test_fees_payments_and_native_pnl_reconcile_once():
    s = scenario(fee_per_side=2., slippage_points=.1,
        payments=(ScheduledPayment('2027-01-02', 'EUR', 15.),))
    result = replay(s, path())
    assert result.ending_wealth == pytest.approx(1073.)  # +100 P&L -12 both sides -15 tax
    assert result.attribution['market_pnl'] == 100.
    assert result.attribution['fees'] == -12.
    assert result.attribution['taxes'] == -15.
    assert abs(result.reconciliation_error) < 1e-10
    assert result.events.iloc[-1].notional == 0.


@pytest.mark.parametrize('currency,multiplier', [('EUR', 1.), ('USD', 25.), ('GBP', 2.5)])
def test_native_currency_and_multiplier_are_generic(currency, multiplier):
    s = scenario(contract=ScenarioContract('INVENTED', currency, multiplier, '2027-06-18'),
        broker=(CashPool(currency, 1000.),), quantity=-1)
    r = replay(s, path((100., 90.)))
    conversion = dict(EUR=1., USD=.8, GBP=1.2)[currency]
    assert r.attribution['market_pnl'] == pytest.approx(10*multiplier*conversion)
    assert r.reconciliation_error == pytest.approx(0.)


def test_fx_applies_to_currency_assets_not_futures_notional():
    s = scenario(contract=ScenarioContract('SYNTH-USD', 'USD', 100., '2027-06-18'),
        broker=(CashPool('USD', 1000.),), bank=(CashPool('GBP', 100.),))
    r = replay(s, path((100., 100.), [dict(EUR=1., USD=.8, GBP=1.2), dict(EUR=1., USD=.9, GBP=1.3)]))
    assert r.attribution['market_pnl'] == 0.
    assert r.attribution['fx_pnl'] == pytest.approx(110.)
    assert r.ending_wealth == pytest.approx(1030.)
    assert r.reconciliation_error == pytest.approx(0.)


def test_exact_maintenance_holds_but_below_liquidates():
    exact = replay(scenario(), path((100., 55.)))
    below = replay(scenario(), path((100., 54.99)))
    assert 'liquidation' not in exact.events.iloc[-1].events
    assert 'liquidation' in below.events.iloc[-1].events
    assert below.ending_wealth == pytest.approx(99.8)


def test_foreign_collateral_supports_margin_not_settlement_cash():
    s = scenario(broker=(CashPool('EUR', 0.), CashPool('USD', 1000.)), settlement_credit_limit=0.)
    r = replay(s, path((100., 99.)))
    assert r.events.iloc[0].held
    assert 'liquidation' in r.events.iloc[1].events
    assert r.events.iloc[1].broker_EUR == -20.
    assert r.ending_wealth == 780.
    assert r.reconciliation_error == pytest.approx(0.)


def test_haircut_reduces_margin_capacity_never_wealth():
    s = scenario(broker=(CashPool('EUR', 0.),), collateral=(Collateral('EUR', 1000., .9),))
    r = replay(s, path((100., 100.)))
    assert not r.events.iloc[0].held
    assert r.initial_wealth == r.ending_wealth == 1000.
    assert r.events.iloc[0].eligible_collateral == pytest.approx(100.)


def test_ordered_conversions_transit_and_cost_attribution():
    s = scenario(bank=(CashPool('USD', 100.), CashPool('GBP', 100.)), broker=(CashPool('EUR', 200.),))
    p = StudyPolicy(350., 300., sources=('USD', 'GBP'), delay=2, conversion_cost=.1, transfer_fee=2.)
    r = replay(s, path((100., 100., 100., 100.)), p)
    first = r.events.iloc[0]
    assert first.bank_USD == pytest.approx(0.)
    assert first.bank_GBP > 0.
    assert first.transit == pytest.approx(150.)
    assert r.events.iloc[1].transit == pytest.approx(150.)
    assert r.events.iloc[2].transit == 0.
    assert r.events.iloc[2].broker_EUR == pytest.approx(350.)
    assert r.attribution['transfer_costs'] < 0.
    assert r.reconciliation_error == pytest.approx(0.)


def test_margin_check_precedes_receipt_and_can_reenter_after_receipt():
    s = scenario(bank=(CashPool('EUR', 500.),), broker=(CashPool('EUR', 200.),))
    p = StudyPolicy(500., 300., sources=('EUR',), delay=1)
    r = replay(s, path((100., 94., 94.)), p)
    assert 'liquidation' in r.events.iloc[1].events
    assert 'transfer received' in r.events.iloc[1].events
    assert 'reentry' in r.events.iloc[1].events
    assert r.ending_wealth == pytest.approx(580.)


def test_roll_gap_not_pnl_and_both_sides_charged():
    s = scenario(fee_per_side=1.)
    observations = (Observation('2027-01-01', 100., {'EUR': 1.}),
        Observation('2027-01-02', 105., {'EUR': 1.}, 120., '2027-09-17'),
        Observation('2027-01-03', 122., {'EUR': 1.}))
    r = replay(s, observations)
    assert r.attribution['market_pnl'] == 140.
    assert r.attribution['fees'] == -8.
    assert r.ending_wealth == 1132.


def test_credit_debit_interest_and_rate_no_lookahead():
    s = scenario(bank=(CashPool('EUR', 1000.),), broker=(CashPool('EUR', -100.),),
        collateral=(Collateral('EUR', 1000., 0.),),
        rates=(InterestRate('EUR', .36, .36, .72, '2027-01-01'),
               InterestRate('EUR', .72, .36, .72, '2027-01-03')))
    r = replay(s, path((100., 100., 100.)))
    assert r.attribution['bank_interest'] == pytest.approx(2.001)
    assert r.attribution['broker_interest'] == pytest.approx(-.4004)
    assert r.reconciliation_error == pytest.approx(0., abs=1e-10)


def test_expiry_gaps_missing_fx_payment_dates_and_fractional_quantity_fail():
    with pytest.raises(DataError, match='expiry'):
        replay(scenario(contract=ScenarioContract('S', 'EUR', 10., '2027-01-02')), path())
    with pytest.raises(DataError, match='FX'):
        replay(scenario(bank=(CashPool('USD', 1.),)), (Observation('2027-01-01', 100., {'EUR': 1.}), Observation('2027-01-02', 100., {'EUR': 1.})))
    with pytest.raises(DataError, match='payment'):
        replay(scenario(payments=(ScheduledPayment('2027-01-04', 'EUR', 1.),)), path())
    with pytest.raises(DataError, match='whole'):
        scenario(quantity=1.5)


def test_proxy_requires_qualification_and_roll_calendar():
    s = scenario()
    proxy = pd.Series([100., 101., 102.], index=pd.date_range('2027-01-01', periods=3))
    fx = {d.date().isoformat(): {'EUR': 1.} for d in proxy.index}
    with pytest.raises(DataError, match='convention'):
        reconstruct(s, proxy, fx, proxy_currency='EUR', proxy_convention='net')
    observations = reconstruct(s, proxy, fx, proxy_currency='EUR', proxy_convention='price',
        net_carry=.05, rolls=(Roll('2027-01-02', '2027-09-17'),))
    assert observations[0].price == pytest.approx(100.)
    assert observations[1].roll_entry_price > observations[1].price
    assert abs(replay(s, observations).reconciliation_error) < 1e-9


def test_forward_shared_paths_reproducibility_and_benchmarks():
    s = scenario()
    dates = pd.date_range('2027-01-01', periods=4)
    fx = {d.date().isoformat(): {'EUR': 1.} for d in dates}
    a = list(forward_paths(s, dates, fx, paths=2))
    b = list(forward_paths(s, dates, fx, paths=2))
    assert a == b
    summary, runs, examples = compare_policies(s, iter(a), {'A': None, 'B': None})
    assert summary.paths.tolist() == [2, 2]
    assert runs.loc[runs.policy.eq('A'), 'ending_wealth'].tolist() == runs.loc[runs.policy.eq('B'), 'ending_wealth'].tolist()
    assert runs.index_benchmark.notna().all()
    assert list(examples) == ['A', 'B']


def test_cohort_gate_has_no_future_lookahead():
    s = scenario()
    dates = pd.date_range('2020-01-01', periods=30)
    proxy = pd.Series([100.]*22+[1000.]*8, index=dates)
    fx = {d.date().isoformat(): {'EUR': 1.} for d in dates}
    cohorts = list(funding_cohorts(s, proxy, fx, start='2027-01-01', opening_fx={'EUR': 1.},
        window=5, step=1, low_volatility=.01, proxy_currency='EUR', proxy_convention='price'))
    assert len(cohorts) == 2  # Starts 21,22 see only previous stable returns.
    assert cohorts[0][-1].price == 1000.


def test_settings_revision_backup_and_journal_untouched(tmp_path):
    journal = tmp_path/'synthetic-journal.json'
    journal.write_text('{"synthetic": true}')
    before = journal.read_bytes()
    settings, revision = load_study_settings(tmp_path)
    assert settings == {} and revision is None
    save_study_settings(tmp_path, 'synthetic:EUR', {'mode': 'Forward Monte Carlo'}, revision)
    _, revision = load_study_settings(tmp_path)
    save_study_settings(tmp_path, 'synthetic:EUR', {'mode': 'Historical settlements'}, revision)
    with pytest.raises(DataError, match='changed'):
        save_study_settings(tmp_path, 'synthetic:EUR', {}, revision)
    assert list((tmp_path/'.backups').iterdir())
    assert journal.read_bytes() == before
    assert json.loads((tmp_path/'generalized-study-settings.json').read_text())['version'] == 1


def test_snapshot_input_and_fx_mapping_remain_immutable():
    supplied_fx = {'EUR': 1.}
    observation = Observation('2027-01-01', 100., supplied_fx)
    supplied_fx['EUR'] = 2.
    assert observation.fx['EUR'] == 1.
    s = scenario()
    before = repr(s)
    replay(s, path())
    assert repr(s) == before


def test_periodic_withdrawal_retains_margin_and_transit_is_wealth():
    s = scenario()
    observations = tuple(Observation(day, 100., {'EUR': 1.}) for day in ('2027-01-31','2027-02-01','2027-02-02','2027-02-03'))
    p = StudyPolicy(300., 100., sources=('EUR',), delay=1, transfer_fee=1., withdraw_excess=True)
    r = replay(s, observations, p)
    assert r.events.iloc[1].broker_EUR == 300.
    assert r.events.iloc[1].transit == 699.
    assert r.events.iloc[2].bank_EUR == 699.
    assert r.ending_wealth == 999.
    assert r.reconciliation_error == pytest.approx(0.)


def test_buffer_policy_targets_maintenance_plus_buffer():
    s = scenario(bank=(CashPool('EUR', 500.),), broker=(CashPool('EUR', 200.),))
    p = StudyPolicy(200., 200., sources=('EUR',), delay=1, mode='buffer')
    r = replay(s, path((100., 99., 99., 99.)), p)
    assert r.events.iloc[1].transit == 120.
    assert r.events.iloc[2].broker_EUR == 300.
    assert r.reconciliation_error == pytest.approx(0.)


def test_transit_carries_destination_currency_fx_only():
    s = scenario(bank=(CashPool('USD', 1000.),), broker=(CashPool('EUR', 200.),))
    p = StudyPolicy(500., 300., sources=('USD',), delay=2)
    r = replay(s, path((100.,100.,100.,100.), [dict(EUR=1.,USD=.5),dict(EUR=1.,USD=1.),dict(EUR=1.,USD=1.),dict(EUR=1.,USD=1.)]),p)
    assert r.events.iloc[0].transit == 300.
    assert r.events.iloc[0].bank_USD == 400.
    assert r.attribution['fx_pnl'] == pytest.approx(200.)
    assert r.reconciliation_error == pytest.approx(0.)
