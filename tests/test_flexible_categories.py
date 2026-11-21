"""Deliberately invented portfolios; no working portfolio data or network access."""
from dataclasses import replace

import pandas as pd
import pytest

from portfolio_app.portfolio.allocation import Allocation, Bucket, analysis_targets, validate_allocation
from portfolio_app.portfolio.strategic import bucket_positions, strategic_summary, strategic_tree
from portfolio_app.portfolio.category_rebalancing import category_plan
from portfolio_app.portfolio.rebalancing import RebalanceError


def example(values=(20., 20., 60.)):
    config = Allocation((Bucket('sleeve', 'Example sleeve', target=1.),
        Bucket('group', 'Example chips', 'sleeve', .5, allocation_mode='flexible',
               buy_priority=('p1', 'p2'), sell_priority=('p2', 'p1'))), 3)
    positions = pd.DataFrame([dict(position_id=f'p{i+1}', id=f'asset{i}', name=f'Invented investment {i}',
        account=f'Account {i}', bucket_id='group' if i < 2 else 'sleeve', shares=1.,
        current_value_reporting=value, within_bucket_target=.5) for i, value in enumerate(values)])
    return config, positions


def test_mixed_scope_and_flat_targets_conserve_values():
    config, positions = example()
    validate_allocation(config, positions)
    assert config.leaves('sleeve') == {'group'}
    assert config.descendants('sleeve') == {'sleeve', 'group'}
    assert len(bucket_positions(positions, config, 'sleeve')) == 3
    summary = strategic_summary(positions, config, 'sleeve')
    assert summary['Value'].sum() == 100
    assert sorted(summary['Target (%)']) == [50, 50]
    flat = strategic_summary(positions, config, 'sleeve', flat=True)
    assert len(flat) == 3 and flat['Value'].sum() == 100
    assert flat['Target (%)'].isna().sum() == 2
    for is_flat in (False, True):
        tree = strategic_tree(positions, config, 'sleeve', flat=is_flat)
        assert tree.iloc[0].value == 100
        assert tree.loc[tree.kind.eq('holding'), 'value'].sum() == 100
    assert analysis_targets(positions, config).target_allocation.isna().tolist() == [True, True, False]


def test_flexible_members_do_not_rebalance_when_combined_target_is_met():
    config, positions = example((49., 1., 50.))
    plan = category_plan(positions, config, 'sleeve', mode='fewest', category_tolerance=0, position_tolerance=0)
    assert plan.trade_count == 0
    assert plan.within_bands
    assert plan.table['Target %'].isna().sum() == 2


def test_buy_and_sell_priorities_are_independent():
    config, positions = example()
    plan = category_plan(positions, config, 'sleeve', mode='fewest', category_tolerance=0, position_tolerance=0)
    assert plan.table.set_index('position_id').Trade.to_dict() == pytest.approx({'p1':10., 'p2':0., 'p3':-10.}, abs=1e-5)
    config, positions = example((40., 30., 30.))
    plan = category_plan(positions, config, 'sleeve', mode='fewest', category_tolerance=0, position_tolerance=0)
    assert plan.table.set_index('position_id').Trade.to_dict() == pytest.approx({'p1':0., 'p2':-20., 'p3':20.}, abs=1e-5)


def test_minimum_cash_and_contribution_use_combined_target():
    config, positions = example()
    plan = category_plan(positions, config, 'sleeve', mode='minimum', category_tolerance=0, position_tolerance=0)
    assert plan.new_money == pytest.approx(20.)
    assert plan.table.Trade.tolist() == pytest.approx([20., 0., 0.], abs=1e-5)
    plan = category_plan(positions, config, 'sleeve', amount=20., category_tolerance=0, position_tolerance=0)
    assert plan.unallocated_cash == pytest.approx(0., abs=1e-5)
    assert plan.within_bands


def test_priority_fallback_and_infeasible_sale_protection():
    config, positions = example()
    plan = category_plan(positions, config, 'sleeve', amount=20., eligible_ids=['p2'], category_tolerance=0, position_tolerance=0)
    assert plan.table.Trade.tolist() == pytest.approx([0.,20.,0.], abs=1e-5)
    assert plan.priority_notes
    protected = replace(config, buckets=(replace(config.buckets[0], sell_protected=True), config.buckets[1]))
    with pytest.raises(RebalanceError, match='feasible'):
        category_plan(positions, protected, 'sleeve', mode='fewest', category_tolerance=0, position_tolerance=0)


def test_missing_prices_priorities_and_incomplete_targets_are_explicit():
    config, positions = example()
    incomplete = replace(config, buckets=(config.buckets[0], replace(config.buckets[1], buy_priority=())))
    with pytest.raises(RebalanceError, match='buy priority'):
        category_plan(positions, incomplete, 'sleeve', amount=20.)
    positions.loc[0, 'current_value_reporting'] = float('nan')
    with pytest.raises(RebalanceError, match='valuation'):
        category_plan(positions, config, 'sleeve', amount=20.)
    assert strategic_summary(positions, config, 'sleeve', flat=True)['Current (%)'].isna().all()


