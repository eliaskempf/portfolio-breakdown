"""Synthetic reporting-schema boundaries; EUR-only leverage never relabels FX."""
from dataclasses import replace
from datetime import datetime, timezone
import json

import pandas as pd
import pytest

from portfolio_app.allocation import Allocation, Bucket
from portfolio_app.exposure_planning import plan_exposure
from portfolio_app.exposure_presentation import exposure_positions
from portfolio_app.exposure_targets import ExposureTargets, exposure_gaps
from portfolio_app.futures import FuturePosition, funded_strategy_positions, summarize_future
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.performance import summarize_performance
from portfolio_app.portfolio import prepare_portfolio
from portfolio_app.portfolio_exposure import calculate_portfolio_exposure
from portfolio_app.prices import PriceService, StaticProvider


CONFIG = Allocation((Bucket('core', 'Invented core'),))
FUTURE = FuturePosition(contracts=1, entry_price=.4, mark_price=.4,
                        reference_capital_eur=10., bucket_id='core')
TARGET = ExposureTargets(1.3, 'composition', {'core': 1.}, version=2, universe='market')


def invented():
    return pd.DataFrame([dict(position_id='stock', id='stock', name='Invented stock',
        shares=9., current_value_reporting=90., reporting_currency='EUR',
        instrument_type='equity', exposure_kind='equity', bucket_id='core',
        ticker='', isin='', account='Synthetic', within_bucket_target=1.)])


def operations(frame):
    result = calculate_portfolio_exposure(invented(), future=FUTURE, allocation=CONFIG)
    return [lambda: calculate_portfolio_exposure(frame, future=FUTURE, allocation=CONFIG),
            lambda: summarize_future(frame, FUTURE),
            lambda: funded_strategy_positions(frame, FUTURE, CONFIG),
            lambda: exposure_positions(frame, result, FUTURE),
            lambda: plan_exposure(result, TARGET, CONFIG, frame, permitted=['stock'])]


@pytest.mark.parametrize('currency', ['USD', 'GBP'])
def test_non_eur_reporting_rejected_at_every_core_entrypoint(currency):
    frame = invented().assign(reporting_currency=currency)
    for operation in operations(frame):
        with pytest.raises(DataError, match='EUR reporting currency'):
            operation()


@pytest.mark.parametrize('metadata', ['absent', 'mixed', 'conflicting_attrs', 'missing_row'])
def test_missing_or_inconsistent_currency_metadata_is_not_assumed_eur(metadata):
    frame = invented()
    if metadata == 'absent':
        frame = frame.drop(columns='reporting_currency')
    elif metadata == 'mixed':
        frame = pd.concat([frame, frame.assign(position_id='other', reporting_currency='USD')])
    elif metadata == 'conflicting_attrs':
        frame.attrs['reporting_currency'] = 'GBP'
    else:
        frame.loc[0, 'reporting_currency'] = None
    for operation in operations(frame):
        with pytest.raises(DataError, match='EUR reporting currency'):
            operation()


def test_reporting_schema_keeps_genuine_eur_notional_and_policy_amounts():
    frame = invented()
    frame['current_value_eur'] = 999999.  # Stale legacy alias must never win.
    result = calculate_portfolio_exposure(frame, future=FUTURE, allocation=CONFIG)
    assert result.net_equity == 100.
    assert result.market_amount() == 130.
    assert result.net_market_multiple == 1.3
    assert summarize_future(frame, FUTURE).equity == 100.
    assert exposure_gaps(result, TARGET, CONFIG)['Gap (EUR)'].item() == 0.
    projected = exposure_positions(frame, result, FUTURE)
    assert projected.current_value_reporting.sum() == 130.
    assert frame.current_value_reporting.item() == 90.
    assert projected.reporting_currency.eq('EUR').all()
    assert projected.attrs['reporting_currency'] == 'EUR'


def test_funded_virtual_row_integrates_main_performance_schema():
    future = replace(FUTURE, net_funding_eur=5., closed_pnl_eur=2., fees_eur=.5)
    displayed = funded_strategy_positions(invented(), future, CONFIG)
    row = displayed.loc[displayed.position_id.eq('future:overlay')]
    assert row.current_value_reporting.item() == 16.5
    assert row.cost_basis_reporting.item() == 10.
    assert row.unrealized_gain_reporting.item() == 1.5
    assert row.return_pct.item() == 15.
    summary = summarize_performance(row)
    assert summary.covered_count == 1
    assert summary.cost_reporting == 10. and summary.gain_reporting == 1.5
    assert summary.return_pct == 15. and summary.estimated_count == 0


def test_real_main_valuation_output_runs_leverage_without_legacy_aliases(tmp_path):
    holdings = parse_holdings('position_key,id,name,ticker,shares,instrument_type,exposure_kind,bucket_id\n'
        'stock,stock,Invented stock,SYNTH,9,equity,equity,core\n')
    prices = tmp_path / 'synthetic-prices.json'
    prices.write_text(json.dumps({'prices': {'SYNTH': {'price': 10., 'currency': 'EUR',
        'observed_at': '2026-01-02T12:00:00+00:00'}}, 'fx': {}}))
    service = PriceService(StaticProvider(prices), now=lambda: datetime(2026, 1, 2, 12, tzinfo=timezone.utc))
    frame = prepare_portfolio(holdings, service, reporting_currency='EUR')
    assert 'current_value_eur' not in frame
    assert frame.current_value_reporting.item() == 90.
    result = calculate_portfolio_exposure(frame, future=FUTURE, allocation=CONFIG)
    plan = plan_exposure(result, TARGET, CONFIG, frame, permitted=['stock'])
    assert plan.equity == 100. and plan.complete and plan.trades.empty


def test_empty_eur_portfolio_can_still_value_funded_future():
    frame = invented().iloc[:0]
    frame.attrs['reporting_currency'] = 'EUR'
    result = calculate_portfolio_exposure(frame, future=FUTURE, allocation=CONFIG)
    assert result.net_equity == 10. and result.net_exposure == 40.
