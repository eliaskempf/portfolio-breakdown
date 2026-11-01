"""Background evaluation; workers never read or write Streamlit session state."""
from dataclasses import replace
from datetime import timedelta
import json
from threading import RLock

import pandas as pd

from portfolio_app.market_data import coordinator
from portfolio_app.monitoring import (SeriesSpec, Evaluation, condition, evaluate_series,
    evaluate_account, indicator_frame, unavailable, utc_now)
from portfolio_app.monitoring_market import MonitoringMarket, SyntheticMonitoringProvider
from portfolio_app.monitoring_store import load_monitoring, record_evaluations, rule_from_dict


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
            if coordinator.request(self.directory, ('monitoring-browse', key), fetch):
                with self.lock:
                    self.browse_attempts[key] = self.now()
        return series

    def schedule(self, *, refresh=False):
        now = self.now()
        with self.lock:
            if not refresh and self.last_attempt and now-self.last_attempt < timedelta(minutes=5):
                return
            if coordinator.request(self.directory, 'monitoring-evaluate', lambda: self.evaluate(refresh=refresh)):
                self.last_attempt = now

    def evaluate(self, *, refresh=False):
        try:
            doc, _ = load_monitoring(self.directory)
            rules = [rule_from_dict(r) for r in doc['rules'] if r['enabled']]
            for rule in rules:
                try:
                    if rule.kind == 'sma':
                        series = self.market.get(rule.series, refresh=refresh)
                        with self.lock:
                            series_key = json.dumps(vars(rule.series), sort_keys=True)
                            self.series[series_key] = series
                            self.browse_attempts[series_key] = self.now()
                        latest = evaluate_series(rule, series, now=self.now())
                        previous = doc['states'].get(rule.id, {}).get('last_valid', {}).get('observed_at')
                        if rule.cadence != 'intraday' and latest.quality == 'valid' and previous:
                            frame = indicator_frame(rule, series, now=self.now()).dropna()
                            after = max(str(previous)[:10], rule.created_at[:10])
                            history = frame.loc[(frame.index > pd.Timestamp(after)) & (frame.index < pd.Timestamp(latest.observed_at))]
                            recovered = [Evaluation(day.date().isoformat(), float(row.distance), float(row.sma), float(row.price),
                                condition(float(row.distance), rule), inputs={'currency': series.currency, 'basis': rule.series.basis},
                                note='Recovered completed-close observation; recorded after reopening.') for day, row in history.iterrows()]
                            if recovered:
                                record_evaluations(self.directory, rule, recovered, recovered=True, now=self.now())
                        record_evaluations(self.directory, rule, [latest], now=self.now())
                    else:
                        journal, holdings, valued, currency, rates, input_stamp, fx_quality = self.account_inputs([rule], refresh=refresh)
                        evaluation = evaluate_account(rule, journal, holdings, valued, reporting_currency=currency, fx_rates=rates, now=self.now())
                        selected = set(rule.backing)
                        if rule.kind == 'coverage':
                            from portfolio_app.account_journal import replay
                            selected = {r.holding_id for r in replay(journal).reservations.values() if r.account_id == rule.account_id and r.holding_id}
                        inputs = valued.loc[valued.position_id.isin(selected)]
                        stale = any(row.price_status in {'stale', 'cached fallback', 'missing'} or row.fx_status in {'stale', 'missing'} or
                                    pd.isna(row.price_age_hours) or row.price_age_hours*60 > rule.max_age_minutes or
                                    (pd.notna(row.fx_age_hours) and row.fx_age_hours*60 > rule.max_age_minutes)
                                    for row in inputs.itertuples() if row.price_status != 'not_held')
                        if stale or fx_quality:
                            evaluation = unavailable('Backing-asset prices or required FX are stale/unavailable. Last valid condition is retained.', quality='stale')
                        evaluation = replace(evaluation, observed_at=input_stamp)
                        record_evaluations(self.directory, rule, [evaluation], now=self.now())
                except Exception:
                    record_evaluations(self.directory, rule, [unavailable('Evaluation failed. Review the rule inputs or refresh its data.')], now=self.now())
            self.last_success, self.error = self.now().isoformat(), ''
        except Exception:
            self.error = 'Monitoring could not update. Review the saved monitoring document or retry.'

    def account_inputs(self, rules, *, refresh=False):
        from portfolio_app.account_journal import load_journal, replay
        from portfolio_app.positions import read_snapshot
        from portfolio_app.portfolio_settings import load_settings
        from portfolio_app.valuation import value_holdings
        from portfolio_app.prices import PriceService, StaticProvider, YahooProvider, Quote
        from portfolio_app.fx import current_rate
        journal, journal_revision = load_journal(self.directory)
        holdings = read_snapshot(self.directory/'holdings.csv').holdings
        currency = load_settings(self.directory).reporting_currency
        market = self.market
        self_now = self.now
        class AccountQuotes(YahooProvider):
            def price(self, ticker):
                series = market.get(SeriesSpec(ticker), refresh=refresh)
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
        prices = PriceService(provider, self.directory/'.cache'/'monitoring-account-prices.json', now=self.now, ttl=timedelta(minutes=5))
        state = replay(journal)
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
        valued = value_holdings(holdings.loc[holdings.position_id.isin(needed)], prices, refresh=refresh, reporting_currency=currency)
        rates, observations, invalid = {}, [], False
        for unit in units:
            result = current_rate(prices, unit, currency, refresh=refresh)
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
        valued.attrs['journal_revision'] = journal_revision
        return journal, holdings, valued, currency, rates, stamp, invalid


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
