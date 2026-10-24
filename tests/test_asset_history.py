"""Deliberately invented assets and prices; no working portfolio reads."""
from decimal import Decimal as D

import pytest

from portfolio_app.asset_history import AssetMovement, AssetProjection, asset_history, project_asset_movements
from portfolio_app.holdings import DataError
from portfolio_app.transaction_csv import normalize


def row(identity, kind, **kwargs):
    fields = dict(schema_version='2', source_id='invented-asset-ledger', event_id=identity,
        timestamp_utc='2024-01-02T12:00:00Z', event_type=kind, account_id='Wallet',
        asset_id='AUR', quantity='2', fee_amount='0')
    fields.update(kwargs)
    result = normalize(fields)
    assert not result.issues, result.issues
    return result


def projection(*rows):
    return project_asset_movements(rows, ('Wallet', 'Stake'), assets=('AUR',))


def history(p, quantity, **kwargs):
    return asset_history(p, 'AUR', '2024-01-01', D(quantity),
        {'2024-01-01':D(10), '2024-01-02':D(10), '2024-01-03':D(10)}, **kwargs)


def test_internal_stake_unstake_transfers_cancel_but_fees_remain():
    rows = [row('stake','stake',counter_account_id='Stake',quantity='8',fee_amount='1',fee_asset_id='AUR'),
            row('unstake','unstake',account_id='Stake',counter_account_id='Wallet',quantity='8'),
            row('move','transfer',counter_account_id='Stake',quantity='3')]
    p = projection(*rows)
    assert p.ignored_internal == 3 and not p.issues
    assert [(m.quantity,m.kind) for m in p.movements] == [(D(-1),'expense')]
    result = history(p,'10')
    assert result.opening_quantity == 10
    assert result.points[-1]['gain'] == -10
    assert result.points[-1]['return_pct'] == -10


def test_rewards_are_income_and_gain_once_not_contributions():
    reward = row('income','reward',account_id='Stake',valuation_amount='20',valuation_currency='EUR',
                 valuation_quality='estimated',valuation_source='Invented receipt estimate')
    result = history(projection(reward),'10')
    point = result.points[-1]
    assert result.opening_quantity == 10
    assert point['income'] == 20 and point['gain'] == 20
    assert point['capital'] == 100 and point['return_pct'] == 20
    assert not result.partial


def test_reward_price_change_is_included_without_readding_receipt_value():
    p = projection(row('income','reward',valuation_amount='20',valuation_currency='EUR',
        valuation_quality='estimated',valuation_source='Invented receipt estimate'))
    result = asset_history(p,'AUR','2024-01-01',D(10),
        {'2024-01-01':D(10),'2024-01-02':D(10),'2024-01-03':D(15)})
    point=result.points[-1]
    assert point['income']==20 and point['gain']==80 and point['return_pct']==80


def test_external_deposit_is_not_gain_and_outside_fee_not_paid_twice():
    p = projection(row('receipt','transfer',account_id='Exchange',counter_account_id='Wallet',
        fee_amount='',fee_asset_id='AUR'))
    assert not p.issues
    point = history(p,'10').points[-1]
    assert point['capital'] == 120 and point['gain'] == 0 and point['return_pct'] == 0


def test_buy_and_sell_flows_include_fiat_fees_once():
    p = projection(row('buy','buy',gross_amount='20',currency='EUR',fee_amount='1',fee_currency='EUR'),
                   row('sell','sell',timestamp_utc='2024-01-03T12:00:00Z',gross_amount='20',currency='EUR',fee_amount='1',fee_currency='EUR'))
    point=history(p,'10').points[-1]
    assert point['gain']==-2 and point['capital']==102