def test_fixed_member_targets_multiply_through_flat_scope():
    config, positions = example()
    config = replace(config, buckets=(config.buckets[0], replace(config.buckets[1], allocation_mode='targets')))
    positions.loc[0, 'within_bucket_target'] = .8
    positions.loc[1, 'within_bucket_target'] = .2
    flat = strategic_summary(positions, config, 'sleeve', flat=True).set_index('Category')
    assert flat.loc['Invented investment 0', 'Target (%)'] == 40.
    assert flat.loc['Invented investment 1', 'Target (%)'] == 10.
    plan = category_plan(positions, config, 'sleeve', mode='fewest', category_tolerance=0, position_tolerance=0)
    assert plan.table.After.tolist() == pytest.approx([40.,10.,50.], abs=1e-5)


def test_expandable_rows_keep_scope_targets_and_source_identity():
    config, positions = example()
    positions['name'] = 'Invented duplicate name'
    table = strategic_summary(positions, config, 'sleeve', hierarchy=True).set_index('row_id')
    assert table.loc[table.parent_id.eq(''), 'Value'].sum() == 100.
    assert table.loc['category:group', 'Target (%)'] == 50.
    assert table.loc['position:p1', 'parent_id'] == 'category:group'
    assert table.loc['position:p3', 'parent_id'] == ''
    assert table.loc['position:p1', 'Current (%)'] == 20.
    assert pd.isna(table.loc['position:p1', 'Target (%)'])
    assert table.loc['position:p1', 'Account'] == 'Account 0'
    assert table.loc['position:p2', 'position_id'] == 'p2'
    fixed = replace(config, buckets=(replace(config.buckets[0], target=.6),
                                     replace(config.buckets[1], allocation_mode='targets')))
    whole = strategic_summary(positions, fixed, hierarchy=True).set_index('row_id')
    assert whole.loc['category:group', 'parent_id'] == 'category:sleeve'
    assert whole.loc['position:p1', 'Target (%)'] == pytest.approx(15.)
    scoped = strategic_summary(positions, fixed, 'group', hierarchy=True).set_index('row_id')
    assert scoped.parent_id.eq('').all()
    assert scoped.loc['position:p1', 'Target (%)'] == 50.
    positions.loc[0, 'current_value_reporting'] = float('nan')
    missing = strategic_summary(positions, config, 'sleeve', hierarchy=True)
    assert missing['Current (%)'].isna().all()
    assert missing.set_index('row_id').loc['category:group', 'Status'] == 'Missing price'


def test_parent_first_contribution_routes_to_flexible_and_direct_siblings():
    from portfolio_app.portfolio.scoped_rebalancing import portfolio_contribution
    config, positions = example()
    positions = analysis_targets(positions, config)
    plan = portfolio_contribution(positions, config, 20., eligible_ids=list(positions.position_id), macro_tolerance=0., position_tolerance=0.)
    assert plan.trades.set_index('position_id').Trade.get('p1') == pytest.approx(20., abs=1e-5)
    assert plan.unallocated_cash == pytest.approx(0., abs=1e-5)
    assert plan.after.current_value_reporting.sum() == pytest.approx(120.)


def test_caps_do_not_allow_buying_an_oversized_flexible_member():
    config, positions = example((35.,5.,60.))
    plan = category_plan(positions, config, 'sleeve', amount=20., eligible_ids=['p1','p2'],
        max_allocations={'p1':.5}, cap_scope='bucket', category_tolerance=0., position_tolerance=0.)
    assert plan.table.Trade.tolist() == pytest.approx([0.,20.,0.], abs=1e-5)


def test_flexible_empty_exclusion_does_not_redistribute_inactive_targets():
    from portfolio_app.portfolio.allocation import ignore_empty_by_bucket
    config, positions = example((40.,0.,60.))
    positions.loc[1,'shares'] = 0.
    positions.loc[:1,'within_bucket_target'] = float('nan')
    kept = ignore_empty_by_bucket(positions, config)
    assert kept.position_id.tolist() == ['p1','p3']
    assert pd.isna(kept.iloc[0].within_bucket_target)
    plan = category_plan(kept, config, 'sleeve', amount=20., category_tolerance=0., position_tolerance=0.)
    assert plan.table.Trade.tolist() == pytest.approx([20.,0.], abs=1e-5)


def test_flexible_contributions_use_whole_cents_and_preserve_remainder():
    config, positions = example((20.,20.,60.))
    plan = category_plan(positions, config, 'group', amount=1., eligible_ids=['p1'], max_allocations={'p1':.49})
    assert plan.table.Trade.tolist() == pytest.approx([.09,0.])
    assert plan.unallocated_cash == pytest.approx(.91)
    assert plan.table.After.sum()+plan.unallocated_cash == pytest.approx(41.)
