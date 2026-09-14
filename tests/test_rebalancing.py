"""Synthetic portfolios only; independently verify optimizer objectives and constraints."""

from itertools import combinations
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from portfolio_app.rebalancing import (
    RebalanceError, allocate_new_money, cash_tradeoffs, ignore_empty_positions,
    minimum_new_money, minimum_trades, prepare_rebalance,
)


def portfolio(values, targets, **kwargs):
    n = len(values)
    frame = pd.DataFrame({"id": [f"synthetic-{i}" for i in range(n)],
                          "name": [f"Synthetic {i}" for i in range(n)],
                          "position_id": [f"row-{i}" for i in range(n)],
                          "shares": np.array(values) / 10, "current_value_eur": values,
                          "target_allocation": targets})
    return prepare_rebalance(frame, **kwargs)


def assert_conserved(plan, total, *, buys_only=False):
    assert plan.table["Trade (EUR)"].sum() == pytest.approx(plan.new_money, abs=1e-5)
    assert plan.table["After (EUR)"].sum() == pytest.approx(total + plan.new_money)
    assert plan.table["After %"].sum() == pytest.approx(100)
    assert plan.trade_count == plan.buy_count + plan.sell_count
    if buys_only:
        assert plan.sell_count == 0
        assert plan.table["Trade (EUR)"].min() >= 0


def test_minimum_trades_then_turnover_uses_range_boundary():
    problem = portfolio([60., 40.], [.5, .5])
    plan = minimum_trades(problem)
    assert plan.trade_count == 2
    assert plan.table["Trade (EUR)"].tolist() == pytest.approx([-9.5, 9.5])
    assert plan.within_bands
    assert_conserved(plan, 100)


def test_minimum_trade_count_matches_independent_subset_enumeration():
    rng = np.random.default_rng(13)
    for _ in range(12):
        values = rng.uniform(1, 50, size=5)
        targets = rng.dirichlet(np.ones(5))
        p = portfolio(values, targets, tolerance=8)
        current = values / values.sum()
        # For each proposed set of trades, unchanged weights must already be in
        # range and the remaining total must fit the traded positions' intervals.
        feasible = []
        for count in range(6):
            for selected in combinations(range(5), count):
                traded = np.array([i in selected for i in range(5)])
                fixed_ok = ((current[~traded] >= p.lower[~traded]) & (current[~traded] <= p.upper[~traded])).all()
                available = 1 - current[~traded].sum()
                if fixed_ok and p.lower[traded].sum() - 1e-10 <= available <= p.upper[traded].sum() + 1e-10:
                    feasible.append(count)
            if feasible:
                break
        plan = minimum_trades(p)
        assert plan.trade_count == min(feasible)
        assert plan.within_bands
        assert_conserved(plan, values.sum())


@pytest.mark.parametrize("tolerance, expected", [(0., 60.), (.5, 80/.505 - 100)])
def test_minimum_cash_matches_closed_form(tolerance, expected):
    plan = minimum_new_money(portfolio([80., 20.], [.5, .5], tolerance=tolerance))
    assert plan.new_money == pytest.approx(expected)
    assert plan.trade_count == 1
    assert plan.within_bands
    assert_conserved(plan, 100, buys_only=True)


def test_minimum_cash_exact_targets_random_closed_form():
    rng = np.random.default_rng(12)
    for _ in range(8):
        values = rng.uniform(1, 100, size=4)
        targets = rng.dirichlet(np.ones(4))
        p = portfolio(values, targets, tolerance=0)
        plan = minimum_new_money(p)
        assert plan.new_money == pytest.approx(max(values / targets) - sum(values))
        assert_conserved(plan, sum(values), buys_only=True)
        assert plan.within_bands


def test_cash_tradeoff_and_max_trades():
    p = portfolio([80., 10., 10.], [.4, .3, .3], tolerance=0)
    one = allocate_new_money(p, 100, max_trades=1)
    two = allocate_new_money(p, 100, max_trades=2)
    assert one.trade_count == 1
    assert one.deviation_after == pytest.approx(50)
    assert not one.within_bands
    assert two.trade_count == 2
    assert two.table["Trade (EUR)"].tolist() == pytest.approx([0, 50, 50], abs=1e-5)
    assert two.within_bands
    for plan in (one, two):
        assert_conserved(plan, 100, buys_only=True)
    progress = []
    frontier = cash_tradeoffs(p, 100, max_trades=3, progress=lambda done, total: progress.append((done, total)))
    assert [plan.trade_count for plan in frontier] == [1, 2]
    assert progress == [(1, 3), (2, 3)]


