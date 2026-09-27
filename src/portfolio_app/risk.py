"""Risk of constant current weights, from synchronized weekly EUR returns."""
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from portfolio_app.fundamentals import finite

DEFAULT_BENCHMARK = 'IUSQ.DE'
MIN_OBSERVATIONS = 52
SUBUNITS = {'GBp': ('GBP', .01), 'GBX': ('GBP', .01), 'ZAc': ('ZAR', .01), 'ILA': ('ILS', .01)}


def daily_prices(series: pd.Series) -> pd.Series:
    """Keep local trading dates, not UTC dates shifted across a market close."""
    result = pd.to_numeric(series, errors='coerce').copy()
    result.index = pd.DatetimeIndex(result.index).tz_localize(None).normalize()
    result = result.loc[~result.index.duplicated(keep='last')].sort_index()
    return result.where(np.isfinite(result) & result.gt(0)).dropna()


def eur_prices(prices, currency, fx=None):
    currency, factor = SUBUNITS.get(currency, (currency, 1.))
    if len(currency) != 3 or not currency.isupper():
        raise ValueError('History currency is unavailable')
    result = daily_prices(prices) * factor
    if currency != 'EUR':
        if fx is None:
            raise ValueError('Historical EUR exchange rates unavailable')
        result = result * daily_prices(fx).reindex(result.index)
    return result.dropna()


def weekly_returns(prices, *, today=None):
    today = pd.Timestamp(today or date.today()).normalize()
    prices = daily_prices(prices)
    prices = prices.loc[prices.index < today]
    if prices.empty:
        return pd.Series(dtype=float)
    # Resampling retains empty weeks; pct_change must not fill their endpoints.
    weekly = prices.resample('W-FRI').last()
    weekly = weekly.loc[weekly.index < today]
    return weekly.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)


@dataclass
class RiskAnalytics:
    status: str = 'unavailable'
    note: str = ''
    beta: float | None = None
    volatility: float | None = None
    correlation: float | None = None
    observations: int = 0
    start: str = ''
    end: str = ''
    covered_value: float = 0.
    known_value: float = 0.
    valuation_complete: bool = True
    holdings: pd.DataFrame = field(default_factory=pd.DataFrame)
    correlations: pd.DataFrame = field(default_factory=pd.DataFrame)
    excluded: dict[str, str] = field(default_factory=dict)


def risk_analytics(valued, histories: dict[str, pd.Series], benchmark: pd.Series,
                   *, today=None, years=3, failures=None) -> RiskAnalytics:
    if years not in (1, 3, 5):
        raise ValueError('Risk window must be 1, 3 or 5 years')
    held = valued.loc[valued.shares.gt(0)]
    valid = held.current_value_eur.map(finite).notna() & held.current_value_eur.ge(0)
    result = RiskAnalytics(known_value=float(held.loc[valid, 'current_value_eur'].sum()),
                           valuation_complete=bool(valid.all()))
    end = pd.Timestamp(today or date.today()).normalize()
    start = end - pd.DateOffset(years=years)
    bench = weekly_returns(benchmark, today=end).loc[lambda s: s.index >= start].rename('__benchmark__')
    # Listing-level histories are fetched once; calculation identities remain instrument IDs.
    returns, values = {}, {}
    for identity, rows in held.groupby('id', sort=False):
        label = str(rows.name.iloc[0])
        if rows.current_value_eur.map(finite).isna().any():
            result.excluded[identity] = f'{label}: missing current valuation'
            continue
        value = float(rows.current_value_eur.sum())
        if value <= 0:
            continue
        cash = rows.get('instrument_type', pd.Series('', index=rows.index)).eq('cash').all() and rows.quote_currency.eq('EUR').all()
        if cash:
            series = pd.Series(0., index=bench.index)
        elif identity in histories:
            series = weekly_returns(histories[identity], today=end).reindex(bench.index)
        else:
            result.excluded[identity] = f'{label}: {(failures or {}).get(identity, "market history unavailable")}'
            continue
        if pd.concat([series, bench], axis=1).dropna().shape[0] < MIN_OBSERVATIONS:
            result.excluded[identity] = f'{label}: fewer than 52 matched weekly returns'
            continue
        returns[identity], values[identity] = series, value
    result.covered_value = sum(values.values())
    if not returns:
        result.note = 'No holdings have sufficient comparable history.'
        return result
    # One shared sample is essential: pairwise covariances can be inconsistent.
    sample = pd.concat([pd.DataFrame(returns), bench], axis=1).dropna()
    result.observations = len(sample)
    if len(sample) < MIN_OBSERVATIONS:
        result.note = 'Fewer than 52 weekly returns in the common sample; try a longer window.'
        return result
    result.start, result.end = sample.index[0].date().isoformat(), sample.index[-1].date().isoformat()
    asset_returns, bench = sample[list(returns)], sample['__benchmark__']
    weights = pd.Series(values) / result.covered_value
    covariance = asset_returns.cov() * 52
    marginal = covariance @ weights
    variance = max(0., float(weights @ marginal))
    sigma = np.sqrt(variance)
    portfolio = asset_returns @ weights
    bench_var = float(bench.var())
    has_benchmark_variance = bench_var > 1e-20
    result.beta = finite(portfolio.cov(bench) / bench_var) if has_benchmark_variance else None
    result.volatility = float(sigma)
    result.correlation = finite(portfolio.corr(bench)) if has_benchmark_variance and sigma > 1e-10 else None
    result.holdings = pd.DataFrame({
        'Weight': weights,
        'Beta': [finite(asset_returns[key].cov(bench) / bench_var) if has_benchmark_variance else None for key in weights.index],
        'Annual volatility': np.sqrt(np.maximum(0., np.diag(covariance))),
        'Volatility contribution': weights * marginal / sigma if sigma > 1e-10 else np.nan,
        'Risk share': weights * marginal / variance if variance > 1e-20 else np.nan,
    })
    result.correlations = asset_returns.corr()
    result.status = 'complete' if result.valuation_complete and not result.excluded else 'partial'
    result.note = 'Today’s weights held constant across weekly historical returns; not personal historical performance.'
    if not has_benchmark_variance:
        result.note += ' Benchmark variance is zero; beta and benchmark correlation are undefined.'
    return result
