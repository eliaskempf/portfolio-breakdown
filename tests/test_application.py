"""Application boundaries with invented data and injected services only."""
from dataclasses import asdict
import json
from unittest.mock import Mock

import pandas as pd

from portfolio_app.application import (PortfolioInputs, historical_for, load_leverage_context,
    prepare_application, prepare_exposure, select_price_service)
from portfolio_app.futures import FuturePosition
from portfolio_app.futures_workspace import SANDBOX_FILE
from portfolio_app.holdings import parse_holdings
from portfolio_app.positions import HoldingsSnapshot


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
    from portfolio_app.allocation import Allocation, Bucket
    from portfolio_app.prices import PriceService, StaticProvider
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
