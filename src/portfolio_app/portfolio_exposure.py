"""Recorded wealth and signed market / equity exposure, independent of UI/providers.

`value` remains funded value. `exposure_reporting` is the deliberately separate
measure used for exposure aggregation. Gross is calculated before any merging.
"""
from dataclasses import dataclass
import math

import pandas as pd

from portfolio_app.allocation import Allocation
from portfolio_app.etf import constituent_resolver, fund_breakdown, matching_fund
from portfolio_app.futures import CONTRACTS, require_eur_reporting
from portfolio_app.constituents import NON_EQUITY_KINDS, fund_kind, interpret_constituent


@dataclass(frozen=True)
class PortfolioExposure:
    capital: pd.DataFrame
    exposures: pd.DataFrame
    owned_assets: float | None
    liabilities: float | None
    settlement_adjustment: float
    net_equity: float | None
    issues: tuple[str, ...]
    exposure_complete: bool
    futures_capital_value: float = 0.
    reporting_currency: str = 'EUR'

    @property
    def denominator(self):
        return self.net_equity if self.net_equity is not None and self.net_equity > 0 else None

    @property
    def net_exposure(self):
        return float(self.exposures.exposure_reporting.sum())

    @property
    def gross_exposure(self):
        return float(self.exposures.exposure_reporting.abs().sum())

    def ratio(self, amount):
        return amount / self.denominator if self.denominator and self.exposure_complete else None

    @property
    def net_multiple(self):
        return self.ratio(self.net_exposure)

    @property
    def gross_multiple(self):
        return self.ratio(self.gross_exposure)

    def market_amount(self, *, gross=False, bucket_ids=None):
        rows = self.exposures
        if bucket_ids is not None:
            rows = rows.loc[rows.bucket_id.isin(bucket_ids)]
        values = rows.market_exposure_reporting
        return float((values.abs() if gross else values).sum())

    @property
    def net_market_multiple(self):
        return self.ratio(self.market_amount())

    @property
    def gross_market_multiple(self):
        return self.ratio(self.market_amount(gross=True))

    def selected_multiple(self, bucket_ids=None, *, gross=False, market=True):
        """Strategic scope capital; unassigned portfolio debt has no category split."""
        if bucket_ids is None:
            return self.ratio(self.market_amount(gross=gross) if market else
                              self.gross_exposure if gross else self.net_exposure)
        if self.liabilities is None or self.liabilities != 0:
            return None
        capital = self.capital.loc[self.capital.bucket_id.isin(bucket_ids)]
        rows = self.exposures.loc[self.exposures.bucket_id.isin(bucket_ids)]
        field = 'market_exposure_reporting' if market else 'exposure_reporting'
        denominator = capital.value.sum()
        if capital.value.isna().any() or rows[field].isna().any() or denominator <= 0:
            return None
        return float((rows[field].abs() if gross else rows[field]).sum() / denominator)

    @property
    def futures_multiple(self):
        return self.ratio(float(self.exposures.loc[self.exposures.source_type.eq('future'), 'exposure_reporting'].sum()))


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def calculate_portfolio_exposure(valued, funds=(), *, future=None, allocation=None, liabilities=(), fund_factors=(), reporting_currency=None):
    """No cash posting: cumulative mock settlement is a separate adjustment once.

    Liability balances are explicit positive EUR amounts owed (None = unknown).
    Fund factors are explicit, documented current daily equity factors. They
    replace parent exposure and do not manufacture shareholder debt.
    """
    if reporting_currency is None:
        require_eur_reporting(valued)
        reporting_currency = 'EUR'
    elif reporting_currency not in {'EUR', 'USD', 'GBP'} or valued.attrs.get('reporting_currency', reporting_currency) != reporting_currency or 'reporting_currency' not in valued or not valued.reporting_currency.eq(reporting_currency).all():
        from portfolio_app.holdings import DataError
        raise DataError('Exposure inputs require consistent reporting currency.')
    if reporting_currency != 'EUR' and (future is not None or liabilities):
        from portfolio_app.holdings import DataError
        raise DataError('Legacy futures and liabilities require EUR reporting.')
    capital, rows, issues = [], [], []
    complete = True
    leaves = allocation.leaves() if allocation else set()
    factors = {item.position_id: item for item in fund_factors}
    resolve = constituent_resolver(valued)
    def bucket(key):
        if key and key not in leaves:
            issues.append('An exposure assignment is no longer an existing leaf; shown as Unassigned.')
        return key if key in leaves else ''
    def add(common, asset_id, name, value, exposure, kind, status='', method='', as_of='', market=None):
        rows.append(dict(**common, asset_id=asset_id, asset_name=name, value=value,
                         exposure_reporting=exposure, market_exposure_reporting=exposure if market is None else market, source_type=kind, status=status, method=method, as_of=as_of))
    for position in valued.to_dict('records'):
        if position.get('shares', 1) == 0:
            continue
        pid = position['position_id']
        value = position['current_value_reporting']
        key = bucket(position.get('bucket_id', ''))
        capital.append(dict(source_position_id=pid, name=position['name'], bucket_id=key, value=value, kind='owned asset'))
        common = dict(source_position_id=pid, source_instrument=position['id'], bucket_id=key,
                      account=position.get('account', ''))
        kind = position.get('instrument_type', 'unknown')
        declared = position.get('exposure_kind', 'unknown')
        fund = matching_fund(position, funds)
        factor = factors.get(pid)
        if kind == 'liability':
            capital[-1]['kind'] = 'liability'
        if not finite(value):
            complete = False
            issues.append('Missing price or FX: recorded net equity and exposure ratios are unavailable.')
            add(common, position['id'], position['name'], float('nan'), float('nan'), 'instrument', 'Missing valuation')
            continue
        if kind == 'liability':
            capital[-1]['kind'] = 'liability'
            add(common, position['id'], position['name'], value, 0., 'liability', 'Funding', market=0.)
        elif kind == 'future':
            notional = position.get('signed_notional_reporting', float('nan'))
            if not finite(notional):
                complete = False
            proxy_id = position.get('proxy_position_id', '')
            candidates = valued.loc[valued.position_id.eq(proxy_id)]
            proxy = matching_fund(candidates.iloc[0].to_dict(), funds) if not candidates.empty else None
            if proxy is not None and proxy_id not in factors:
                method = f'ETF holdings proxy: {proxy.name}; {proxy.proxy_source or proxy.source}. Approximate index composition.'
                for constituent in fund_breakdown(proxy).to_dict('records'):
                    interpreted = interpret_constituent(constituent, fallback_kind=fund_kind(proxy))
                    known = interpreted.proxy_company_known
                    aid, name = resolve(constituent) if known else ('future-other:' + position['id'], position['name'] + ' · Unresolved proxy remainder')
                    weight = constituent['weight']
                    add(common, aid, name, value * weight, notional * weight, 'future',
                        'ETF holdings proxy' if known else 'Unresolved index exposure; proxy residual / non-equity', method, str(proxy.as_of))
            else:
                add(common, 'index:' + position.get('contract_id', position['id']), position['name'], value,
                    notional, 'future', 'Unresolved index constituents', position.get('price_source', ''), position.get('quote_date', ''))
        elif factor:
            if kind != 'etf':
                complete = False
                issues.append('A fund factor refers to a position that is no longer a fund.')
                add(common, position['id'], position['name'], value, float('nan'), 'instrument', 'Invalid fund adapter')
            elif factor.factor is None:
                complete = False
                add(common, position['id'], position['name'], value, float('nan'), 'instrument', 'Unknown embedded leverage')
            else:
                add(common, position['id'], position['name'], value, value * factor.factor, 'leveraged_fund',
                    'Approximate equity exposure; underlying constituents unresolved', factor.method, factor.as_of)
        elif kind in {'cash', 'crypto', 'physical'} or declared == 'non_equity':
            add(common, position['id'], position['name'], value, 0., 'instrument', 'Funding' if kind == 'cash' else 'Non-equity', market=0. if kind == 'cash' else value)
        elif kind == 'equity' and fund is None:
            add(common, position.get('analysis_asset_id') or position['id'], position.get('analysis_asset_name') or position['name'], value, value, 'instrument')
        elif fund:
            basis = fund_kind(fund, equity_declared=declared == 'equity')
            for constituent in fund_breakdown(fund).to_dict('records'):
                amount = value * constituent['weight']
                if not amount:
                    continue
                interpreted = interpret_constituent(constituent, fallback_kind=basis)
                residual, ck = interpreted.residual, interpreted.kind
                # Nested equity has known exposure, not resolved company coverage.
                nested_equity = interpreted.nested_equity
                # A missing constituent classification does not erase an
                # ordinary equity fund's value-based exposure. Keep that amount
                # as unresolved allocation, without calling it a company or
                # interpreting a right/warrant's value as its own delta/notional.
                # Explicit leverage adapters above still replace this basis.
                unresolved_allocation = basis == 'equity' and ck not in NON_EQUITY_KINDS and ck != 'equity' and not nested_equity
                exposure = amount if ck == 'equity' or nested_equity or unresolved_allocation else 0. if ck in NON_EQUITY_KINDS else float('nan')
                if not finite(exposure):
                    complete = False
                asset_id, name = resolve(constituent)
                method = fund.proxy_source or fund.source
                if unresolved_allocation:
                    asset_id, name = f'etf-other:{fund.fund_id}', f'{fund.name} / Other'
                    method += f'; Fund-value exposure basis; unresolved constituent: {constituent["name"]}'
                    residual = True
                add(common, asset_id, name, amount, exposure, 'etf_other' if residual else 'etf_constituent',
                    'Unresolved equity allocation; fund-value basis' if unresolved_allocation else 'Unresolved equity residual' if residual and ck == 'equity' else 'Unresolved equity fund' if nested_equity else 'Unknown composition' if not finite(exposure) else '',
                    method, str(fund.as_of),
                    market=amount if ck in {'bond', 'non_equity', 'crypto', 'physical'} else exposure)
        elif declared == 'equity':
            add(common, position['id'], position['name'], value, value, 'instrument', 'Unresolved equity fund')
        else:
            complete = False
            add(common, position['id'], position['name'], value, float('nan'), 'instrument', 'Unknown composition / embedded leverage coverage')
    held_ids = set(valued.position_id)
    if set(factors) - held_ids:
        issues.append('A fund adapter refers to a missing source position; review leverage settings.')
        complete = False
    assets_complete = all(finite(row['value']) for row in capital)
    assets = sum(row['value'] for row in capital if row['kind'] != 'liability') if assets_complete else None
    debt_rows = [row for row in capital if row['kind'] == 'liability']
    debt = -sum(row['value'] for row in debt_rows) if all(finite(row['value']) for row in debt_rows) else None
    for liability in liabilities:
        amount = liability.balance_eur
        if amount is None:
            debt = None
            issues.append('A recorded liability balance is missing; net equity is unavailable.')
        elif debt is not None:
            debt += amount
        capital.append(dict(source_position_id='liability:' + liability.id, name=liability.name,
                            bucket_id='', value=-amount if amount is not None else float('nan'), kind='liability'))
    funded = future is not None and future.reference_capital_eur is not None
    strategy_value = future.capital_value if funded else 0.
    settlement = future.pnl if future and not funded else 0.
    if future:
        from portfolio_app.future_projections import capital_contribution, exposure_source
        capital.append(capital_contribution(future, bucket(future.bucket_id)))
        if future.contracts or funded:
            common = exposure_source(future, bucket(future.bucket_id))
            from portfolio_app.futures_proxy import linked_fund
            proxy = linked_fund(future, valued, funds)
            if proxy and future.contracts and future.proxy_position_id not in factors:
                method = f'ETF holdings proxy: {proxy.name}; {proxy.proxy_source or proxy.source}. Approximate index composition.'
                for constituent in fund_breakdown(proxy).to_dict('records'):
                    weight = constituent['weight']
                    if not weight:
                        continue
                    interpreted = interpret_constituent(constituent, fallback_kind=fund_kind(proxy))
                    known = interpreted.proxy_company_known
                    asset_id, name = resolve(constituent) if known else (
                        'future-other:' + future.instrument, future.instrument + ' · Unresolved proxy remainder')
                    add(common, asset_id, name, strategy_value * weight, future.notional * weight, 'future',
                        'ETF holdings proxy' if known else 'Unresolved index exposure; proxy residual / non-equity',
                        method, str(proxy.as_of))
            else:
                add(common, 'index:' + future.instrument, CONTRACTS[future.instrument][0] + ' index · ' + future.instrument,
                    strategy_value, future.notional, 'future', 'Unresolved index constituents' if future.contracts else 'Funding; contract closed', future.price_source or 'Manual mock mark', future.quote_date)
    equity = assets + strategy_value + settlement - debt if assets is not None and debt is not None else None
    if not complete:
        issues.append('Incomplete market / equity exposure coverage; gross/net multiples and equity target gaps are unavailable.')
    if equity is not None and equity <= 0:
        issues.append('Net equity is zero or negative; exposure ratios are unavailable.')
    columns = ['source_position_id', 'source_instrument', 'bucket_id', 'account', 'asset_id', 'asset_name',
               'value', 'exposure_reporting', 'market_exposure_reporting', 'source_type', 'status', 'method', 'as_of']
    exposures = pd.DataFrame(rows, columns=columns)
    if reporting_currency == 'EUR':
        exposures['exposure_eur'] = exposures.exposure_reporting
        exposures['market_exposure_eur'] = exposures.market_exposure_reporting
    return PortfolioExposure(pd.DataFrame(capital, columns=['source_position_id', 'name', 'bucket_id', 'value', 'kind']),
                             exposures, assets, debt, settlement, equity,
                             tuple(dict.fromkeys(issues)), complete, strategy_value, reporting_currency)


