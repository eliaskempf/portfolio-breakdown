"""Risk of constant current weights, from synchronized weekly reporting returns."""
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from portfolio_app.currencies import quote_unit

from portfolio_app.fundamentals import finite

DEFAULT_BENCHMARK = 'MSCI:892400:NETR'
MIN_OBSERVATIONS = 52


def daily_prices(series: pd.Series) -> pd.Series:
    """Keep local trading dates, not UTC dates shifted across a market close."""
    result = pd.to_numeric(series, errors='coerce').copy()
    result.index = pd.DatetimeIndex(result.index).tz_localize(None).normalize()
    result = result.loc[~result.index.duplicated(keep='last')].sort_index()
    return result.where(np.isfinite(result) & result.gt(0)).dropna()


def eur_prices(prices, currency, fx=None):
    currency, factor = quote_unit(currency)
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
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    # Resampling retains empty weeks; pct_change must not fill their endpoints.
    weekly = prices.resample('W-FRI').last()
    weekly = weekly.loc[weekly.index < today]
    return weekly.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)


def native_future_weekly_returns(prices, *, fx=None, current_fx=1., today=None):
    """Translate native variation, never the return of FX-valued full notional.

    Replay each native weekly move against today's native notional and convert
    that variation at the observed week-end FX. The result is expressed per unit
    of today's reporting-currency notional. Separate cash/claim/debt histories
    carry their own FX returns. This is a fixed-notional risk approximation, not
    a reconstruction of trading or settlement cash through time.
    """
    if finite(current_fx) is None or current_fx <= 0:
        raise ValueError('A positive current reporting FX rate is required for futures risk.')
    returns = weekly_returns(prices, today=today)
    if fx is not None:
        prices = daily_prices(prices)
        aligned_fx = daily_prices(fx).reindex(prices.index)
        # Use the FX on the actual last index observation of each week. last()
        # would silently substitute an earlier FX when the closing FX is absent.
        endpoints = aligned_fx.resample('W-FRI').apply(lambda values: values.iloc[-1] if len(values) else np.nan)
        returns = returns * endpoints.reindex(returns.index) / current_fx
    return returns


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
    integrated: bool = False
    denominator: float | None = None
    scoped: bool = False
    portfolio_beta_contribution: float | None = None


