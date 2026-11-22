"""Reuse the last risk calculation in a session while its financial inputs are current."""
from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from portfolio_app.analytics.analytics_service import load_risk
from portfolio_app.analytics.risk import RiskAnalytics
from portfolio_app.analytics.risk_data import RiskHistoryService
from portfolio_app.funds.leverage_settings import FILE as LEVERAGE_FILE
from portfolio_app.workspace.storage import revision


@dataclass(frozen=True)
class RiskSnapshot:
    settings: tuple
    holdings: pd.DataFrame
    histories: RiskHistoryService
    result: RiskAnalytics
    sources: pd.DataFrame


def view_risk(valued, data_dir, demo, benchmark, years, refresh=False, *, future=None,
              portfolio_result=None, scoped=False, portfolio_net_equity=None):
    # The computation and its reuse check consume the same projection. Presentation
    # fields such as a quote's advancing age are independent of financial risk.
    fields = ('id', 'position_id', 'name', 'shares', 'instrument_type', 'ticker', 'isin', 'manual_price',
              'current_value_reporting', 'quote_currency', 'reporting_currency', 'signed_notional_reporting',
              'product', 'contract_id', 'proxy_benchmark', 'return_convention', 'proxy_position_id', 'current_fx_rate')
    positions = valued.loc[:, [field for field in fields if field in valued]]
    today = date.today()
    actual = bool(valued.attrs.get('actual_accounts'))
    # Actual contract proxies can reference a position outside the selected accounts.
    proxy_inputs = (revision(data_dir / 'holdings.csv'), revision(data_dir / LEVERAGE_FILE)) if actual else ()
    settings = (data_dir.resolve(), demo, today, benchmark, years, actual, proxy_inputs, future, scoped,
                portfolio_result is not None, portfolio_result.net_equity if portfolio_result is not None else portfolio_net_equity)
    previous = st.session_state.get('analytics_risk_snapshot')
    if (not refresh and isinstance(previous, RiskSnapshot) and previous.settings == settings
            and previous.holdings.equals(positions) and previous.histories.inputs_current()):
        return previous.result, previous.sources

    service = RiskHistoryService.for_workspace(data_dir, demo, track_inputs=True)
    result, sources = load_risk(positions, data_dir, demo, benchmark, years, refresh, future=future,
        service=service, today=today, portfolio_result=portfolio_result, scoped=scoped, portfolio_net_equity=portfolio_net_equity)
    cached_sources = sources.assign(Status=sources.Status.replace('fresh', 'cached'))
    st.session_state['analytics_risk_snapshot'] = RiskSnapshot(settings, positions.copy(), service, result, cached_sources)
    return result, sources
