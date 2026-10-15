"""Synthetic version migrations, stable hierarchy and constrained trade examples."""
from dataclasses import replace
import json

import pandas as pd
import pytest

from portfolio_app.allocation import Allocation, Bucket
from portfolio_app.exposure_targets import ExposureTargets, exposure_gaps, load_exposure_targets, save_exposure_targets, validate_targets
from portfolio_app.exposure_planning import plan_exposure
from portfolio_app.futures import FuturePosition
from portfolio_app.holdings import DataError
from portfolio_app.leverage_settings import LeverageSettings, MarginLiability, FundFactor, load_leverage_settings, save_leverage_settings
from portfolio_app.portfolio_exposure import calculate_portfolio_exposure

CONFIG = Allocation((Bucket('assets', 'Invented assets'), Bucket('stock', 'Invented stock', 'assets'), Bucket('gold', 'Invented gold', 'assets')))


def invented():
    return pd.DataFrame([dict(position_id=key, id=key, name='Invented '+key, instrument_type=kind, exposure_kind=declared,
        current_value_reporting=value, reporting_currency='EUR', shares=value, bucket_id=key, isin='', ticker='', account='Synthetic')
        for key, kind, declared, value in [('stock', 'equity', 'equity', 60.), ('gold', 'physical', 'non_equity', 30.)]])


def future(**kwargs):
    return FuturePosition(contracts=1, entry_price=.4, mark_price=.4, reference_capital_eur=10., bucket_id='stock', **kwargs)


def policy(**kwargs):
    return ExposureTargets(1.3, 'composition', {'stock': 100/130, 'gold': 30/130}, version=2, universe='market', **kwargs)


def result(position=None):
    return calculate_portfolio_exposure(invented(), allocation=CONFIG, future=position or future())


def test_missing_legacy_and_active_migration_is_read_only(tmp_path):
    assert not load_leverage_settings(tmp_path)[0].enabled
    assert load_leverage_settings(tmp_path, future=future())[0].enabled
    assert not list(tmp_path.iterdir())
    path = tmp_path/'leverage-settings.json'
    for debt, expected in [([], False), ([dict(id='loan', name='Invented loan', balance_eur=None)], True)]:
        raw = json.dumps(dict(version=1, liabilities=debt, fund_factors=[]))
        path.write_text(raw)
        settings, rev = load_leverage_settings(tmp_path)
        assert settings.enabled == expected and settings.active_basis == 'capital' and settings.version == 2
        assert path.read_text() == raw
        save_leverage_settings(tmp_path, settings, rev)
        assert load_leverage_settings(tmp_path)[0] == settings
    path.write_text(json.dumps(dict(version=1, liabilities=[], fund_factors=[dict(position_id='stock', factor=None)])))
    assert load_leverage_settings(tmp_path)[0].enabled


@pytest.mark.parametrize('version', [True, '2', 3, 1.0, None])
def test_invalid_settings_versions(tmp_path, version):
    (tmp_path/'leverage-settings.json').write_text(json.dumps(dict(version=version, liabilities=[], fund_factors=[])))
    with pytest.raises(DataError):
        load_leverage_settings(tmp_path)


def test_settings_conflicts_and_safe_disable(tmp_path):
    save_leverage_settings(tmp_path, LeverageSettings(enabled=True), None)
    settings, rev = load_leverage_settings(tmp_path)
    save_leverage_settings(tmp_path, replace(settings, active_basis='exposure'), rev)
    with pytest.raises(DataError, match='changed'):
        save_leverage_settings(tmp_path, settings, rev)
    with pytest.raises(DataError, match='disabled'):
        LeverageSettings((MarginLiability('loan', 'Invented loan', 1.),), enabled=False)
    with pytest.raises(DataError):
        LeverageSettings(enabled=False, active_basis='exposure')