def risk_analytics(valued, histories: dict[str, pd.Series], benchmark: pd.Series,
                   *, today=None, years=3, failures=None, exposures=None, net_equity=None,
                   return_series=None, scoped=False, subset_capital=None) -> RiskAnalytics:
    """Whole-portfolio risk uses net equity; standalone subsets use their own base.

    Fund price histories already include embedded leverage. Their weights remain
    funded values; only derivatives use separately supplied signed notionals.
    Ordinary subsets normalize covered funded values. Derivative subsets require
    explicit selected capital, supplied before risk-only claim rows are added.
    """
    if years not in (1, 3, 5):
        raise ValueError('Risk window must be 1, 3 or 5 years')
    target = valued.reporting_currency.iloc[0] if len(valued) and 'reporting_currency' in valued else 'EUR'
    held = valued.loc[valued.shares.gt(0)]
    valid = held.current_value_reporting.map(finite).notna() & held.current_value_reporting.ge(0)
    integrated = exposures is not None
    if integrated:
        valid = held.current_value_reporting.map(finite).notna()
    result = RiskAnalytics(known_value=float(held.loc[valid, 'current_value_reporting'].sum()),
                           valuation_complete=bool(valid.all()), integrated=integrated,
                           denominator=net_equity if integrated else None, scoped=scoped)
    equity_valid = finite(net_equity) is not None and net_equity > 0
    if integrated and not scoped and not equity_valid:
        result.note = 'Positive, complete portfolio net equity is required for integrated risk.'
        return result
    end = pd.Timestamp(today or date.today()).normalize()
    start = end - pd.DateOffset(years=years)
    bench = weekly_returns(benchmark, today=end).loc[lambda s: s.index >= start].rename('__benchmark__')
    # Listing-level histories are fetched once; calculation identities remain instrument IDs.
    returns, values = {}, {}
    for identity, rows in held.groupby('id', sort=False):
        label = str(rows.name.iloc[0])
        if rows.current_value_reporting.map(finite).isna().any():
            result.excluded[identity] = f'{label}: missing current valuation'
            continue
        value = exposures.get(identity, float(rows.current_value_reporting.sum())) if integrated else float(rows.current_value_reporting.sum())
        if finite(value) is None:
            result.excluded[identity] = f'{label}: missing risk exposure'
            continue
        if value <= 0 and not integrated:
            continue
        cash = rows.get('instrument_type', pd.Series('', index=rows.index)).isin(['cash', 'liability']).all() and rows.quote_currency.eq(target).all()
        if cash or (integrated and value == 0):
            series = pd.Series(0., index=bench.index)
        elif identity in (return_series or {}):
            series = return_series[identity]
            if (not isinstance(series.index, pd.DatetimeIndex) or series.index.has_duplicates
                    or series.index.tz is not None or not series.index.is_monotonic_increasing):
                raise ValueError('Explicit risk returns require ordered unique weekly dates.')
            series = pd.to_numeric(series, errors='coerce').replace([np.inf, -np.inf], np.nan).reindex(bench.index)
        elif identity in histories:
            series = weekly_returns(histories[identity], today=end).reindex(bench.index)
        else:
            result.excluded[identity] = f'{label}: {(failures or {}).get(identity, "market history unavailable")}'
            continue
        if pd.concat([series, bench], axis=1).dropna().shape[0] < MIN_OBSERVATIONS:
            result.excluded[identity] = f'{label}: fewer than 52 matched weekly returns'
            continue
        returns[identity], values[identity] = series, value
    result.covered_value = sum(abs(value) for value in values.values()) if integrated else sum(values.values())
    if integrated:
        result.known_value = sum(abs(exposures.get(identity, float(rows.current_value_reporting.sum())))
                                 for identity, rows in held.loc[valid].groupby('id')
                                 if finite(exposures.get(identity, float(rows.current_value_reporting.sum()))) is not None)
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
    has_derivatives = held.get('instrument_type', pd.Series('', index=held.index)).eq('future').any()
    if scoped:
        denominator = subset_capital if has_derivatives else sum(values.values())
    else:
        denominator = net_equity if integrated else result.covered_value
    denominator_valid = finite(denominator) is not None and denominator > 0
    result.denominator = float(denominator) if denominator_valid else None
    # A unit base lets us retain a meaningful portfolio beta contribution even
    # when a bare contract has no standalone capital base. Never display that
    # temporary base as a standalone return or allocation.
    calculation_base = denominator if denominator_valid else 1.
    weights = pd.Series(values) / calculation_base
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
    if scoped and equity_valid and result.beta is not None:
        result.portfolio_beta_contribution = result.beta * calculation_base / net_equity
    result.holdings = pd.DataFrame({
        'Weight': weights,
        'Beta': [finite(asset_returns[key].cov(bench) / bench_var) if has_benchmark_variance else None for key in weights.index],
        'Annual volatility': np.sqrt(np.maximum(0., np.diag(covariance))),
        'Volatility contribution': weights * marginal / sigma if sigma > 1e-10 else np.nan,
        'Risk share': weights * marginal / variance if variance > 1e-20 else np.nan,
    })
    if integrated:
        result.holdings['Beta contribution'] = result.holdings.Weight * result.holdings.Beta
    result.correlations = asset_returns.corr()
    result.status = 'complete' if result.valuation_complete and not result.excluded else 'partial'
    result.note = 'Today’s weights held constant across weekly historical returns; not personal historical performance.'
    if not has_benchmark_variance:
        result.note += ' Benchmark variance is zero; beta and benchmark correlation are undefined.'
    if scoped and result.status != 'complete':
        basis = ('Covered positions retain weights against selected capital, including futures notionals.' if has_derivatives
                 else 'Weights are renormalized within the selected holdings with usable history.')
        result.note = 'Approximate risk of covered holdings only; exclusions may increase or decrease total risk. ' + basis + ' ' + result.note
    elif integrated and result.status != 'complete':
        result.note = ('Approximate risk of covered holdings only; excluded holdings may increase or decrease total risk. '
                       'Covered holdings retain their weights against whole-portfolio net equity, including futures notionals; '
                       'weights are not renormalized. ' + result.note)
    if not denominator_valid:
        result.beta = result.volatility = result.correlation = None
        result.holdings[['Weight', 'Volatility contribution']] = np.nan
        if 'Beta contribution' in result.holdings:
            result.holdings['Beta contribution'] = np.nan
        result.status = 'unavailable'
        result.note = ('Standalone subset risk requires positive, fully valued selected capital. '
                       'Include the capital backing the future or select its funded investment; '
                       'contract notional, margin requirements and unsettled gains alone are not a capital base.'
                       if has_derivatives else 'Standalone subset risk requires positive covered net capital.')
    return result