def test_already_balanced_and_zero_cash_need_no_trades():
    p = portfolio([50., 50.], [.5, .5])
    for plan in (minimum_trades(p), minimum_new_money(p), allocate_new_money(p, 0, max_trades=0)):
        assert plan.trade_count == 0
        assert plan.new_money == 0
        assert plan.within_bands
    plan = allocate_new_money(p, .5, max_trades=2)
    assert plan.trade_count == 1  # Exact targets would require two; both are in range.
    assert_conserved(plan, 100, buys_only=True)


def test_no_new_position_restriction_and_ignored_target_redistribution():
    p = portfolio([60., 40., 0.], [.4, .4, .2], tolerance=0)
    for solve in (minimum_trades, minimum_new_money):
        with pytest.raises(RebalanceError, match="feasible|finite buy-only"):
            solve(p, no_new_positions=True)
    plan = allocate_new_money(p, 20, max_trades=2, no_new_positions=True)
    assert plan.table["Trade (EUR)"].iloc[2] == 0
    assert not plan.within_bands
    assert_conserved(plan, 100, buys_only=True)
    kept = prepare_rebalance(ignore_empty_positions(p.positions), tolerance=0)
    assert kept.targets.tolist() == pytest.approx([.5, .5])
    assert minimum_new_money(kept, no_new_positions=True).new_money == pytest.approx(20)


def test_positive_holding_with_zero_target_can_require_selling():
    p = portfolio([20., 80.], [0., 1.], tolerance=0)
    with pytest.raises(RebalanceError, match="finite buy-only"):
        minimum_new_money(p)
    plan = minimum_trades(p)
    assert plan.table["Trade (EUR)"].tolist() == pytest.approx([-20, 20])


def test_empty_portfolio_can_allocate_a_budget_but_has_no_unique_minimum_cash():
    p = portfolio([0., 0.], [.4, .6], tolerance=0)
    plan = allocate_new_money(p, 100, max_trades=2)
    assert plan.table["After (EUR)"].tolist() == pytest.approx([40, 60])
    assert_conserved(plan, 0, buys_only=True)
    for solve in (minimum_trades, minimum_new_money):
        with pytest.raises(RebalanceError):
            solve(p)
    with pytest.raises(RebalanceError, match="feasible"):
        allocate_new_money(p, 100, max_trades=2, no_new_positions=True)


def test_no_new_positions_is_per_account_row_and_never_merges_trades():
    p = portfolio([80., 20., 0.], [.4, .4, .2], tolerance=0)
    p.positions["id"] = ["same", "other", "same"]
    p.positions["account"] = ["Synthetic A", "Synthetic A", "Synthetic B"]
    unrestricted = minimum_trades(p)
    assert unrestricted.trade_count == 3
    assert unrestricted.table.position_id.nunique() == 3
    restricted = allocate_new_money(p, 100, max_trades=3, no_new_positions=True)
    assert restricted.table["Trade (EUR)"].iloc[2] == 0


def test_ignore_empty_shares_targets_equally_per_asset_then_account_without_mutation():
    frame = pd.DataFrame({"id": ["a", "a", "b", "c"], "shares": [1., 2., 3., 0.],
                          "target_allocation": [.1, .2, .4, .3]})
    original = frame.copy(deep=True)
    result = ignore_empty_positions(frame)
    assert result.target_allocation.tolist() == pytest.approx([.175, .275, .55])
    assert result.target_allocation.sum() == pytest.approx(1)
    pd.testing.assert_frame_equal(frame, original)
    assert ignore_empty_positions(frame.assign(shares=0)).empty
    assert len(ignore_empty_positions(frame.drop(columns="target_allocation"))) == 3
    assert len(ignore_empty_positions(frame.assign(target_allocation=np.nan))) == 3
    with pytest.raises(RebalanceError, match="missing targets"):
        ignore_empty_positions(frame.assign(target_allocation=[.1, .2, np.nan, .3]))


