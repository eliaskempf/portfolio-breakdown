"""Invented accounting examples; no working portfolio files or network."""
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from portfolio_app.allocation import Allocation, Bucket
from portfolio_app.etf import FundSnapshot
from portfolio_app.exposure_targets import ExposureTargets, exposure_gaps, load_exposure_targets, save_exposure_targets
from portfolio_app.futures import FuturePosition
from portfolio_app.holdings import DataError
from portfolio_app.leverage_settings import FundFactor, LeverageSettings, MarginLiability, load_leverage_settings, save_leverage_settings
from portfolio_app.portfolio_exposure import calculate_portfolio_exposure, strategy_exposure

CONFIG = Allocation((Bucket('core', 'Core'), Bucket('stocks', 'Stocks'), Bucket('cash', 'Cash')))


def invented():
    return pd.DataFrame([dict(position_id=pid, id=pid, name='Invented ' + pid, instrument_type=kind, exposure_kind=declared,
                              current_value_reporting=value, reporting_currency='EUR', shares=1., bucket_id=bucket, isin='', ticker='', account='Synthetic')
        for pid, kind, declared, value, bucket in [('fund', 'etf', 'equity', 60000., 'core'),
        ('stock', 'equity', 'equity', 30000., 'stocks'), ('cash', 'cash', 'non_equity', 10000., 'cash')]])


def calculate(frame=None, **kwargs):
    return calculate_portfolio_exposure(invented() if frame is None else frame, allocation=CONFIG, **kwargs)


@pytest.mark.parametrize('contracts,net,core', [(1, 1.4, 110000), (-1, .4, 10000)])
def test_long_short_gross_before_offsets_fixed_filter_denominator(contracts, net, core):
    result = calculate(future=FuturePosition(contracts=contracts, bucket_id='core'))
    assert result.net_equity == 100000
    assert result.net_multiple == pytest.approx(net)
    assert result.gross_multiple == pytest.approx(1.4)
    table = strategy_exposure(result, CONFIG).set_index('ID')
    assert table.loc['core', 'Exposure (EUR)'] == core
    filtered = strategy_exposure(result, CONFIG, 'core')
    assert filtered['% of net equity'].sum() == pytest.approx(core/1000)
    assert result.exposures.value.sum() == 100000


def test_cash_backed_future_and_collateral_do_not_create_wealth():
    frame = invented().iloc[[2]].copy()
    frame.current_value_reporting = 100000.
    p = FuturePosition(contracts=2, collateral_budget=20000.)
    result = calculate(frame, future=p)
    assert result.net_multiple == result.gross_multiple == 1.
    assert calculate(frame, future=replace(p, collateral_budget=0)).net_equity == result.net_equity


def test_settlement_once_and_unknown_price_fx_or_debt():
    result = calculate(future=FuturePosition(mark_price=510))
    assert result.settlement_adjustment == 1000
    assert result.net_equity == 101000
    assert result.capital.value.sum() == 101000
    for value in [float('nan'), float('inf')]:
        frame = invented()
        frame.loc[0, 'current_value_reporting'] = value
        result = calculate(frame, future=FuturePosition())
        assert result.net_equity is result.gross_multiple is result.net_multiple is None
    result = calculate(liabilities=(MarginLiability('loan', 'Invented loan', None),))
    assert result.net_equity is result.net_multiple is None


def test_margin_debt_and_fund_factor_with_future():
    frame = invented().iloc[[1]].copy()
    frame.current_value_reporting = 130000.
    result = calculate(frame, liabilities=(MarginLiability('loan', 'Invented loan', 30000.),))
    assert result.net_equity == 100000
    assert result.net_multiple == 1.3
    frame = invented().iloc[[0]].copy()
    frame.current_value_reporting = 10000.
    adapter = FundFactor('fund', 2., 'Invented documented daily factor', '2026-01-01')
    result = calculate(frame, fund_factors=(adapter,))
    assert result.net_equity == 10000 and result.net_exposure == 20000
    result = calculate(frame, fund_factors=(replace(adapter, factor=-2),),
                       liabilities=(MarginLiability('loan', 'Invented loan', 2000.),), future=FuturePosition())
    assert result.net_equity == 8000 and result.net_exposure == 30000 and result.gross_exposure == 70000
    result = calculate(frame, fund_factors=(FundFactor('fund', None),))
    assert result.net_multiple is None and not result.exposure_complete
    assert 'Unknown embedded leverage' in set(result.exposures.status)


@pytest.mark.parametrize('balance', [100000., 120000.])
def test_nonpositive_equity_suppresses_ratios(balance):
    result = calculate(liabilities=(MarginLiability('loan', 'Invented loan', balance),))
    assert result.net_multiple is result.gross_multiple is result.futures_multiple is None


