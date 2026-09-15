"""Per-plan caps use synthetic values and independently enumerated cent buys."""

from itertools import product

import numpy as np
import pytest

from portfolio_app.rebalancing import RebalanceError, balanced_cash_tradeoffs
from test_rebalancing import portfolio


def test_cap_redirects_money_while_saved_targets_remain_unchanged():
    p = portfolio([80., 10., 10.], [.4, .3, .3], tolerance=0)
    original = p.positions.copy(deep=True)
    plan = balanced_cash_tradeoffs(p, 100, eligible_position_ids={'row-0', 'row-1', 'row-2'},
                                    minimum_purchase=5, buy_all=True, max_allocations={'row-1': .2})[-1]
    assert plan.table['Trade (EUR)'].tolist() == [10, 30, 60]
    assert plan.table['After %'].tolist() == [45, 20, 35]
    assert plan.unallocated_cash == 0
    assert p.positions.equals(original)
    assert plan.table['Target %'].tolist() == [40, 30, 30]
    assert plan.table['Max allocation %'].iloc[1] == 20


def test_all_caps_leave_cash_in_final_denominator():
    p = portfolio([80., 10., 10.], [.4, .3, .3])
    caps = {'row-0': .45, 'row-1': .15, 'row-2': .15}
    plan = balanced_cash_tradeoffs(p, 100, eligible_position_ids=set(caps), minimum_purchase=5,
                                    buy_all=True, max_allocations=caps)[-1]
    assert plan.table['Trade (EUR)'].tolist() == [10, 20, 20]
    assert plan.new_money == 100
    assert plan.unallocated_cash == 50
    assert plan.table['After %'].tolist() == [45, 15, 15]
    assert plan.table['After (EUR)'].sum() + plan.unallocated_cash == 200
    assert plan.table['Trade (EUR)'].sum() + plan.unallocated_cash == 100


def test_already_above_cap_is_never_sold_and_all_blocked_returns_cash():
    p = portfolio([80., 10., 10.], [.4, .3, .3])
    ids = {'row-0', 'row-1', 'row-2'}
    plan = balanced_cash_tradeoffs(p, 100, eligible_position_ids=ids, minimum_purchase=5,
                                    max_allocations={'row-0': .1})[-1]
    assert plan.table['Trade (EUR)'].tolist() == [0, 50, 50]
    blocked = balanced_cash_tradeoffs(p, 100, eligible_position_ids=ids, minimum_purchase=5,
                                       max_allocations={key: 0. for key in ids})[-1]
    assert blocked.trade_count == 0
    assert blocked.unallocated_cash == 100
    assert blocked.table['After (EUR)'].tolist() == [80, 10, 10]
    with pytest.raises(RebalanceError, match='less room than the minimum'):
        balanced_cash_tradeoffs(p, 100, eligible_position_ids=ids, buy_all=True,
                                minimum_purchase=5, max_allocations={'row-0': .1})


@pytest.mark.parametrize('upper', [(2, 6, 3), (1, 2, 1), (0, 3, 7), (7, 1, 7)])
@pytest.mark.parametrize('limit', [1, 2, 3])
@pytest.mark.parametrize('minimum', [1, 2])
def test_capped_trade_limits_match_exhaustive_integer_optimum(upper, limit, minimum):
    p = portfolio([.10, .05, .02], [.2, .5, .3], tolerance=0)
    budget = 8
    final = p.total + budget / 100
    caps = {f'row-{i}': (p.values[i] + upper[i] / 100) / final for i in range(3)}
    plan = balanced_cash_tradeoffs(p, budget / 100, eligible_position_ids=set(caps),
                                    minimum_purchase=minimum/100, max_trades=limit, max_allocations=caps)[-1]
    feasible = []
    for amounts in product(range(budget+1), repeat=3):
        buys = np.array(amounts)
        if buys.sum() > budget or np.count_nonzero(buys) > limit or (buys > upper).any():
            continue
        if ((buys > 0) & (buys < minimum)).any():
            continue
        error = np.mean(((p.values + buys / 100) / final * 100 - p.targets * 100)**2)
        feasible.append((-int(buys.sum()), error))
    invested, error = min(feasible)
    assert plan.table['Trade (EUR)'].sum() == pytest.approx(-invested/100)
    assert plan.target_rms**2 == pytest.approx(error, abs=1e-8)
    assert plan.unallocated_cash == pytest.approx((budget+invested)/100)


def test_caps_validate_selected_ids_fractions_and_subcent_capacity():
    p = portfolio([50., 50.], [.5, .5])
    for caps in ({'unknown': .5}, {'row-0': -1}, {'row-0': 1.1}, {'row-0': float('nan')},
                 {'row-0': True}, {'row-0': '0.2'}, []):
        with pytest.raises(RebalanceError):
            balanced_cash_tradeoffs(p, 10, eligible_position_ids={'row-0'}, max_allocations=caps)
    cap = (50 + .009) / 110
    plan = balanced_cash_tradeoffs(p, 10, eligible_position_ids={'row-0'}, max_allocations={'row-0': cap})[-1]
    assert plan.trade_count == 0
    assert plan.unallocated_cash == 10


def test_capped_solver_timeout_does_not_present_an_unproven_plan(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('portfolio_app.capped_contributions.milp', lambda *a, **kw: SimpleNamespace(status=1, x=None))
    p = portfolio([80., 10., 10.], [.4, .3, .3])
    with pytest.raises(RebalanceError, match='did not prove'):
        balanced_cash_tradeoffs(p, 100, eligible_position_ids={'row-0', 'row-1', 'row-2'},
                                minimum_purchase=5, max_allocations={'row-1': .2})
