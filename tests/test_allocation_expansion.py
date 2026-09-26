"""Invented allocation scenarios; never load a personal workspace."""

import pandas as pd
import pytest

from portfolio_app.allocation import (Allocation, Bucket, analysis_targets, ignore_empty_by_bucket,
                                     load_allocation, macro_table, migrate, migration_preview, validate_allocation)
from portfolio_app.balances import patch_holdings
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.positions import read_snapshot
from portfolio_app.scoped_rebalancing import portfolio_contribution, sleeve_positions
from portfolio_app.storage import revision
from portfolio_app.rebalancing import prepare_rebalance, minimum_trades, RebalanceError


def invented():
    rows = parse_holdings('id,name,shares,portfolio,account,target_allocation,bucket_id,within_bucket_target\na,Invented A,1,Sleeve,Demo,0.4,active,0.6\nb,Invented B,1,Sleeve,Demo,0.6,active,0.4\nc,Invented C,1,Reserve,Demo,,core,1\n')
    rows['current_value_eur'] = [60., 40., 300.]
    return rows


@pytest.fixture
def config():
    return Allocation((Bucket('active', 'Active', target=.25), Bucket('core', 'Reserve', target=.75, sell_protected=True)))


def test_parent_targets_and_exclusive_macro_ownership(config):
    rows = analysis_targets(invented(), config)
    assert rows.target_allocation.tolist() == pytest.approx([.15, .10, .75])
    table = macro_table(rows, config)
    assert table['EUR value'].sum() == 400
    assert table['Current portfolio %'].sum() == 100
    nested = Allocation((Bucket('parent', 'Parent', target=.5), Bucket('active', 'Active', 'parent', .4), Bucket('core', 'Reserve', 'parent', .6)))
    assert nested.global_target('active') == pytest.approx(.2)
    with pytest.raises(DataError):
        validate_allocation(Allocation((Bucket('a', 'A', 'b'), Bucket('b', 'B', 'a'))))