def test_mixed_fund_residual_and_parent_not_double_counted():
    frame = invented().iloc[[0]].copy()
    frame.loc[:, 'ticker'] = 'SYNTH'
    frame.loc[:, 'exposure_kind'] = 'unknown'
    constituents = pd.DataFrame([dict(constituent_id='company', name='Invented company', ticker='', isin='', weight=.6, instrument_type='equity'),
                                 dict(constituent_id='cash', name='Invented cash', ticker='', isin='', weight=.4, instrument_type='cash')])
    fund = FundSnapshot('f', 'Invented mixed fund', 'ZZ0000000000', ('SYNTH',), date(2026, 1, 1), 'Synthetic', constituents)
    result = calculate(frame, funds=[fund])
    assert result.net_exposure == 36000 and result.net_multiple == .6
    assert result.exposures.value.sum() == result.net_equity == 60000
    partial = replace(fund, constituents=constituents.iloc[[0]])
    result = calculate(frame, funds=[partial])
    assert not result.exposure_complete and result.net_multiple is None
    result = calculate(frame, funds=[replace(partial, equity_fund=True)])
    assert result.net_exposure == 60000 and result.exposure_complete
    assert result.exposures.source_type.eq('etf_other').sum() == 1
    # A documented daily factor replaces expansion, without a second parent.
    result = calculate(frame, funds=[fund], fund_factors=(FundFactor('fund', 2., 'Synthetic', '2026-01-01'),))
    assert len(result.exposures) == 1 and result.net_exposure == 120000


def test_unknown_composition_and_stale_bucket_stay_visible():
    frame = invented()
    frame.loc[0, 'exposure_kind'] = 'unknown'
    result = calculate(frame, future=FuturePosition(bucket_id='deleted'))
    assert result.net_multiple is None
    assert result.exposures.loc[result.exposures.source_type.eq('future'), 'bucket_id'].item() == ''
    assert result.exposures.loc[result.exposures.source_type.eq('future'), 'exposure_eur'].item() == 50000


def test_distinct_targets_policies_and_revision_storage(tmp_path):
    result = calculate(future=FuturePosition(bucket_id='core'))
    composition = ExposureTargets(1.2, 'composition', {'core': .7, 'stocks': .3, 'cash': 0.})
    gaps = exposure_gaps(result, composition, CONFIG).set_index('Bucket ID')
    assert gaps.loc['core', 'Desired exposure (EUR)'] == 84000
    assert gaps.loc['stocks', 'Desired (% equity)'] == 36
    assert gaps.loc['core', 'Gap (pp)'] == -26
    overlay = ExposureTargets(1.2, 'overlay_only', {}, 'core')
    gaps = exposure_gaps(result, overlay, CONFIG).set_index('Bucket ID')
    assert gaps.loc['stocks', 'Desired exposure (EUR)'] == 30000
    assert gaps.loc['core', 'Desired exposure (EUR)'] == 90000
    assert load_exposure_targets(tmp_path, CONFIG) == (None, None)
    save_exposure_targets(tmp_path, composition, CONFIG, None)
    saved, revision = load_exposure_targets(tmp_path, CONFIG)
    assert saved == composition
    save_exposure_targets(tmp_path, overlay, CONFIG, revision)
    with pytest.raises(DataError, match='changed'):
        save_exposure_targets(tmp_path, composition, CONFIG, revision)
    assert list((tmp_path / '.backups').iterdir())
    assert not (tmp_path / 'allocation.yaml').exists()
    with pytest.raises(DataError, match='negative category'):
        exposure_gaps(result, replace(overlay, multiple=.1), CONFIG)


@pytest.mark.parametrize('target', [ExposureTargets(1.2, '', {}), ExposureTargets(1.2, 'composition', {'core': 1.1, 'stocks': -.1}),
    ExposureTargets(1.2, 'composition', {'core': .7}), ExposureTargets(1.2, 'overlay_only', {}, 'deleted')])
def test_invalid_targets_rejected(tmp_path, target):
    with pytest.raises(DataError):
        save_exposure_targets(tmp_path, target, CONFIG, None)


def test_adapter_save_reload_and_validation(tmp_path):
    settings = LeverageSettings((MarginLiability('loan', 'Invented loan', 30000.),), (FundFactor('fund', -2., 'Invented daily inverse factor', '2026-01-01'),))
    save_leverage_settings(tmp_path, settings, None)
    loaded, revision = load_leverage_settings(tmp_path)
    assert loaded == settings
    save_leverage_settings(tmp_path, LeverageSettings(), revision)
    with pytest.raises(DataError):
        save_leverage_settings(tmp_path, settings, revision)
    with pytest.raises(DataError):
        FundFactor('fund', 2.)
    with pytest.raises(DataError):
        MarginLiability('loan', 'Invented loan', -1.)