def test_swap_changes_assets_without_reward_income():
    swap=row('swap','swap',received_asset_id='LUM',received_quantity='4',valuation_amount='20',valuation_currency='EUR',
        valuation_quality='estimated',valuation_source='Invented swap valuation')
    p=project_asset_movements([swap],['Wallet'])
    assert [(m.asset,m.quantity,m.amount) for m in p.movements]==[('AUR',D(-2),D(-20)),('LUM',D(4),D(20))]
    assert all(m.kind=='flow' for m in p.movements)


def test_duplicate_rows_not_additive_and_conflicts_rejected():
    r=row('reward','reward')
    assert projection(r,r)==projection(r)
    with pytest.raises(DataError,match='conflicting'):
        projection(r,row('reward','reward',quantity='3'))


def test_missing_owned_fee_never_becomes_zero():
    p=projection(row('move','stake',counter_account_id='Stake',fee_amount='',fee_asset_id='AUR'))
    assert p.issues
    assert history(p,'10').points[-1]['gain'] is None


def test_same_time_order_is_irrelevant_to_daily_asset_history():
    rows=[row('a','reward'),row('b','fee',quantity='1'),row('c','stake',counter_account_id='Stake',quantity='9')]
    assert history(projection(*rows),'10')==history(projection(*reversed(rows)),'10')


def test_unclassified_units_remain_partial_not_invented_income():
    p=projection(row('dust','inventory_in',flow_classification='unresolved'))
    result=history(p,'10')
    assert result.partial and result.points[-1]['gain'] is None and result.points[-1]['income']==0
    assert any('origin remains unresolved' in r for r in result.reasons)


def test_missing_prices_break_return_without_filling():
    p=projection(row('income','reward'))
    result=asset_history(p,'AUR','2024-01-01',D(10),{'2024-01-01':D(10),'2024-01-03':D(10)})
    assert result.points[1]['value'] is None
    assert result.points[-1]['return_pct'] is None
    assert result.points[-1]['income'] is None
    assert result.points[-1]['gain']==20  # Reward price is unnecessary for total gain.


def test_unknown_trade_amount_not_replaced_by_market_value():
    result=history(projection(row('buy','buy',currency='EUR')),'10')
    assert result.points[-1]['gain'] is None


def test_negative_forward_units_invalidates_gain():
    result=history(projection(row('sell','sell',quantity='20',gross_amount='200',currency='EUR')),'10')
    assert result.opening_quantity==10
    assert result.points[-1]['quantity']==-10
    assert result.points[-1]['gain'] is None


def test_period_selection_uses_market_opening_not_remaining_purchase_cost():
    p=projection(row('income','reward'))
    result=asset_history(p,'AUR','2024-01-01',D(10),{'2024-01-02':D(10),'2024-01-03':D(15)},start='2024-01-02')
    assert result.points[-1]['gain']==60 and result.points[-1]['income']==0


def test_incremental_movements_after_opening_roll_forward():
    p=projection(row('income','reward',timestamp_utc='2024-01-04T12:00:00Z'))
    result=asset_history(p,'AUR','2024-01-03',D(10),{'2024-01-03':D(10),'2024-01-04':D(10)},end='2024-01-04')
    assert result.points[0]['quantity']==10 and result.points[-1]['quantity']==12


def test_invalid_prices_are_rejected():
    with pytest.raises(DataError,match='prices'):
        asset_history(AssetProjection((),(),0),'AUR','2024-01-03',D(10),{'2024-01-03':D('NaN')})


def test_reward_currency_gap_only_blocks_income_breakdown_not_total_gain():
    p=projection(row('income','reward',valuation_amount='22',valuation_currency='USD',
        valuation_quality='estimated',valuation_source='Invented USD estimate'))
    point=history(p,'10').points[-1]
    assert point['gain']==20 and point['return_pct']==20 and point['income'] is None


def test_unknown_fee_in_another_asset_does_not_block_this_assets_gain():
    p=projection(row('income','reward',fee_amount='',fee_asset_id='LUM'))
    assert not p.issues
    assert history(p,'10').points[-1]['gain']==20
