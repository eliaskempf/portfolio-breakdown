"""Cross-consumer contracts using invented holdings and fund weights only."""
from datetime import date

import pandas as pd
import pytest

from portfolio_app.etf import FundSnapshot, expand_etfs
from portfolio_app.exposures import normalize_exposures, project_contributions
from portfolio_app.futures import FuturePosition
from portfolio_app.future_projections import capital_position, capital_contribution, exposure_source, risk_position
from portfolio_app.holdings import DataError
from portfolio_app.portfolio_exposure import calculate_portfolio_exposure
from portfolio_app.stock_exposure import stock_exposure
from portfolio_app.targets import target_exposures


def source():
    return pd.DataFrame([dict(position_id='invented-position', id='invented-fund', name='Invented fund',
        isin='ZZ9999999999', ticker='SYNTH', shares=1., instrument_type='etf', account='Invented account',
        current_value_reporting=100., reporting_currency='EUR', target_allocation=.4, custom_theme='Invented theme',
        cost_basis_reporting=80., unrealized_gain_reporting=20., cost_estimated=False, return_pct=25.,
        purchase_history='private-format synthetic placeholder', cost_basis_details='synthetic placeholder')])


@pytest.mark.parametrize('kind,equity_fund,equity_total,stock_total', [
    ('equity', True, 100., 100.), ('etf', True, 100., 100.),
    ('bond', False, 0., 0.), ('cash', False, 0., 0.),
    ('overnight_rate', False, 0., 0.), ('unknown', False, None, None),
])
def test_whole_fund_weights_and_distinct_company_coverage(kind, equity_fund, equity_total, stock_total):
    valued = source()
    before = valued.copy(deep=True)
    constituent = pd.DataFrame([dict(constituent_id='invented-security', name='Invented security', ticker='',
        isin='ZZ1111111111', weight=.6, instrument_type=kind, exposure_kind='equity' if kind == 'etf' else '')])
    fund = FundSnapshot('invented', 'Invented fund', 'ZZ9999999999', ('SYNTH',), date(2001, 1, 1),
        'Invented source', constituent, equity_fund=equity_fund,
        asset_class='equity' if equity_fund else 'fixed_income' if kind != 'unknown' else '')
    expanded = expand_etfs(normalize_exposures(valued), [fund], valued)
    assert expanded.value.tolist() == pytest.approx([60., 40.])
    assert expanded.source_position_id.eq('invented-position').all()
    assert expanded.custom_theme.eq('Invented theme').all()
    assert not {'cost_basis_reporting', 'unrealized_gain_reporting', 'purchase_history', 'return_pct'} & set(expanded)
    targets = target_exposures(valued, [fund], lookthrough=True)
    assert targets.known.value.sum() == pytest.approx(.4)
    assert targets.missing.value.sum() == 0.
    integrated = calculate_portfolio_exposure(valued, [fund])
    stocks = stock_exposure(valued, [fund])
    assert integrated.net_equity == 100.
    assert integrated.exposure_complete == (equity_total is not None)
    if equity_total is not None:
        assert integrated.net_exposure == equity_total
    assert stocks.stock_value == stock_total
    # A nested equity fund and residual have equity amounts, not known companies.
    assert stocks.companies.Total.sum() == (60. if kind == 'equity' else 0.)
    pd.testing.assert_frame_equal(valued, before)
    pd.testing.assert_frame_equal(fund.constituents, constituent)


def test_explicit_measure_alignment_missing_values_and_owned_frames():
    valued = pd.concat([source(), source().assign(position_id='invented-missing', current_value_reporting=float('nan'))], ignore_index=True)
    values = normalize_exposures(valued)
    assert len(values) == 1
    targets = project_contributions(valued, pd.Series([.3, .7]))
    assert targets.value.sum() == 1. and len(targets) == 2
    targets.loc[0, 'custom_theme'] = 'Changed projection'
    assert valued.loc[0, 'custom_theme'] == 'Invented theme'
    with pytest.raises(DataError, match='align'):
        project_contributions(valued, pd.Series([1., 2.], index=[2, 3]))


@pytest.mark.parametrize('contracts', [-1, 0, 1])
def test_future_projection_reconciliation(contracts):
    future = FuturePosition(contracts=contracts, entry_price=1., mark_price=1.1,
        reference_capital_eur=100., net_funding_eur=20., closed_pnl_eur=5., fees_eur=2.)
    overview, capital = capital_position(future, 'invented'), capital_contribution(future, 'invented')
    risk, exposure = risk_position(future), exposure_source(future, 'invented')
    assert overview['current_value_reporting'] == capital['value'] == risk['current_value_reporting'] == future.capital_value
    assert overview['unrealized_gain_reporting'] == future.lifetime_gain
    assert overview['cost_basis_reporting'] == 100.  # Additional funding is not gain/reference cost.
    assert overview['position_id'] == capital['source_position_id'] == risk['id'] == exposure['source_position_id']
    assert 'notional' not in risk and 'shares' not in exposure