def test_legacy_policy_retains_scope_and_explicit_market_conversion(tmp_path):
    legacy = ExposureTargets(1., 'composition', {'stock': 1.})
    save_exposure_targets(tmp_path, legacy, CONFIG, None)
    loaded, rev = load_exposure_targets(tmp_path, CONFIG)
    assert loaded.universe == 'equity'
    equity = exposure_gaps(result(), loaded, CONFIG).set_index('Bucket ID')
    assert equity.loc['gold', 'Current exposure (EUR)'] == 0
    broader = replace(loaded, version=2, universe='market')
    save_exposure_targets(tmp_path, broader, CONFIG, rev)
    assert exposure_gaps(result(), broader, CONFIG)['Current exposure (EUR)'].sum() == 130
    assert result().net_equity == 100 and result().net_market_multiple == 1.3


def test_parent_relative_targets_rename_reparent_and_global_denominator():
    target = ExposureTargets(1.3, 'composition', {'assets': 1., 'stock': .75, 'gold': .25}, version=2, universe='market', weights='parent_relative')
    parent = exposure_gaps(result(), target, CONFIG, parent='')
    leaves = exposure_gaps(result(), target, CONFIG, parent='assets')
    assert parent['Desired exposure (EUR)'].item() == 130
    assert leaves['Desired exposure (EUR)'].sum() == 130
    renamed = Allocation(tuple(replace(b, name='Renamed '+b.id) for b in CONFIG.buckets))
    assert exposure_gaps(result(), target, renamed)['Desired exposure (EUR)'].tolist() == exposure_gaps(result(), target, CONFIG)['Desired exposure (EUR)'].tolist()
    reparented = Allocation(tuple(replace(b, parent='') if b.id == 'gold' else b for b in CONFIG.buckets))
    with pytest.raises(DataError, match='siblings'):
        validate_targets(target, reparented)
    assert leaves['Composition gap (pp)'].sum() == pytest.approx(0)


def test_future_integer_rounding_fees_and_residual_not_cash_budget():
    target = replace(policy(), multiple=1.5, composition={'stock': .8, 'gold': .2})
    plan = plan_exposure(result(), target, CONFIG, invented(), permitted=['future:overlay'], future=future(),
        collateral_limit=10., margin_per_contract=2., future_fee=1., max_contracts=5)
    assert plan.equity == 100-plan.fees
    assert plan.unallocated_cash == 0
    assert plan.trades['Funded trade (EUR)'].sum() == 0
    assert not plan.complete
    assert plan.margin_required <= plan.collateral_remaining
    assert plan.gaps['Desired exposure (EUR)'].sum() == pytest.approx(plan.equity*1.5)


def test_funded_purchase_contribution_changes_denominator_once():
    target = replace(policy(), multiple=1., composition={'stock': .75, 'gold': .25})
    pos = replace(future(), contracts=0)
    plan = plan_exposure(result(pos), target, CONFIG, invented(), permitted=['stock', 'gold'], future=pos, contribution=30.)
    assert plan.equity == 130.
    assert plan.trades['Funded trade (EUR)'].sum() <= 30.+1e-6
    assert plan.gaps['Desired exposure (EUR)'].sum() == 130.
    assert not plan.complete  # Existing dedicated cash cannot silently fund purchases.
    assert plan.gaps['Current exposure (EUR)'].sum() == pytest.approx(120.)


def test_collateral_constraints_and_declared_contribution_are_honest():
    target = replace(policy(), multiple=2., composition={'stock': .85, 'gold': .15})
    constrained = plan_exposure(result(), target, CONFIG, invented(), permitted=['future:overlay'], future=future(), collateral_limit=2., margin_per_contract=2., max_contracts=5)
    assert constrained.trades.empty and not constrained.complete
    funded = plan_exposure(result(), target, CONFIG, invented(), permitted=['future:overlay'], future=future(), collateral_limit=2., margin_per_contract=2., max_contracts=5, contribution=10., future_funding=10.)
    assert funded.equity == 110. and funded.unallocated_cash == 0.
    assert funded.margin_required <= 12.
    with pytest.raises(DataError, match='cannot exceed'):
        plan_exposure(result(), target, CONFIG, invented(), permitted=['future:overlay'], future=future(), collateral_limit=11., margin_per_contract=2.)