def test_migration_preserves_raw_targets_and_keys_survive_reordering(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,shares,portfolio,target_allocation\na,Invented A,1,Sleeve,40%\nb,Invented B,1,Sleeve,60%\n')
    snapshot = read_snapshot(path)
    config, preview = migration_preview(snapshot.holdings)
    assert all(b.target is None for b in config.buckets)
    migrate(path, config, preview, expected_revision=snapshot.revision)
    migrated = read_snapshot(path)
    assert migrated.holdings.target_allocation.tolist() == [.4, .6]
    assert migrated.holdings.within_bucket_target.tolist() == [.4, .6]
    assert list((tmp_path / '.backups').glob('*.csv'))
    assert load_allocation(tmp_path / 'allocation.yaml', migrated.holdings) == config
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    raw.iloc[::-1].to_csv(path, index=False)
    assert read_snapshot(path).holdings.position_id.tolist() == migrated.holdings.position_id.tolist()[::-1]
    with pytest.raises(DataError):
        migrate(path, config, preview, expected_revision=snapshot.revision)


def test_interrupted_activation_retains_legacy_semantics(tmp_path, monkeypatch):
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,shares,portfolio,target_allocation\na,Invented,1,Sleeve,1\n')
    snap = read_snapshot(path)
    config, preview = migration_preview(snap.holdings)
    import portfolio_app.allocation as module
    original = module.save_allocation
    monkeypatch.setattr(module, 'save_allocation', lambda *a, **k: (_ for _ in ()).throw(OSError('interrupted')))
    with pytest.raises(OSError):
        migrate(path, config, preview, expected_revision=snap.revision)
    assert load_allocation(tmp_path / 'allocation.yaml') is None
    assert read_snapshot(path).holdings.target_allocation.tolist() == [1.]
    monkeypatch.setattr(module, 'save_allocation', original)
    snap = read_snapshot(path)
    config, preview = migration_preview(snap.holdings)
    migrate(path, config, preview, expected_revision=snap.revision)
    assert load_allocation(tmp_path / 'allocation.yaml') is not None


def test_balance_replacement_is_idempotent_and_stale_safe(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,shares,acquisition_price,acquisition_currency,target_allocation\na,Invented,1,,,0.3\n')
    snap = read_snapshot(path)
    edits = {'position-0': {'shares': '0.0001234567', 'acquisition_price': '', 'acquisition_currency': '', 'holdings_confirmed_on': '2026-01-01'}}
    patch_holdings(path, edits, expected_revision=snap.revision, replacement=True)
    with pytest.raises(DataError):
        patch_holdings(path, edits, expected_revision=snap.revision, replacement=True)
    patch_holdings(path, edits, expected_revision=revision(path), replacement=True)
    saved = read_snapshot(path).holdings
    assert saved.shares.iloc[0] == pytest.approx(.0001234567)
    assert saved.target_allocation.iloc[0] == .3
    before = path.read_bytes()
    with pytest.raises(DataError):
        patch_holdings(path, {'position-0': {'shares': '-1'}}, expected_revision=revision(path), replacement=True)
    assert path.read_bytes() == before


def test_empty_redistribution_stays_in_bucket(config):
    rows = invented()
    rows.loc[0, 'shares'] = 0
    kept = ignore_empty_by_bucket(rows)
    assert kept.within_bucket_target.tolist() == [1., 1.]
    rows.loc[1, 'shares'] = 0
    kept = ignore_empty_by_bucket(rows)
    assert kept.bucket_id.tolist() == ['core']
    assert config.global_target('active') == .25


def test_macro_first_is_independent_of_number_of_positions(config):
    rows = invented()
    plan = portfolio_contribution(rows, config, 100., eligible_ids=rows.position_id.tolist())
    budgets = plan.budgets.set_index('Bucket ID')['Budget (EUR)']
    assert budgets['active'] == 25
    assert budgets['core'] == 75
    assert plan.trades['Trade (EUR)'].sum() + plan.unallocated_cash == 100
    assert plan.after.current_value_eur.sum() + plan.unallocated_cash == 500
    assert plan.after.loc[plan.after.bucket_id == 'active', 'current_value_eur'].sum() == 125


def test_sleeve_works_with_unrelated_missing_targets(config):
    rows = invented()
    rows.loc[2, 'within_bucket_target'] = float('nan')
    problem = prepare_rebalance(sleeve_positions(rows, config, 'active'))
    assert problem.total == 100
    assert problem.targets.tolist() == [.6, .4]
    rows.loc[2, 'current_value_eur'] = float('nan')
    assert prepare_rebalance(sleeve_positions(rows, config, 'active')).total == 100


def test_global_caps_cash_and_trade_limit(config):
    rows = invented()
    plan = portfolio_contribution(rows, config, 100., eligible_ids=rows.position_id.tolist(), max_trades=1)
    assert (plan.trades['Trade (EUR)'] != 0).sum() <= 1
    assert plan.trades['Trade (EUR)'].sum() + plan.unallocated_cash == 100
    plan = portfolio_contribution(rows, config, 100., eligible_ids=rows.position_id.tolist(),
                                  max_allocations=dict.fromkeys(rows.position_id, 0.))
    assert plan.unallocated_cash == 100
    assert plan.trades.empty


def test_sell_protection(config):
    import numpy as np
    rows = sleeve_positions(invented(), config, 'active')
    rows['current_value_eur'] = [90., 10.]
    problem = prepare_rebalance(rows, tolerance=0.)
    with pytest.raises(RebalanceError):
        minimum_trades(problem, sell_allowed=np.array([False, True]))


def test_invalid_tree_assignment_and_unknown_targets(config):
    rows = invented()
    rows.loc[0, 'bucket_id'] = 'missing'
    with pytest.raises(DataError):
        validate_allocation(config, rows)
    rows.loc[0, 'bucket_id'] = ''
    assert pd.isna(analysis_targets(rows, config).target_allocation.iloc[0])
    rows.loc[1, 'within_bucket_target'] = float('nan')
    assert pd.isna(analysis_targets(rows, config).target_allocation.iloc[1])
    rows.loc[1, 'within_bucket_target'] = 0.
    assert analysis_targets(rows, config).target_allocation.iloc[1] == 0.


def test_nested_routing_and_buy_all_minimums():
    rows = invented()
    rows['current_value_eur'] = [0., 0., 0.]
    config = Allocation((Bucket('root', 'Invented parent', target=1),
                         Bucket('active', 'Invented active', 'root', .4), Bucket('core', 'Invented reserve', 'root', .6)))
    plan = portfolio_contribution(rows, config, 100., eligible_ids=rows.position_id.tolist(),
                                  buy_all=True, minimum_purchase=15., max_trades=3)
    assert plan.budgets.set_index('Bucket ID').loc['active', 'Budget (EUR)'] == 40.
    assert (plan.trades['Trade (EUR)'] >= 15.).all()
    assert plan.unallocated_cash == 0
    with pytest.raises(RebalanceError):
        portfolio_contribution(rows, config, 30., eligible_ids=rows.position_id.tolist(), buy_all=True, minimum_purchase=15.)


def test_cap_denominators_change_actual_capacity(config):
    rows = invented()
    ids = rows.position_id.tolist()
    global_plan = portfolio_contribution(rows, config, 100., eligible_ids=ids, max_allocations={ids[0]: .15})
    local_plan = portfolio_contribution(rows, config, 100., eligible_ids=ids, max_allocations={ids[0]: .15}, cap_scope='bucket')
    assert global_plan.after.current_value_eur.iloc[0] == pytest.approx(75.)
    assert local_plan.after.current_value_eur.iloc[0] == 60.
    assert global_plan.after.current_value_eur.sum() + global_plan.unallocated_cash == 500.
    assert local_plan.after.current_value_eur.sum() + local_plan.unallocated_cash == 500.


def test_manual_price_fractional_crypto_fx_and_missing_quote():
    from datetime import datetime, timezone
    from portfolio_app.prices import Quote, PriceService
    from portfolio_app.valuation import value_holdings
    now = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    class Provider:
        def price(self, ticker):
            if ticker == 'INVENTED-EUR':
                return Quote(500., 'EUR', datetime(2026, 9, 18, 12, tzinfo=timezone.utc))
            raise ValueError('Unavailable')
        def fx(self, currency):
            return Quote(.8, 'EUR', now)
    rows = parse_holdings('id,name,shares,ticker,instrument_type,manual_price,manual_price_currency,manual_price_date,quantity_unit\na,Invented token,0.0000001234,INVENTED-EUR,crypto,,,,\nb,Invented metal,2,,physical,30,USD,2026-09-01,grams\nc,Missing token,1,MISSING-EUR,crypto,,,,\n')
    valued = value_holdings(rows, PriceService(Provider(), now=lambda: now))
    assert valued.current_value_eur.iloc[0] == pytest.approx(.0000001234 * 500)
    assert 'continuously' in valued.valuation_note.iloc[0]
    assert valued.current_value_eur.iloc[1] == 48.
    assert valued.price_status.iloc[1] == 'manual'
    assert pd.isna(valued.current_value_eur.iloc[2])


def test_balance_update_blocks_purchases_already_in_snapshot(tmp_path):
    from portfolio_app.purchases import save_purchase_batch
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,shares,acquisition_price,acquisition_currency\na,Invented,1,10,EUR\n')
    patch_holdings(path, {'position-0': {'shares': '2', 'holdings_confirmed_on': '2026-01-05'}}, expected_revision=revision(path), replacement=True)
    with pytest.raises(DataError, match='already be included'):
        save_purchase_batch(path, {}, [{'date': '2026-01-04', 'shares': '1', 'price': '10', 'fees': '0'}], currency='EUR', position_id='position-0', expected_revision=revision(path))
    save_purchase_batch(path, {}, [{'date': '2026-01-06', 'shares': '1', 'price': '10', 'fees': '0'}], currency='EUR', position_id='position-0', expected_revision=revision(path))
    assert read_snapshot(path).holdings.shares.iloc[0] == 3.


def test_minimal_csv_balance_replacement_and_invalid_ancestor(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,shares\na,Invented,1\n')
    patch_holdings(path, {'position-0': {'shares': '2'}}, expected_revision=revision(path), replacement=True)
    assert read_snapshot(path).holdings.shares.iloc[0] == 2.
    with pytest.raises(DataError, match='existing parent'):
        validate_allocation(Allocation((Bucket('child', 'Child', 'parent'), Bucket('parent', 'Parent', 'missing'))))


def test_failed_balance_replace_keeps_original_and_backup(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,shares\na,Invented,1\n')
    before = path.read_bytes()
    def fail(*args):
        raise OSError('Synthetic interrupted replace')
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError):
        patch_holdings(path, {'position-0': {'shares': '2'}}, expected_revision=revision(path), replacement=True)
    assert path.read_bytes() == before
    assert next((tmp_path / '.backups').glob('*.csv')).read_bytes() == before
    assert not list(tmp_path.glob('*.tmp'))
