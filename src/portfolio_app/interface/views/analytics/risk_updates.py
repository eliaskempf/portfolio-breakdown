"""Reuse the last risk calculation in a session while its financial inputs are current."""
from dataclasses import dataclass, replace
from datetime import date

import pandas as pd
import streamlit as st

from portfolio_app.analytics.analytics_service import load_risk
from portfolio_app.analytics.risk import RiskAnalytics
from portfolio_app.analytics.risk_data import RiskHistoryService
from portfolio_app.analytics.risk_refresh import (
    active_risk_refresh, read_risk_snapshot, retry_deferred, start_risk_refresh, workspace_history_cache,
)
from portfolio_app.interface.shared.background_ui import refresh_allowed, status_fragment
from portfolio_app.workspace.workspace_lock import workspace_context
from portfolio_app.funds.leverage_settings import FILE as LEVERAGE_FILE
from portfolio_app.workspace.storage import revision


@dataclass(eq=False, frozen=True)
class RiskSelection:
    settings: tuple
    proxy_inputs: tuple
    holdings: pd.DataFrame

    def matches(self, other):
        return self.settings == other.settings and self.proxy_inputs == other.proxy_inputs and self.holdings.equals(other.holdings)

    def proxies_current(self):
        return all(revision(path) == saved for path, saved in self.proxy_inputs)


@dataclass(frozen=True)
class RiskSnapshot:
    """Completed results and source rows are read-only across subscribing views."""
    selection: RiskSelection
    histories: RiskHistoryService
    result: RiskAnalytics
    sources: pd.DataFrame


def _refresh_failure():
    st.warning('Historical risk could not be updated. Showing cached histories.')


def mounted_risk_current(result):
    job = st.session_state.get('analytics_risk_refresh')
    if job and job.previous and job.previous.result is result and not job.result.done():
        return job.selection.proxies_current()
    snapshot = st.session_state.get('analytics_risk_snapshot')
    return (isinstance(snapshot, RiskSnapshot) and snapshot.result is result
            and snapshot.selection.proxies_current()
            and snapshot.histories.inputs_current(allow_due=retry_deferred(snapshot.histories.cache, snapshot.selection)))


def _observe(job):
    @status_fragment()
    def status(poll):
        if st.session_state.get('analytics_risk_refresh') is not job:
            return
        if job.result.done():
            if refresh_allowed():
                st.rerun()
        else:
            job.submit()
            st.caption('Updating historical risk…')
        poll(.5)

    status()


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
    workspace = (data_dir.resolve(), workspace_context(data_dir.resolve()))
    capital = portfolio_result.net_equity if portfolio_result is not None else portfolio_net_equity
    settings = (workspace, demo, today, benchmark, years, actual, future, scoped,
                portfolio_result is not None, capital is None, None if pd.isna(capital) else capital)
    # An actual contract proxy can be outside the selected accounts.
    proxy_paths = (data_dir / 'holdings.csv', data_dir / LEVERAGE_FILE) if actual else ()
    selection = RiskSelection(settings, tuple((path, revision(path)) for path in proxy_paths), positions.copy())

    cache = None if demo else workspace_history_cache(workspace)

    def calculate(*, cached_read=None, before_fetch=None, previous=None):
        service = RiskHistoryService.for_workspace(data_dir, demo, track_inputs=True,
            cached_read=cached_read, before_fetch=before_fetch)
        if cache is not None:
            service.cache = cache
        if previous is not None:
            service.acquired.update(previous.histories.acquired)
        result, sources = load_risk(selection.holdings, data_dir, demo, benchmark, years, refresh, future=future,
            service=service, today=today, portfolio_result=portfolio_result, scoped=scoped, portfolio_net_equity=portfolio_net_equity)
        # Retain the receipts, releasing callbacks that reference other active jobs.
        service.cached_read = service.before_fetch = None
        return RiskSnapshot(selection, service, result, sources)

    def publish(snapshot):
        sources = snapshot.sources.assign(Status=snapshot.sources.Status.replace('fresh', 'cached'))
        st.session_state['analytics_risk_snapshot'] = replace(snapshot, sources=sources)
        return snapshot.result, snapshot.sources

    def waiting(job):
        st.session_state['analytics_risk_refresh'] = job
        _observe(job)
        return job.previous.result, job.previous.sources

    job = st.session_state.pop('analytics_risk_refresh', None)
    if job and job.selection.matches(selection) and (not refresh or job.force):
        if not job.result.done():
            return waiting(job)
        if not refresh and not job.result.cancelled():
            if job.result.exception() is not None:
                if job.previous.histories.inputs_current(allow_due=True):
                    _refresh_failure()
                    return publish(job.previous)
            else:
                snapshot = job.result.result()
                if snapshot.histories.inputs_current() and selection.proxies_current():
                    return publish(snapshot)

    if job := active_risk_refresh(workspace, selection, force=refresh):
        return waiting(job)
    previous = st.session_state.get('analytics_risk_snapshot')
    deferred = cache is not None and not refresh and retry_deferred(cache, selection)
    if (not refresh and isinstance(previous, RiskSnapshot) and previous.selection.matches(selection)
            and previous.histories.inputs_current(allow_due=deferred)):
        if deferred:
            _refresh_failure()
        return previous.result, previous.sources
    if demo:
        return publish(calculate())

    saved = read_risk_snapshot(workspace, selection, calculate, force=refresh)
    if not saved.histories.due:
        return publish(saved)
    if deferred:
        _refresh_failure()
        return publish(saved)
    return waiting(start_risk_refresh(workspace, selection, saved, calculate, force=refresh))