def test_sale_proceeds_and_fees_no_simultaneous_buy_sell():
    pos = replace(future(), contracts=0)
    target = replace(policy(), multiple=.9, composition={'stock': 1., 'gold': 0.})
    plan = plan_exposure(result(pos), target, CONFIG, invented(), permitted=['stock', 'gold'], future=pos, allow_sales=True, fee_per_unit=.01)
    assert plan.equity == pytest.approx(100-plan.trades['Fees (EUR)'].sum())
    assert plan.fees == pytest.approx(plan.trades['Fees (EUR)'].sum())
    assert plan.unallocated_cash == pytest.approx(-plan.trades['Funded trade (EUR)'].sum()-plan.fees, abs=1e-6)
    assert set(plan.trades.Action) == {'Buy', 'Sell'}


def test_unknown_unassigned_nonpositive_equity_block_planning():
    for bad in [replace(result(), net_equity=0), replace(result(), exposure_complete=False)]:
        with pytest.raises(DataError):
            plan_exposure(bad, policy(), CONFIG, invented(), permitted=['stock'])
    unknown = result().exposures.copy(); unknown.loc[0, 'bucket_id'] = ''
    with pytest.raises(DataError, match='Assign'):
        plan_exposure(replace(result(), exposures=unknown), policy(), CONFIG, invented(), permitted=['stock'])


def test_legacy_overlay_plan_keeps_funded_exposures():
    target = ExposureTargets(1.4, 'overlay_only', {}, 'stock')
    planned = plan_exposure(result(), target, CONFIG, invented(), permitted=['future:overlay'], future=future(), collateral_limit=10., margin_per_contract=2., max_contracts=4)
    assert planned.complete
    assert planned.trades['Units / contracts'].item() == 1


def test_exhausted_futures_funding_is_not_borrowing_or_available_collateral():
    pos = replace(future(), net_funding_eur=-20.)
    assert pos.capital_value == -10.
    plan = plan_exposure(result(pos), policy(), CONFIG, invented(), permitted=['future:overlay'], future=pos,
        contribution=10., future_funding=10., collateral_limit=0., margin_per_contract=2., max_contracts=3)
    assert plan.margin_required == 0.
    assert plan.trades['Units / contracts'].item() == -1
    assert plan.equity == result(pos).net_equity+10.


def test_short_permissions_debt_denominator_and_infeasibility():
    pos = replace(future(), contracts=-1)
    target = replace(policy(), multiple=.2, composition={'stock': 0., 'gold': 1.})
    long_only = plan_exposure(result(pos), target, CONFIG, invented(), permitted=['future:overlay'], future=pos,
        collateral_limit=10., margin_per_contract=2., max_contracts=3)
    assert long_only.trades['Units / contracts'].item() >= 1
    shorts = plan_exposure(result(pos), target, CONFIG, invented(), permitted=['future:overlay'], future=pos,
        collateral_limit=10., margin_per_contract=2., max_contracts=3, allow_short=True)
    assert shorts.gaps['Gap (EUR)'].abs().sum() <= long_only.gaps['Gap (EUR)'].abs().sum()
    borrowed = calculate_portfolio_exposure(invented(), allocation=CONFIG, future=future(), liabilities=(MarginLiability('loan', 'Invented loan', 20.),))
    assert exposure_gaps(borrowed, policy(), CONFIG)['Desired exposure (EUR)'].sum() == 104.
    with pytest.raises(DataError, match='feasible'):
        plan_exposure(result(), policy(), CONFIG, invented(), permitted=['future:overlay'], future=future(),
            collateral_limit=0., margin_per_contract=2., future_fee=1., max_contracts=3)
