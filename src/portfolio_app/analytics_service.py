"""Analytics data loading and orchestration, independent of Streamlit rendering."""
from datetime import date

import pandas as pd

from portfolio_app.fundamentals import (PUBLIC_FEES, DemoFundamentalsProvider, Fundamentals,
    FundamentalsService, FundFee, Metric, YahooFundamentalsProvider, apply_fund_metadata, fee_key,
    load_fee_overrides)
from portfolio_app.risk import risk_analytics
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


def load_risk(valued, data_dir, demo, benchmark, years, refresh=False):
    service = RiskHistoryService(DemoRiskHistoryProvider() if demo else YahooRiskHistoryProvider(),
                                 None if demo else data_dir / '.cache' / 'risk-history')
    target = valued.reporting_currency.iloc[0] if len(valued) and 'reporting_currency' in valued else 'EUR'
    memo, histories, failures, status = {}, {}, {}, []
    bench = service.in_currency(benchmark, target, years, refresh=refresh, memo=memo)
    status.append(dict(Instrument=benchmark, Status=bench.status, Retrieved=bench.fetched_at, Note=bench.note))
    for identity, rows in valued.loc[valued.shares.gt(0)].groupby('id'):
        row = rows.iloc[0]
        if row.get('instrument_type') == 'cash':
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
    result = risk_analytics(valued, histories, bench.prices, years=years, failures=failures)
    return result, pd.DataFrame(status).drop_duplicates('Instrument')