def test_explicit_fund_factor_overrides_old_exposure_classification():
    frame = invented().iloc[[0]].copy()
    frame['exposure_kind'] = 'non_equity'
    adapter = FundFactor('fund', 2., 'Synthetic documented equity factor', '2026-01-01')
    assert calculate(frame, fund_factors=(adapter,)).net_multiple == 2.
    frame['instrument_type'] = 'cash'
    assert calculate(frame, fund_factors=(adapter,)).net_multiple is None


def test_hierarchical_rollups_preserve_ownership_and_gross():
    config = Allocation((Bucket('equities', 'Equities'), Bucket('core', 'Core', 'equities'), Bucket('stocks', 'Stocks', 'equities'), Bucket('cash', 'Cash')))
    result = calculate_portfolio_exposure(invented(), allocation=config, future=FuturePosition(contracts=-1, bucket_id='core'))
    top = strategy_exposure(result, config).set_index('ID')
    children = strategy_exposure(result, config, 'equities')
    assert top.loc['equities', 'Exposure (EUR)'] == children['Exposure (EUR)'].sum() == 40000.
    assert top.loc['equities', 'Gross exposure (EUR)'] == children['Gross exposure (EUR)'].sum() == 140000.


def test_strategy_leaf_labels_fund_total_with_source_name_not_first_constituent():
    frame = invented().iloc[[0]].copy()
    frame['ticker'] = 'SYNTH'
    fund = FundSnapshot('f', 'Invented fund', 'ZZ0000000000', ('SYNTH',), date(2026, 1, 1), 'Synthetic',
        pd.DataFrame([dict(constituent_id='company', name='Invented constituent', ticker='', isin='', weight=1.)]), equity_fund=True)
    result = calculate(frame, funds=[fund])
    leaf = strategy_exposure(result, CONFIG, 'core')
    assert leaf.Category.item() == 'Invented fund'
    assert leaf['Exposure (EUR)'].item() == 60000.


def test_price_scenario_uses_recorded_equity_and_fixed_debt():
    from portfolio_app.futures import futures_scenarios, summarize_future
    frame = invented()
    position = FuturePosition(mark_price=510.)
    result = calculate(frame, future=position, liabilities=(MarginLiability('loan', 'Invented loan', 30000.),))
    summary = summarize_future(frame, position)
    flat = futures_scenarios(summary, position, [0.], portfolio_result=result).iloc[0]
    assert flat['With futures (EUR)'] == result.net_equity == 71000.
    assert flat['Without futures (EUR)'] == 70000.
    shock = futures_scenarios(summary, position, [-.1], portfolio_result=result).iloc[0]
    assert shock['With futures (EUR)'] == 65900.
    assert shock['Change from current equity (%)'] == pytest.approx(-5100/71000*100)
    missing = calculate(frame, future=position, liabilities=(MarginLiability('loan', 'Invented loan', None),))
    assert pd.isna(futures_scenarios(summary, position, [0.], portfolio_result=missing).iloc[0]['With futures (EUR)'])


@pytest.mark.parametrize('nested_exposure', ['equity', 'unknown'])
def test_nested_equity_fund_coverage_is_separate_from_company_coverage(nested_exposure):
    frame = invented().iloc[[0]].copy()
    frame['ticker'] = 'SYNTH'
    constituents = pd.DataFrame([
        dict(constituent_id='company', name='Invented company', ticker='', isin='', weight=.55, instrument_type='equity'),
        dict(constituent_id='nested', name='Invented nested fund', ticker='', isin='', weight=.25,
             instrument_type='etf', exposure_kind=nested_exposure),
        dict(constituent_id='cash', name='Invented cash', ticker='', isin='', weight=.20, instrument_type='cash'),
    ])
    fund = FundSnapshot('parent', 'Invented parent fund', 'ZZ0000000000', ('SYNTH',),
                        date(2026, 1, 1), 'Synthetic', constituents, equity_fund=True)
    result = calculate(frame, funds=[fund], future=FuturePosition(contracts=-1, bucket_id='core'))
    nested = result.exposures.loc[result.exposures.asset_id.eq('nested' if nested_exposure == 'equity' else 'etf-other:parent')].iloc[0]
    assert result.net_equity == 60000.
    assert result.exposures.value.sum() == 60000.
    assert len(result.exposures) == 4  # Constituents replace the parent, plus the overlay.
    assert result.exposure_complete
    assert nested.exposure_eur == 15000.
    assert result.net_multiple == pytest.approx(-2000/60000)
    assert result.gross_multiple == pytest.approx(98000/60000)
    targets = ExposureTargets(1.2, 'composition', {'core': 1.})
    assert not exposure_gaps(result, targets, CONFIG).empty
    if nested_exposure == 'equity':
        assert nested.status == 'Unresolved equity fund'
    else:
        assert nested.status == 'Unresolved equity allocation; fund-value basis'
        assert 'Invented nested fund' in nested.method