def test_relative_and_absolute_bands():
    p = portfolio([10, 90], [.1, .9], tolerance=5, tolerance_type="relative")
    assert p.lower.tolist() == pytest.approx([.095, .855])
    assert p.upper.tolist() == pytest.approx([.105, .945])
    p = portfolio([0, 100], [0, 1], tolerance=.5)
    assert p.upper[0] == .005
    assert p.upper[1] == 1
    p = portfolio([0, 100], [0, 1], tolerance=5, tolerance_type="relative")
    assert p.upper[0] == 0


@pytest.mark.parametrize("values, targets", [([10, np.nan], [.5, .5]), ([-1, 10], [.5, .5]),
    ([10, 10], [None, .5]), ([10, 10], [.4, .5]), ([10, 10], [-.1, 1.1]), ([10, 10], [np.inf, 0])])
def test_invalid_valuations_and_targets(values, targets):
    with pytest.raises(RebalanceError):
        portfolio(values, targets)


@pytest.mark.parametrize("value", [-1, np.nan, np.inf])
def test_invalid_cash(value):
    with pytest.raises(RebalanceError):
        allocate_new_money(portfolio([50, 50], [.5, .5]), value, max_trades=1)


@pytest.mark.parametrize("value", [-1, 3, 1.5, True])
def test_invalid_trade_limit(value):
    with pytest.raises(RebalanceError):
        allocate_new_money(portfolio([50, 50], [.5, .5]), 10, max_trades=value)


def test_unproven_optimizer_result_is_never_presented_as_minimum(monkeypatch):
    monkeypatch.setattr("portfolio_app.rebalancing.milp", lambda *a, **kw: SimpleNamespace(status=1, x=None))
    with pytest.raises(RebalanceError, match="did not prove"):
        minimum_trades(portfolio([60, 40], [.5, .5]))


def test_tiny_portfolio_preserves_real_trades_instead_of_rounding_to_zero():
    p = portfolio([6e-8, 4e-8], [.5, .5], tolerance=0)
    plan = minimum_trades(p)
    assert plan.trade_count == 2
    np.testing.assert_allclose(plan.table["Trade (EUR)"], [-1e-8, 1e-8], atol=1e-15)
    np.testing.assert_allclose(plan.table["After (EUR)"], [5e-8, 5e-8], atol=1e-15)


def test_eligible_subset_invests_all_cash_without_changing_other_positions():
    p = portfolio([80., 10., 10.], [.4, .3, .3], tolerance=0)
    plan = allocate_new_money(p, 100, max_trades=3, eligible_position_ids={"row-1", "row-2"})
    assert plan.table["Trade (EUR)"].tolist() == pytest.approx([0, 50, 50], abs=1e-5)
    assert plan.table["After %"].tolist() == pytest.approx([40, 30, 30])
    assert_conserved(plan, 100, buys_only=True)
    single = allocate_new_money(p, 100, max_trades=3, eligible_position_ids={"row-1"})
    assert single.table["Trade (EUR)"].tolist() == pytest.approx([0, 100, 0])
    assert single.deviation_after == pytest.approx(50)
    assert not single.within_bands
    # The full portfolio remains the denominator; targets are not normalized to the subset.
    assert single.table["After %"].tolist() == pytest.approx([40, 55, 5])
    assert_conserved(single, 100, buys_only=True)


def test_tradeoff_frontier_respects_eligible_subset_and_max_trades():
    p = portfolio([80., 10., 10.], [.4, .3, .3], tolerance=0)
    plans = cash_tradeoffs(p, 100, max_trades=3, eligible_position_ids={"row-1", "row-2"})
    assert [plan.trade_count for plan in plans] == [1, 2]
    for plan in plans:
        assert plan.table["Trade (EUR)"].iloc[0] == 0
        assert_conserved(plan, 100, buys_only=True)
    one = allocate_new_money(p, 100, max_trades=1, eligible_position_ids={"row-1", "row-2"})
    assert one.trade_count == 1


