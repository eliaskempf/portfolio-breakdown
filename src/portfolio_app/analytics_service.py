"""Analytics data loading and orchestration, independent of Streamlit rendering."""
from datetime import date

import pandas as pd

from portfolio_app.fundamentals import (PUBLIC_FEES, DemoFundamentalsProvider, Fundamentals,
    FundamentalsService, FundFee, Metric, YahooFundamentalsProvider, apply_fund_metadata, fee_key,
    load_fee_overrides)
from portfolio_app.risk import risk_analytics, native_future_weekly_returns
from portfolio_app.risk_data import DemoRiskHistoryProvider, RiskHistoryService, YahooRiskHistoryProvider


def load_metrics(holdings, data_dir, *, demo=False, fetch=True, refresh=False):
    provider = DemoFundamentalsProvider() if demo else YahooFundamentalsProvider()
    service = FundamentalsService(provider, None if demo else data_dir / '.cache' / 'fundamentals')
    overrides = load_fee_overrides(data_dir / 'fund-fees.json')
    result, listings = {}, {}
    for row in holdings.drop_duplicates('id').to_dict('records'):
        kind = row.get('instrument_type') or 'unknown'
        ticker = row['ticker']
        if fetch and kind in {'equity', 'etf', 'unknown'} and ticker:
            if ticker not in listings:
                listings[ticker] = service.get(ticker, refresh=refresh)
            snapshot = listings[ticker]
        else:
            snapshot = Fundamentals(ticker, kind)
        if demo and (kind == 'etf' or row.get('isin') in PUBLIC_FEES):
            snapshot = Fundamentals(ticker, 'etf', {
                'fee': Metric(.0025, 'fraction', 'Synthetic demo'),
                'distribution_yield': Metric(.01, 'fraction', 'Synthetic demo'),
                'fund_pe': Metric(22., 'ratio', 'Synthetic demo'),
                'fund_pb': Metric(2.5, 'ratio', 'Synthetic demo'),
                'fund_assets': Metric(5e8, 'EUR', 'Synthetic demo')}, 'fresh', note='Synthetic demo fund metrics')
            # Avoid presenting public issuer fees as invented demo observations.
            synthetic = dict(overrides)
            synthetic.setdefault(fee_key(row), FundFee(.0025, 'Synthetic demo', date.today().isoformat()))
            snapshot = apply_fund_metadata(snapshot, row, synthetic)
        else:
            snapshot = apply_fund_metadata(snapshot, row, overrides)
        result[row['id']] = snapshot
    return result


def _actual_future_risk(valued, data_dir, service, target, years, refresh, memo, histories, failures, status,
                        *, today=None, qualified_histories=None):
    """Risk-only projections; actual journal and recorded balances are untouched."""
    from types import SimpleNamespace
    from portfolio_app.derivative_market import CATALOG
    from portfolio_app.holdings import DataError
    from portfolio_app.leverage_settings import load_leverage_settings
    from portfolio_app.positions import read_snapshot
    from portfolio_app.study_market import validate_history_for_contract
    factors = {item.position_id for item in load_leverage_settings(data_dir)[0].fund_factors}
    source_positions = read_snapshot(data_dir / 'holdings.csv').holdings
    extras, exposures, returns = [], {}, {}
    for identity, rows in valued.loc[valued.instrument_type.eq('future')].groupby('id'):
        row = rows.iloc[0]
        native = row.quote_currency
        exposures[identity] = row.get('signed_notional_reporting', float('nan'))
        claim = row.current_value_reporting
        # An unsettled or pending foreign-currency claim has FX risk independent
        # of the future's native market move. This row exists only in risk input.
        if pd.notna(claim) and claim != 0:
            claim_id = 'risk-claim:' + str(identity)
            extras.append(dict(row, id=claim_id, position_id=claim_id, instrument_type='cash',
                               name=str(row['name']) + ' · unsettled claim', shares=1., ticker=''))
            if native != target:
                cash_fx = service.cash(native, target, years, refresh=refresh, memo=memo)
                if cash_fx.prices.empty:
                    failures[claim_id] = 'Unsettled-claim FX history unavailable'
                else:
                    histories[claim_id] = cash_fx.prices
        if pd.notna(exposures[identity]) and exposures[identity] == 0:
            continue
        product = row.get('product', '')
        if not isinstance(product, str) or not product:
            parts = str(row.get('contract_id', '')).split(':')
            product = parts[1] if len(parts) == 3 else ''
        catalog = CATALOG.get(product)
        try:
            supplied = (qualified_histories or {}).get(identity)
            if supplied is not None:
                expected = row.get('proxy_benchmark') or (catalog.underlying if catalog else '')
                convention = row.get('return_convention') or (catalog.return_convention if catalog else 'unknown')
                validate_history_for_contract(supplied, SimpleNamespace(currency=native, return_convention=convention),
                                              benchmark=expected)
                prices = supplied.values
                note = 'Explicit qualified contract-currency proxy: ' + supplied.source
                history_status, retrieved = 'provided', ''
            else:
                proxy_id = row.get('proxy_position_id', '')
                candidates = valued.loc[valued.position_id.eq(proxy_id)] if 'position_id' in valued else valued.iloc[:0]
                if candidates.empty:
                    candidates = source_positions.loc[source_positions.position_id.eq(proxy_id)]
                if (product not in ('FMWN', 'FMWO', 'FMWB') or len(candidates) != 1 or proxy_id in factors
                        or candidates.iloc[0].get('instrument_type') != 'etf' or candidates.iloc[0].shares <= 0
                        or not candidates.iloc[0].ticker):
                    raise DataError('A qualified contract-currency index proxy is required; no automatic proxy substitution.')
                ticker = candidates.iloc[0].ticker
                proxy = service.in_currency(ticker, native, years, refresh=refresh, memo=memo)
                if proxy.prices.empty:
                    raise DataError('Contract-currency proxy history unavailable')
                prices = proxy.prices
                history_status, retrieved = proxy.status, proxy.fetched_at
                note = f'Explicit linked ETF {ticker}: approximate MSCI World NTR in {native}; fund costs/tracking differ.'
            fx = None
            current_fx = 1.
            if native != target:
                current_fx = row.get('current_fx_rate', float('nan'))
                observed_fx = service.cash(native, target, years, refresh=refresh, memo=memo)
                if observed_fx.prices.empty:
                    raise DataError('Historical futures settlement FX unavailable')
                fx = observed_fx.prices
                if observed_fx.status == 'stale':
                    history_status = 'stale'
            returns[identity] = native_future_weekly_returns(prices, fx=fx, current_fx=current_fx, today=today)
            status.append(dict(Instrument=str(identity), Status=history_status, Retrieved=retrieved,
                               Note=note + ' Native variation translated at week-end FX; no FX gain on full notional.'))
        except (DataError, ValueError, TypeError) as exc:
            failures[identity] = str(exc)
            status.append(dict(Instrument=str(identity), Status='unavailable', Retrieved='', Note=str(exc)))
    if extras:
        valued = pd.concat([valued, pd.DataFrame(extras)], ignore_index=True)
    return valued, exposures, returns


