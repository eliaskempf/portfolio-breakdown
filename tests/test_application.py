"""Application boundaries with invented data and injected services only."""
from dataclasses import asdict
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from portfolio_app.application import (PortfolioInputs, historical_for, load_leverage_context,
    prepare_application, prepare_exposure, select_price_service)
from portfolio_app.studies.futures import FuturePosition
from portfolio_app.studies.futures_workspace import SANDBOX_FILE
from portfolio_app.portfolio.holdings import parse_holdings
from portfolio_app.portfolio.positions import HoldingsSnapshot


def test_currency_eligibility_gates_exposure_and_preserves_saved_records(tmp_path):
    ordinary = load_leverage_context(tmp_path, 'EUR')
    assert ordinary.supported and not ordinary.active and not ordinary.unsupported_records
    assert prepare_exposure(None, [], None, ordinary) is None
    path = tmp_path / SANDBOX_FILE
    content = json.dumps(dict(version=2, instrument='FMWN', simulation=True, position=asdict(FuturePosition())))
    path.write_text(content)
    eur = load_leverage_context(tmp_path, 'EUR')
    usd = load_leverage_context(tmp_path, 'USD')
    assert eur.active and eur.future is not None
    assert not usd.supported and not usd.active and usd.unsupported_records
    assert prepare_exposure(None, [], None, usd) is None
    assert path.read_text() == content


def test_offline_selection_and_preparation_never_request_live_services(tmp_path):
    live = Mock(side_effect=AssertionError('Live prices must remain lazy'))
    service = select_price_service(tmp_path, offline_demo=True, live_prices=live)
    holdings = parse_holdings('id,name,ticker,shares\na,Invented missing,SYNTH-A,2\n')
    original = holdings.copy(deep=True)
    inputs = PortfolioInputs(HoldingsSnapshot(holdings, None), None, {}, [])
    prepared = prepare_application(inputs, service, historical=historical_for(tmp_path, demo=True))
    assert prepared.status.missing_prices == prepared.status.missing_costs == 1
    assert prepared.status.estimated_costs == prepared.status.stale_quotes == 0
    assert prepared.status.problems.name.tolist() == ['Invented missing']
    assert pd.isna(prepared.valued.current_value_reporting.iloc[0])
    pd.testing.assert_frame_equal(holdings, original)
    live.assert_not_called()
    assert select_price_service(tmp_path, supplied=service, live_prices=live) is service
    live.assert_not_called()


def test_refresh_is_explicit_and_target_preparation_preserves_input_snapshot(tmp_path):
    from portfolio_app.portfolio.allocation import Allocation, Bucket
    from portfolio_app.market.prices import PriceService, StaticProvider
    path = tmp_path / 'invented.json'
    path.write_text(json.dumps({'prices': {'SYNTH-A': {'price': 100, 'currency': 'EUR', 'observed_at': '2026-01-01T12:00:00+00:00'}}, 'fx': {}}))
    provider = StaticProvider(path)
    provider.price = Mock(wraps=provider.price)
    service = PriceService(provider)
    holdings = parse_holdings('id,name,ticker,shares,bucket_id,within_bucket_target\na,Invented holding,SYNTH-A,2,core,1\n')
    original = holdings.copy(deep=True)
    inputs = PortfolioInputs(HoldingsSnapshot(holdings, 'invented'), Allocation((Bucket('core', 'Core', target=1.),)), {}, [])
    first = prepare_application(inputs, service)
    assert first.valued.current_value_reporting.item() == 200.
    assert first.valued.target_allocation.item() == 1.
    prepare_application(inputs, service)
    assert provider.price.call_count == 1
    prepare_application(inputs, service, refresh=True)
    assert provider.price.call_count == 2
    pd.testing.assert_frame_equal(holdings, original)


