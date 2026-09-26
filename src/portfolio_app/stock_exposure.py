"""Company exposure with source-based exclusions and explicit coverage."""
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from portfolio_app.etf import fund_breakdown, matching_fund
from portfolio_app.holdings import DataError


@dataclass
class StockExposure:
    companies: pd.DataFrame
    sources: pd.DataFrame
    unresolved: pd.DataFrame
    stock_value: float | None
    whole_value: float | None


def load_company_identities(path: Path) -> dict[str, str]:
    """Optional explicit security:<ISIN>/instrument:<ID> to company mapping."""
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict) or any(not isinstance(k, str) or not k.startswith(('security:', 'instrument:'))
                                           or not isinstance(v, str) or not v.strip() for k, v in raw.items()):
            raise ValueError('Expected security:<ISIN> or instrument:<ID> keys and nonempty company IDs.')
        return raw
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise DataError(f'Invalid company identity mappings: {exc}') from exc


def stock_exposure(valued: pd.DataFrame, funds, *, excluded_buckets=(), identities=None) -> StockExposure:
    identities = identities or {}
    whole = float(valued.current_value_eur.sum()) if valued.current_value_eur.notna().all() else None
    selected = valued.loc[~valued.get('bucket_id', pd.Series('', index=valued.index)).isin(excluded_buckets)]
    known, unknown = [], []
    stock_total = 0.
    complete = True
    def identity(asset_id, isin):
        key = f'security:{isin}' if isin else f'instrument:{asset_id}'
        return identities.get(key, key)
    for position in selected.to_dict('records'):
        value = position['current_value_eur']
        if pd.notna(value) and value == 0:
            continue
        fund = matching_fund(position, funds)
        kind = position.get('instrument_type', 'unknown')
        declared = position.get('exposure_kind', 'unknown')
        if declared == 'non_equity' or (kind in {'crypto', 'physical', 'cash'} and fund is None):
            continue
        common = {'Source position': position['position_id'], 'Source instrument': position['name'],
                  'Bucket': position.get('bucket_id', ''), 'Account': position.get('account', '')}
        if pd.isna(value):
            unknown.append({**common, 'Exposure': position['name'], 'EUR value': float('nan'), 'Status': 'Missing valuation'})
            complete = False
            continue
        if fund is None:
            if kind == 'equity':
                stock_total += value
                known.append({**common, 'Company ID': identity(position['id'], position.get('isin', '')),
                              'Company': position['name'], 'EUR value': value, 'Origin': 'Direct'})
            else:
                if declared == 'equity':
                    stock_total += value
                unknown.append({**common, 'Exposure': position['name'], 'EUR value': value,
                                'Status': 'Unresolved equity' if declared == 'equity' else 'Unknown composition / unsupported instrument'})
                if declared != 'equity':
                    complete = False
            continue
        equity_universe = fund.equity_fund or declared == 'equity'
        for constituent in fund_breakdown(fund).to_dict('records'):
            amount = value * constituent['weight']
            residual = constituent['constituent_id'].startswith('etf-other:')
            constituent_kind = constituent.get('instrument_type')
            if not isinstance(constituent_kind, str) or not constituent_kind:
                constituent_kind = 'equity' if equity_universe else 'unknown'
            if residual:
                constituent_kind = 'equity' if equity_universe else 'unknown'
            if constituent_kind in {'cash', 'crypto', 'physical', 'non_equity'}:
                continue
            if constituent_kind == 'equity':
                stock_total += amount
            else:
                complete = False
            if residual or constituent_kind != 'equity':
                unknown.append({**common, 'Exposure': constituent['name'], 'EUR value': amount,
                                'Status': 'Unresolved equity' if constituent_kind == 'equity' else 'Unknown composition'})
            else:
                known.append({**common, 'Company ID': identity(constituent['constituent_id'], constituent['isin']),
                              'Company': constituent['name'], 'EUR value': amount, 'Origin': 'ETF-derived'})
    sources = pd.DataFrame(known, columns=['Source position', 'Source instrument', 'Bucket', 'Account', 'Company ID', 'Company', 'EUR value', 'Origin'])
    records = []
    for key, rows in sources.groupby('Company ID', sort=False):
        value = float(rows['EUR value'].sum())
        records.append({'Company ID': key, 'Company': rows.Company.iloc[0], 'Direct (EUR)': rows.loc[rows.Origin == 'Direct', 'EUR value'].sum(),
                        'ETF-derived (EUR)': rows.loc[rows.Origin == 'ETF-derived', 'EUR value'].sum(), 'Total (EUR)': value,
                        'Selected stock-universe %': 100 * value / stock_total if complete and stock_total else float('nan'),
                        'Whole-portfolio %': 100 * value / whole if whole else float('nan')})
    companies = pd.DataFrame(records, columns=['Company ID', 'Company', 'Direct (EUR)', 'ETF-derived (EUR)', 'Total (EUR)', 'Selected stock-universe %', 'Whole-portfolio %'])
    return StockExposure(companies.sort_values('Total (EUR)', ascending=False), sources,
                         pd.DataFrame(unknown), stock_total if complete else None, whole)