def load_risk(valued, data_dir, demo, benchmark, years, refresh=False, *, future=None, portfolio_result=None,
              service=None, today=None, qualified_histories=None):
    service = service or RiskHistoryService(DemoRiskHistoryProvider() if demo else YahooRiskHistoryProvider(),
                                           None if demo else data_dir / '.cache' / 'risk-history')
    target = valued.reporting_currency.iloc[0] if len(valued) and 'reporting_currency' in valued else 'EUR'
    actual = bool(valued.attrs.get('actual_accounts'))
    if future is not None and target != 'EUR':
        from portfolio_app.holdings import DataError
        raise DataError('Integrated leverage risk currently requires EUR reporting.')
    memo, histories, failures, status = {}, {}, {}, []
    bench = service.in_currency(benchmark, target, years, refresh=refresh, memo=memo)
    status.append(dict(Instrument=benchmark, Status=bench.status, Retrieved=bench.fetched_at, Note=bench.note))
    for identity, rows in valued.loc[valued.shares.gt(0)].groupby('id'):
        row = rows.iloc[0]
        if actual and row.get('instrument_type') == 'future':
            continue
        if row.get('instrument_type') in ('cash', 'liability'):
            if row.quote_currency != target:
                history = service.cash(row.quote_currency, target, years, refresh=refresh, memo=memo)
                if not history.prices.empty:
                    histories[identity] = history.prices
                else:
                    failures[identity] = 'Historical cash FX unavailable'
            continue
        if not row.ticker or pd.notna(row.get('manual_price', float('nan'))):
            failures[identity] = 'no supported history for a manual or unlisted asset'
            continue
        history = service.in_currency(row.ticker, target, years, refresh=refresh, memo=memo)
        status.append(dict(Instrument=row.ticker, Status=history.status, Retrieved=history.fetched_at, Note=history.note))
        if not history.prices.empty:
            histories[identity] = history.prices
        else:
            failures[identity] = history.note or 'history unavailable'
    exposures = None
    explicit_returns = None
    if actual:
        valued, exposures, explicit_returns = _actual_future_risk(valued, data_dir, service, target, years, refresh,
            memo, histories, failures, status, today=today, qualified_histories=qualified_histories)
    if portfolio_result is not None and not actual:
        exposures = {}
        if future is not None:
            from portfolio_app.futures_market import PROXY_TICKER
            from portfolio_app.future_projections import FUTURE_SOURCE_ID, risk_position
            identity = FUTURE_SOURCE_ID
            valued = pd.concat([valued, pd.DataFrame([risk_position(future)])], ignore_index=True)
            exposures[identity] = future.notional
            if future.contracts and future.instrument == 'FMWN':
                proxy = service.eur(PROXY_TICKER, years, refresh=refresh, memo=memo)
                if not proxy.prices.empty:
                    histories[identity] = proxy.prices
                else:
                    failures[identity] = 'MSCI World EUR proxy history unavailable'
                status.append(dict(Instrument=f'{future.instrument} via {PROXY_TICKER}', Status=proxy.status,
                    Retrieved=proxy.fetched_at, Note='Approximate index beta using adjusted EUR ETF proxy; signed notional weight.'))
            elif future.contracts:
                failures[identity] = 'No supported index-risk proxy for this contract'
    result = risk_analytics(valued, histories, bench.prices, years=years, failures=failures, today=today,
        exposures=exposures, net_equity=portfolio_result.net_equity if portfolio_result is not None else None,
        return_series=explicit_returns)
    if actual:
        result.note += ' Futures replay native weekly moves at current native notional, converting variation at historical week-end FX; cash, debt and unsettled claims carry separate FX risk.'
    return result, pd.DataFrame(status).drop_duplicates('Instrument')