def strategy_exposure(result, config: Allocation, bucket='', *, measure='exposure_reporting'):
    """Immediate child rollups; filtering never changes the equity denominator."""
    measure = {'exposure_eur': 'exposure_reporting', 'market_exposure_eur': 'market_exposure_reporting'}.get(measure, measure)
    children = [(b.id, b.name) for b in config.children(bucket)]
    selected = result.exposures
    if bucket:
        selected = selected.loc[selected.bucket_id.isin(config.leaves(bucket))] if bucket != 'unassigned' else selected.loc[selected.bucket_id.eq('')]
    if not bucket and selected.bucket_id.eq('').any():
        children.append(('unassigned', 'Unassigned'))
    records = []
    if children:
        groups = [(key, name, selected.loc[selected.bucket_id.eq('') if key == 'unassigned' else selected.bucket_id.isin(config.leaves(key))]) for key, name in children]
    else:
        source_names = dict(zip(result.capital.source_position_id, result.capital.name))
        groups = [(key, source_names.get(key, group.asset_name.iloc[0]), group)
                  for key, group in selected.groupby('source_position_id', sort=False)]
    for key, name, group in groups:
        amount = float(group[measure].sum()) if group[measure].notna().all() else float('nan')
        records.append({'ID': key, 'Category': name, 'Exposure (EUR)': amount,
                        '% of net equity': 100 * amount / result.denominator if result.denominator and result.exposure_complete else float('nan'),
                        'Gross exposure (EUR)': float(group[measure].abs().sum()) if group[measure].notna().all() else float('nan')})
    return pd.DataFrame(records, columns=['ID', 'Category', 'Exposure (EUR)', '% of net equity', 'Gross exposure (EUR)'])