def test_exposure_cache_uses_effective_leverage_inputs_and_currency(tmp_path):
    from dataclasses import replace
    from portfolio_app.exposure.exposure_workspace import analytical_signature
    from portfolio_app.funds.leverage_settings import LeverageSettings, MarginLiability
    from portfolio_app.ledger.account_journal import Journal
    from portfolio_app.ledger.security_history import SecurityBook

    valued = pd.DataFrame({'reporting_currency': ['EUR']})
    valued.attrs['reporting_currency'] = 'EUR'
    liability = MarginLiability('invented', 'Invented debt', 50.)
    ctx = replace(load_leverage_context(tmp_path, 'EUR'), active=True, settings=LeverageSettings(liabilities=(liability,)))
    def key(context):
        return analytical_signature(valued, (), context.exposure_options(valued))

    original = key(ctx)
    other = replace(ctx, journal_revision='new', journal=replace(Journal(version=2), securities=(SecurityBook('position', 'invented', ('0', '', '')),)))
    assert key(other) == original
    adopted = replace(other, journal=replace(other.journal, adopted_liability_ids=(liability.id,)))
    assert key(adopted) != original
    assert adopted.exposure_options(valued)['liabilities'] == ()
    assert key(replace(ctx, settings=LeverageSettings(liabilities=(replace(liability, balance_eur=None),)))) != original
    valued.attrs['reporting_currency'] = 'USD'
    assert key(ctx) != original


def test_repeated_valuation_reuses_original_costs_and_requests_current_dated_fx(monkeypatch):
    from decimal import localcontext
    from portfolio_app.market.fx import DatedRate
    from portfolio_app.market.prices import PriceService, UnavailableProvider
    from portfolio_app.performance import cost_basis
    from portfolio_app.performance.performance import ValuationCostCache

    holdings = parse_holdings('id,name,ticker,shares,acquisition_price,acquisition_currency\n'
                              'a,Invented share,SYNTH-A,2,50,USD\n')
    row = holdings.iloc[0]
    parts = [cost_basis.component(2, 100, 'USD', '2023-01-10')]
    document = cost_basis.encode_components(parts, row)
    holdings[cost_basis.FIELD] = document
    inputs = PortfolioInputs(HoldingsSnapshot(holdings, 'invented'), None, {}, [])
    prices = PriceService(UnavailableProvider())
    history = Mock()
    history.get.side_effect = [DatedRate(.9, '2023-01-10'), DatedRate(.8, '2023-01-10')]
    read = Mock(wraps=cost_basis.read_details)
    monkeypatch.setattr(cost_basis, 'read_details', read)
    cache = ValuationCostCache()
    def cost(target='EUR'):
        return prepare_application(inputs, prices, reporting_currency=target, historical=history,
                                   cost_cache=cache).valued.cost_basis_reporting.item()

    assert cost() == 90 and cost() == 80
    assert history.get.call_count == 2 and read.call_count == 1
    assert cost('USD') == 100 and read.call_count == 1
    # Editors receive fresh documents, separate from the valuation snapshot.
    cost_basis.active_components(row.to_dict() | {cost_basis.FIELD: document})[0]['amount'] = '999'
    assert cost('USD') == 100
    holdings.loc[0, 'acquisition_price'] = 51
    assert cost('USD') == 102  # The edited summary supersedes the saved components.
    holdings.loc[0, 'acquisition_price'] = 51.27
    with localcontext() as precision:
        precision.prec = 3
        assert cost('USD') == 103
    assert cost('USD') == 102.54
    holdings.loc[0, cost_basis.FIELD] = cost_basis.encode_components(
        [cost_basis.component(2, 98, 'USD')], holdings.iloc[0])
    assert cost('USD') == 98


def test_cached_valuation_validates_replaced_cash_document_before_summary_or_fx():
    from portfolio_app.performance import cost_basis
    from portfolio_app.performance.performance import ValuationCostCache, cash_balance_only, position_performance
    from portfolio_app.portfolio.holdings import DataError

    row = dict(position_id='invented-cash', instrument_type='cash', shares=0,
               acquisition_price=0, acquisition_currency='EUR')
    rows = pd.DataFrame([row])
    cache = ValuationCostCache()
    for amount, balance_only in [(None, True), (0, False)]:
        rows[cost_basis.FIELD] = cost_basis.encode_components([cost_basis.component(0, amount, 'EUR')], row)
        for _ in range(2):
            assert cash_balance_only(rows, cost_cache=cache).item() == balance_only
    rows[cost_basis.FIELD] = 'invalid document'
    rows['acquisition_currency'] = pd.NA
    history = Mock()
    with pytest.raises(DataError, match='Stored cost details are invalid'):
        position_performance(rows, historical=history, cost_cache=cache)
    history.get.assert_not_called()
