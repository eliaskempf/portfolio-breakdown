"""Standalone category/account risk from explicitly invented portfolios."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from portfolio_app.analytics_service import load_risk
from portfolio_app.risk import risk_analytics
from test_actual_risk import SyntheticService, actual, future, qualified, row
from test_risk import TODAY, held, history, market


@pytest.mark.parametrize('equity', [100., 200., None, 0., -10.])
def test_subset_beta_and_volatility_do_not_depend_on_outside_capital(equity):
    returns = market()
    result = risk_analytics(held(['stock'], [20.]), {'stock': history(1.5 * returns)}, history(returns),
                            today=TODAY, exposures={}, net_equity=equity, scoped=True)
    assert result.status == 'complete' and result.beta == pytest.approx(1.5)
    assert result.volatility == pytest.approx(np.std(returns, ddof=1) * np.sqrt(52) * 1.5)
    assert result.holdings.loc['stock', 'Weight'] == 1.
    assert result.denominator == 20.
    if equity is not None and equity > 0:
        assert result.portfolio_beta_contribution == pytest.approx(30 / equity)
    else:
        assert result.portfolio_beta_contribution is None


def test_partial_ordinary_subset_normalizes_covered_holdings_but_preserves_portfolio_contribution():
    prices = history(market())
    positions = held(['covered', 'new', 'cash', 'unpriced'], [20., 40., 10., np.nan],
                     ['equity', 'equity', 'cash', 'equity'])
    result = risk_analytics(positions, {'covered': prices, 'new': prices.tail(8)}, prices,
                           today=TODAY, exposures={}, net_equity=100., scoped=True)
    assert result.status == 'partial' and set(result.excluded) == {'new', 'unpriced'}
    assert result.beta == pytest.approx(2 / 3)
    assert result.portfolio_beta_contribution == pytest.approx(.2)
    assert result.holdings.Weight.sum() == pytest.approx(1.)
    assert result.holdings['Volatility contribution'].sum() == pytest.approx(result.volatility)
    assert 'renormalized within the selected' in result.note


def test_selected_debt_reduces_subset_capital_without_allocating_unselected_debt():
    prices = history(market())
    positions = held(['stock', 'loan'], [100., -20.], ['equity', 'liability'])
    result = risk_analytics(positions, {'stock': prices}, prices, today=TODAY,
                           exposures={}, net_equity=500., scoped=True)
    assert result.beta == pytest.approx(1.25)
    assert result.portfolio_beta_contribution == pytest.approx(.2)


@pytest.mark.parametrize('notional', [200., -200.])
def test_funded_future_subset_uses_selected_capital_and_counts_unsettled_claim_once(tmp_path, notional):
    prices = history(market())
    positions = actual([row('cash', 'cash', 90., 'EUR'),
                        future(value=10., notional=notional, currency='EUR', product='FMWN')])
    result, _ = load_risk(positions, tmp_path, False, 'BENCH', 3,
        service=SyntheticService(prices), today=TODAY, portfolio_result=SimpleNamespace(net_equity=500.),
        qualified_histories=qualified(prices, 'EUR'), scoped=True)
    assert result.status == 'complete' and result.denominator == 100.
    assert result.beta == pytest.approx(notional / 100.)
    assert result.portfolio_beta_contribution == pytest.approx(notional / 500.)
    assert result.holdings['Beta contribution'].sum() == pytest.approx(result.beta)
    assert result.holdings['Volatility contribution'].sum() == pytest.approx(result.volatility)


@pytest.mark.parametrize('claim', [0., 10., -10.])
def test_bare_future_claim_is_not_a_capital_base_but_portfolio_contribution_remains(tmp_path, claim):
    prices = history(market())
    positions = actual([future(value=claim, currency='EUR', product='FMWN')])
    result, _ = load_risk(positions, tmp_path, False, 'BENCH', 3,
        service=SyntheticService(prices), today=TODAY, portfolio_result=SimpleNamespace(net_equity=500.),
        qualified_histories=qualified(prices, 'EUR'), scoped=True)
    assert result.status == 'unavailable'
    assert result.beta is result.volatility is result.correlation is result.denominator is None
    assert result.portfolio_beta_contribution == pytest.approx(.4)
    assert result.holdings.Weight.isna().all()
    assert 'capital backing the future' in result.note


def test_missing_future_history_keeps_funded_subset_capital(tmp_path):
    prices = history(market())
    positions = actual([dict(row('stock', 'equity', 80., 'EUR'), ticker='STOCK'),
                        row('cash', 'cash', 20., 'EUR'), future(currency='EUR', product='FESX')])
    result, _ = load_risk(positions, tmp_path, False, 'BENCH', 3,
        service=SyntheticService(prices, proxy=prices), today=TODAY,
        portfolio_result=SimpleNamespace(net_equity=500.), scoped=True)
    assert result.status == 'partial' and 'future:synthetic' in result.excluded
    assert result.denominator == 100. and result.beta == pytest.approx(.8)
    assert result.portfolio_beta_contribution == pytest.approx(.16)


def test_sandbox_funded_future_can_supply_its_own_subset_capital(tmp_path):
    from portfolio_app.futures import FuturePosition
    positions = held([], [])
    positions['reporting_currency'] = pd.Series(dtype=str)
    position = FuturePosition(entry_price=1., mark_price=1., reference_capital_eur=20.)
    result, _ = load_risk(positions, tmp_path, True, 'IUSQ.DE', 3, future=position,
                         portfolio_result=SimpleNamespace(net_equity=500.), scoped=True)
    assert result.status == 'complete' and result.denominator == 20.
    assert result.holdings.loc['future:overlay', 'Weight'] == pytest.approx(position.notional / 20.)
    assert result.portfolio_beta_contribution == pytest.approx(result.beta * 20. / 500.)


def test_ordinary_snapshot_retains_standalone_weights_with_portfolio_contribution(tmp_path):
    prices = history(market())
    result, _ = load_risk(held(['stock'], [20.]), tmp_path, False, 'BENCH', 3,
                         service=SyntheticService(prices, proxy=prices), today=TODAY,
                         scoped=True, portfolio_net_equity=100.)
    assert not result.integrated
    assert result.beta == pytest.approx(1.)
    assert result.portfolio_beta_contribution == pytest.approx(.2)
