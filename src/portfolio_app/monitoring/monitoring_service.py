"""Background evaluation; workers never read or write Streamlit session state."""
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import cache, partial
import json
from threading import RLock
from typing import TYPE_CHECKING

import pandas as pd

from portfolio_app.market.market_data import coordinator
from portfolio_app.monitoring import monitoring
from portfolio_app.monitoring.monitoring import (SeriesSpec, completed_evaluation, evaluate_with_history,
    evaluate_account, unavailable, utc_now)
from portfolio_app.monitoring.monitoring_market import MonitoringMarket, SyntheticMonitoringProvider
from portfolio_app.monitoring.monitoring_store import EvaluationWriter


if TYPE_CHECKING:
    from portfolio_app.ledger.account_journal import Journal, JournalState
    from portfolio_app.market.prices import PriceResult


@dataclass(frozen=True)
class _AccountSources:
    journal: 'Journal'
    state: 'JournalState'
    holdings: pd.DataFrame
    currency: str
    value_positions: Callable[[frozenset[str]], pd.DataFrame]
    rate: Callable[[str], 'PriceResult']


class MonitoringService:
    def __init__(self, directory, *, demo=False, now=utc_now):
        self.directory, self.demo, self.now = directory, demo, now
        self.market = MonitoringMarket(directory, provider=SyntheticMonitoringProvider() if demo else None, now=now)
        self.series = {}
        self.browse_attempts = {}
        self.last_attempt = None
        self.last_success = ''
        self.error = ''
        self.lock = RLock()

    def browse(self, spec, *, refresh=False):
        key = json.dumps(vars(spec), sort_keys=True)
        with self.lock:
            series = self.series.get(key)
            attempted = self.browse_attempts.get(key)
        due = attempted is None or self.now()-attempted >= timedelta(minutes=5)
        if due or refresh:
            def fetch():
                value = self.market.get(spec, refresh=refresh)
                with self.lock:
                    self.series[key] = value
            if coordinator.request(self.directory, ('monitoring-browse', key), fetch, channel='monitoring'):
                with self.lock:
                    self.browse_attempts[key] = self.now()
        return series

    def schedule(self, *, refresh=False):
        now = self.now()
        with self.lock:
            if not refresh and self.last_attempt and now-self.last_attempt < timedelta(minutes=5):
                return
            if coordinator.request(self.directory, 'monitoring-evaluate', lambda: self.evaluate(refresh=refresh), channel='monitoring'):
                self.last_attempt = now

    def evaluate(self, *, refresh=False):
        try:
            writer = EvaluationWriter(self.directory)
            rules = writer.enabled_rules()
            @cache
            def fetch_series(spec):
                series = self.market.get(spec, refresh=refresh)
                key = json.dumps(vars(spec), sort_keys=True)
                with self.lock:
                    self.series[key] = series
                    self.browse_attempts[key] = self.now()
                return series

            account_sources = None
            if any(rule.kind != 'sma' for rule, _ in rules):
                try:
                    account_sources = self._account_sources(refresh=refresh, fetch_series=fetch_series)
                except Exception:
                    pass  # Record unavailable for each affected rule; instrument checks can proceed.
            frames = {}
            def frame_for(rule, series, *, now):
                # fetch_series fixes one source snapshot per spec for this evaluation pass.
                day = pd.Timestamp(now).tz_convert(series.timezone).date()
                key = (rule.series, rule.period, rule.window, rule.comparison_window, rule.cadence, day)
                if key not in frames:
                    frames[key] = monitoring.indicator_frame(rule, series, now=now)
                return frames[key]

            for rule, previous in rules:
                try:
                    if rule.kind == 'sma':
                        series = fetch_series(rule.series)
                        latest, frame = evaluate_with_history(rule, series, now=self.now(), frame_for=frame_for)
                        if rule.cadence != 'intraday' and latest.quality == 'valid' and previous and previous < latest.observed_at:
                            after = max(str(previous)[:10], rule.created_at[:10])
                            history = frame.loc[(frame.index > pd.Timestamp(after)) & (frame.index < pd.Timestamp(latest.observed_at))]
                            recovered = [
                                completed_evaluation(rule, series, row.Index.date().isoformat(), row,
                                    note='Recovered completed-close observation; recorded after reopening.')
                                for row in history.dropna(subset=['distance']).itertuples()
                            ]
                            if recovered:
                                writer.record(rule, recovered, recovered=True, now=self.now())
                        writer.record(rule, [latest], now=self.now())
                    elif account_sources is None:
                        writer.record(rule, [unavailable('Could not load the account inputs for this check.')], now=self.now())
                    else:
                        journal, holdings, valued, currency, rates, input_stamp, fx_quality = self.account_inputs(
                            [rule], sources=account_sources)
                        evaluation = evaluate_account(rule, journal, holdings, valued, reporting_currency=currency,
                                                      fx_rates=rates, now=self.now(), state=account_sources.state)
                        selected = set(rule.backing)
                        if rule.kind == 'coverage':
                            selected = {r.holding_id for r in account_sources.state.reservations.values()
                                        if r.account_id == rule.account_id and r.holding_id}
                        inputs = valued.loc[valued.position_id.isin(selected)]
                        cutoff = self.now() - timedelta(minutes=rule.max_age_minutes)
                        stale = any(row.price_status in {'stale', 'cached fallback', 'missing'} or row.fx_status in {'stale', 'missing'} or
                                    not row.price_observed_at or datetime.fromisoformat(row.price_observed_at) < cutoff or
                                    (row.fx_status != 'identity' and row.fx_observed_at and
                                     datetime.fromisoformat(row.fx_observed_at) < cutoff)
                                    for row in inputs.itertuples() if row.price_status != 'not_held')
                        if stale or fx_quality:
                            evaluation = unavailable('Backing-asset prices or required FX are stale/unavailable. Last valid condition is retained.', quality='stale')
                        evaluation = replace(evaluation, observed_at=input_stamp)
                        writer.record(rule, [evaluation], now=self.now())
                except Exception:
                    writer.record(rule, [unavailable('Evaluation failed. Review the rule inputs or refresh its data.')], now=self.now())
            self.last_success, self.error = self.now().isoformat(), ''
        except Exception:
            self.error = 'Monitoring could not update. Review the saved monitoring document or retry.'

    def _account_sources(self, *, refresh, fetch_series=None):
        from portfolio_app.ledger.account_journal import load_journal, replay
        from portfolio_app.ledger.security_history import cash_postings, effective_holdings, replay_securities
        from portfolio_app.portfolio.positions import read_snapshot
        from portfolio_app.portfolio.portfolio_settings import load_settings
        from portfolio_app.portfolio.valuation import value_holdings
        from portfolio_app.market.prices import PriceService, StaticProvider, YahooProvider, Quote, QuoteSnapshot
        from portfolio_app.market.fx import current_rate
        from portfolio_app.workspace.workspace_lock import workspace_lock
        with workspace_lock(self.directory):
            journal, journal_revision = load_journal(self.directory)
            holdings = read_snapshot(self.directory/'holdings.csv').holdings
            currency = load_settings(self.directory).reporting_currency
        securities = replay_securities(journal.securities)
        if journal.securities:
            holdings = effective_holdings(holdings, journal.securities, security_state=securities)
        postings = cash_postings(journal, security_cash=securities.cash)
        account_state = replay(journal, _security_postings=postings)
        if fetch_series is None:
            fetch_series = partial(self.market.get, refresh=refresh)
        self_now = self.now
        class AccountQuotes(YahooProvider):
            def price(self, ticker):
                series = fetch_series(SeriesSpec(ticker))
                if series.quality != 'valid':
                    raise ValueError('Account market source unavailable')
                if series.quote is not None and series.quote_at:
                    observed = pd.Timestamp(series.quote_at).to_pydatetime()
                    if observed > self_now()+timedelta(minutes=1):
                        raise ValueError('Account quote timestamp is in the future')
                    return Quote(series.quote, series.currency, observed)
                completed = [(d,p) for d,p,c in zip(series.dates,series.closes,series.complete) if c]
                if not completed:
                    raise ValueError('No completed account asset quote')
                day, price = completed[-1]
                observed = pd.Timestamp(day).tz_localize(series.timezone).to_pydatetime()
                return Quote(price, series.currency, observed)
        provider = StaticProvider(self.directory/'demo_prices.json') if self.demo else AccountQuotes()
        prices = QuoteSnapshot(PriceService(provider, self.directory/'.cache'/'monitoring-account-prices.json',
                                         now=self.now, ttl=timedelta(minutes=5)))
        @cache
        def value_positions(identities):
            selected = holdings.loc[holdings.position_id.isin(identities)]
            valued = value_holdings(selected, prices, refresh=refresh, reporting_currency=currency)
            valued.attrs['journal_revision'] = journal_revision
            return valued

        @cache
        def rate(unit):
            return current_rate(prices, unit, currency, refresh=refresh)

        return _AccountSources(journal, account_state, holdings, currency, value_positions, rate)

    def account_inputs(self, rules, *, refresh=False, sources=None):
        """Scope evidence to the requested backing assets, currencies and freshness limits."""
        if sources is None:
            sources = self._account_sources(refresh=refresh)
        journal, state, currency = sources.journal, sources.state, sources.currency
        needed = set().union(*(set(r.backing) for r in rules))
        units = {currency, *(r.currency for r in rules)}
        for rule in rules:
            if rule.kind == 'coverage':
                reservations = [r for r in state.reservations.values() if r.account_id == rule.account_id]
                needed.update(r.holding_id for r in reservations if r.holding_id)
                units.update(r.currency for r in reservations if not r.holding_id)
                units.update(r.currency for r in journal.margin_requirements if r.account_id == rule.account_id)
            else:
                units.update(unit for owner,unit,_ in state.loans if owner == rule.account_id)
                units.update(unit for (owner,unit),amount in state.cash.items() if owner == rule.account_id and amount < 0)
                units.update(rule.cash)
        valued = sources.value_positions(frozenset(needed))
        rates, observations, invalid = {}, [], False
        for unit in units:
            result = sources.rate(unit)
            if result.quote:
                rates[unit] = result.quote.price
                if unit != currency:
                    observations.append(result.quote.observed_at.isoformat())
                    invalid |= result.quote.observed_at > self.now()+timedelta(minutes=1) or result.status == 'stale' or (self.now()-result.quote.observed_at).total_seconds() > min(r.max_age_minutes for r in rules)*60
            else:
                invalid = True
        observations.extend(x for x in valued.price_observed_at if x)
        # Use the latest fact timestamp, with the complete snapshot in evaluation
        # inputs. Journal revision and quantities also change the fingerprint.
        stamp = max(observations, default='') or self.now().date().isoformat()
        return journal, sources.holdings, valued, currency, rates, stamp, invalid


_services = {}
_lock = RLock()


def monitoring_for(directory, *, demo=False):
    # Cache identity and background request identity must match UI polling,
    # including temporary roots exposed through short names or symlinks.
    directory = directory.resolve()
    key = (str(directory), demo)
    with _lock:
        if key not in _services:
            _services[key] = MonitoringService(directory, demo=demo)
        return _services[key]