def test_eligible_positions_intersect_no_new_and_identify_account_rows():
    p = portfolio([80., 20., 0.], [.4, .4, .2], tolerance=0)
    p.positions["id"] = ["same", "other", "same"]
    p.positions["account"] = ["Synthetic A", "Synthetic A", "Synthetic B"]
    allowed = {"row-1", "row-2"}
    plan = allocate_new_money(p, 100, max_trades=3, eligible_position_ids=allowed, no_new_positions=True)
    assert plan.table["Trade (EUR)"].tolist() == pytest.approx([0, 100, 0])
    plan = allocate_new_money(p, 100, max_trades=3, eligible_position_ids={"row-2"})
    assert plan.table["Trade (EUR)"].tolist() == pytest.approx([0, 0, 100])
    with pytest.raises(RebalanceError, match="No feasible buy"):
        allocate_new_money(p, 100, max_trades=3, eligible_position_ids={"row-2"}, no_new_positions=True)


def test_empty_selection_requires_no_cash_and_invalid_ids_are_rejected():
    p = portfolio([60., 40.], [.5, .5])
    with pytest.raises(RebalanceError, match="select at least one"):
        allocate_new_money(p, 10, max_trades=2, eligible_position_ids=set())
    plan = allocate_new_money(p, 0, max_trades=0, eligible_position_ids=set())
    assert plan.trade_count == 0
    assert_conserved(plan, 100, buys_only=True)
    for invalid in ({"unknown"}, "row-1", [1]):
        with pytest.raises(RebalanceError):
            allocate_new_money(p, 10, max_trades=2, eligible_position_ids=invalid)
    p.positions["position_id"] = ["same", "same"]
    with pytest.raises(RebalanceError, match="unique position IDs"):
        allocate_new_money(p, 10, max_trades=2, eligible_position_ids={"same"})


def test_spread_equal_buys_every_selected_row_and_conserves_cents():
    from portfolio_app.rebalancing import spread_new_money
    p = portfolio([80., 10., 10., 0.], [.4, .3, .2, .1], tolerance=0)
    plan = spread_new_money(p, 100, eligible_position_ids={"row-0", "row-1", "row-2"})
    assert plan.trade_count == 3
    assert plan.table["Trade (EUR)"].tolist() == pytest.approx([33.34, 33.33, 33.33, 0])
    assert_conserved(plan, 100, buys_only=True)
    assert not plan.within_bands  # Distribution is not an optimization claim.


def test_spread_target_weights_apply_to_contribution_not_existing_holdings():
    from portfolio_app.rebalancing import spread_new_money
    p = portfolio([80., 10., 10.], [.4, .3, .3])
    plan = spread_new_money(p, 100, eligible_position_ids={"row-0", "row-2"}, method="target")
    assert plan.table["Trade (EUR)"].tolist() == pytest.approx([57.14, 0, 42.86])
    assert_conserved(plan, 100, buys_only=True)


def test_spread_no_new_positions_and_zero_targets():
    from portfolio_app.rebalancing import spread_new_money
    p = portfolio([80., 20., 0.], [.6, .4, 0])
    ids = {"row-0", "row-1", "row-2"}
    plan = spread_new_money(p, 100, eligible_position_ids=ids, no_new_positions=True)
    assert plan.table["Trade (EUR)"].tolist() == pytest.approx([50, 50, 0])
    plan = spread_new_money(p, 100, eligible_position_ids=ids, method="target")
    assert plan.table["Trade (EUR)"].tolist() == pytest.approx([60, 40, 0])
    with pytest.raises(RebalanceError, match="all zero"):
        spread_new_money(p, 100, eligible_position_ids={"row-2"}, method="target")
    assert spread_new_money(p, 100, eligible_position_ids={"row-2"}).trade_count == 1


def test_spread_rejects_unusable_budgets_and_selections():
    from portfolio_app.rebalancing import spread_new_money
    p = portfolio([80., 20.], [.6, .4])
    for amount in (0, -1, float("nan"), float("inf"), .001, .01):
        with pytest.raises(RebalanceError):
            spread_new_money(p, amount, eligible_position_ids={"row-0", "row-1"})
    with pytest.raises(RebalanceError, match="select at least one"):
        spread_new_money(p, 100, eligible_position_ids=set())


def test_spread_small_contribution_into_large_portfolio_preserves_real_cents():
    from portfolio_app.rebalancing import spread_new_money
    p = portfolio([10_000_000., 10_000_000.], [.5, .5])
    plan = spread_new_money(p, .02, eligible_position_ids={"row-0", "row-1"})
    assert plan.trade_count == 2
    assert plan.table["Trade (EUR)"].tolist() == [.01, .01]
    assert_conserved(plan, 20_000_000, buys_only=True)