def test_total_market_includes_gold_crypto_but_equity_targets_do_not():
    frame = invented()
    frame.loc[0, ['instrument_type', 'exposure_kind']] = ['physical', 'non_equity']
    frame.loc[1, ['instrument_type', 'exposure_kind']] = ['crypto', 'non_equity']
    result = calculate(frame, future=FuturePosition(contracts=-1, bucket_id='core'))
    assert result.net_equity == 100000.
    assert result.net_multiple == -.5
    assert result.gross_multiple == .5
    assert result.net_market_multiple == .4
    assert result.gross_market_multiple == 1.4
    assert result.market_amount(bucket_ids={'cash'}) == 0.
    assert result.ratio(result.market_amount(bucket_ids={'core'})) == .1
    assert result.ratio(result.market_amount(gross=True, bucket_ids={'core'})) == 1.1
    table = strategy_exposure(result, CONFIG, measure='market_exposure_eur').set_index('ID')
    assert table.loc['core', 'Exposure (EUR)'] == 10000.
    assert table.loc['core', 'Gross exposure (EUR)'] == 110000.
    equity = strategy_exposure(result, CONFIG).set_index('ID')
    assert equity.loc['stocks', 'Exposure (EUR)'] == 0.


def test_combined_position_rows_keep_future_notional_out_of_capital_and_returns():
    from portfolio_app.position_list import overview_position_rows
    frame = invented()
    future = FuturePosition(contracts=-1, mark_price=510., bucket_id='core')
    result = calculate(frame, future=future)
    rows = overview_position_rows(frame, CONFIG, future=future)
    derivative = rows[-1]
    assert derivative['id'] == 'future:overlay'
    assert derivative['value'] is derivative['allocation'] is derivative['returnPct'] is None
    assert derivative['exposure'] == -51000.
    assert derivative['name'] == 'FMWN future'
    assert dict((x['label'], x['value']) for x in derivative['details'])['Notional'] == '€-51,000'
    assert 'subtitle' not in derivative
    assert 'details' not in rows[0]
    assert derivative['gainReporting'] == -1000.
    assert sum(row['value'] or 0 for row in rows) == 100000.
    assert result.net_equity == 99000.
    assert len(overview_position_rows(frame, CONFIG, future=replace(future, contracts=0))) == 3


def test_total_market_preserves_unknown_leverage_coverage():
    result = calculate(fund_factors=(FundFactor('fund', None),))
    assert result.net_market_multiple is result.gross_market_multiple is None


@pytest.mark.parametrize('declared,metadata', [('equity', False), ('unknown', True)])
def test_equity_fund_value_basis_survives_unclassified_security(declared, metadata):
    frame = invented().iloc[[0]].copy()
    frame['ticker'] = 'SYNTH'
    frame['exposure_kind'] = declared
    fund = FundSnapshot('ordinary', 'Invented ordinary equity ETF', 'ZZ0000000000', ('SYNTH',),
        date(2026, 1, 1), 'Synthetic', pd.DataFrame([
            dict(constituent_id='stock', name='Invented company', ticker='', isin='', weight=.89, instrument_type='equity'),
            dict(constituent_id='right', name='Invented subscription right', ticker='', isin='', weight=.01, instrument_type='unknown'),
            dict(constituent_id='cash', name='Invented cash', ticker='', isin='', weight=.10, instrument_type='cash'),
        ]), equity_fund=metadata)
    result = calculate(frame, funds=[fund])
    assert result.exposure_complete
    assert result.net_equity == 60000.
    assert result.market_amount() == pytest.approx(54000.)
    assert result.net_exposure == pytest.approx(54000.)
    other = result.exposures.loc[result.exposures.asset_id.eq('etf-other:ordinary')].iloc[0]
    assert other.market_exposure_reporting == 600.
    assert other.source_type == 'etf_other'
    assert other.status.startswith('Unresolved')
    assert 'Invented subscription right' in other.method
    assert 'right' not in set(result.exposures.asset_id)
    assert result.exposures.loc[result.exposures.asset_id.eq('cash'), 'market_exposure_reporting'].item() == 0.
    assert result.exposures.value.sum() == 60000.
    # Explicit leverage factors, including unknown leverage, take precedence.
    adapted = calculate(frame, funds=[fund], fund_factors=(FundFactor('fund', 2., 'Synthetic', '2026-01-01'),))
    assert adapted.market_amount() == 120000.
    unknown = calculate(frame, funds=[fund], fund_factors=(FundFactor('fund', None),))
    assert not unknown.exposure_complete
    assert unknown.exposures.market_exposure_reporting.isna().all()
    assert unknown.net_market_multiple is None
