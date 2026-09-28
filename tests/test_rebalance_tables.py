"""Invented plans exercise presentation without personal data or live prices."""
import pandas as pd
import pytest

from portfolio_app.allocation import Allocation, Bucket, macro_table
from portfolio_app.rebalance_tables import allocation_table, category_budgets, portfolio_impact, position_labels, suggested_trades


@pytest.fixture
def positions():
    return pd.DataFrame([
        dict(position_id='a', name='Invented Asset', account='First', bucket_id='left', current_value_eur=120., target_allocation=.4),
        dict(position_id='b', name='Invented Asset', account='Second', bucket_id='right', current_value_eur=80., target_allocation=.4),
        dict(position_id='c', name='Invented Reserve', account='', bucket_id='reserve', current_value_eur=50., target_allocation=.2),
    ])


@pytest.fixture
def config():
    return Allocation((Bucket('root', 'Growth', target=.8), Bucket('left', 'Left', 'root', .5),
                       Bucket('right', 'Right', 'root', .5), Bucket('reserve', 'Reserve', target=.2),
                       Bucket('empty', 'Future', target=0.)))


def test_trade_table_hides_identities_retains_holds_and_uses_whole_scope(positions, config):
    trades = pd.DataFrame({'position_id': ['b', 'a'], 'Trade (EUR)': [30., 0.], 'After %': [100., 0.]})
    table = allocation_table(positions, trades, new_money=50., config=config)
    assert not {'position_id', 'id', 'ticker', 'portfolio'} & set(table)
    assert table.Action.tolist() == ['Hold', 'Buy', 'Hold']
    assert table['After (EUR)'].sum() + 20 == 300
    assert table['After %'].sum() == pytest.approx(100 * 280 / 300)
    assert table['After %'].iloc[1] == pytest.approx(100 * 110 / 300)
    assert table.Category.tolist() == ['Growth › Left', 'Growth › Right', 'Reserve']
    buys = suggested_trades(table)
    assert len(buys) == 1
    assert buys.Account.tolist() == ['Second']
    assert 'Target %' not in buys
    assert position_labels(positions, config)['b'] == 'Invented Asset · Second · Growth › Right'


def test_budgets_distinguish_reserved_invested_and_cash(positions, config):
    budgets = pd.DataFrame({'Bucket ID': ['left', 'right', 'reserve'], 'Budget (EUR)': [10., 30., 0.]})
    trades = pd.DataFrame({'position_id': ['a', 'b'], 'Trade (EUR)': [0., 25.]})
    result = category_budgets(budgets, trades, positions, config)
    assert 'Bucket ID' not in result
    assert result['Reserved (EUR)'].sum() == 40
    assert result['Invested (EUR)'].sum() == 25
    assert result['Unallocated (EUR)'].sum() == 15
    empty = category_budgets(budgets, trades.iloc[:0], positions, config)
    assert empty['Invested (EUR)'].sum() == 0


def test_impact_only_siblings_keeps_planned_capacity_and_cash(positions, config):
    after = positions.copy()
    after.loc[1, 'current_value_eur'] += 30
    impact = portfolio_impact(positions, after, config, extra_cash=20).set_index('Category')
    assert set(impact.index) == {'Growth', 'Reserve', 'Future', 'Unallocated contribution'}
    assert impact['After %'].sum() == pytest.approx(100)
    assert impact.loc['Growth', 'After %'] == pytest.approx(230 / 300 * 100)
    children = portfolio_impact(positions, after, config, extra_cash=20, parent='root')
    assert children.Category.tolist() == ['Left', 'Right']
    assert children['After %'].sum() == pytest.approx(100)
    assert children['Target %'].tolist() == [50, 50]
    assert 'Unassigned' not in macro_table(positions, config).Bucket.tolist()


@pytest.mark.parametrize('value', [0., float('nan')])
def test_real_unassigned_rows_are_visible_even_with_no_known_value(positions, config, value):
    positions.loc[2, ['bucket_id', 'current_value_eur']] = ['', value]
    result = portfolio_impact(positions, positions, config)
    assert 'Unassigned' in result.Category.tolist()
    assert 'Unassigned' in macro_table(positions, config).Bucket.tolist()
    if pd.isna(value):
        assert result['After %'].isna().all()


def test_trades_preserve_sells_and_sort_by_amount(positions):
    trades = pd.DataFrame({'position_id': ['a', 'b'], 'Trade (EUR)': [-40., 40.]})
    table = allocation_table(positions, trades, new_money=0.)
    result = suggested_trades(table)
    assert result.Action.tolist() == ['Sell', 'Buy']
    assert result['Trade (EUR)'].sum() == 0
    assert table['After %'].sum() == pytest.approx(100)
