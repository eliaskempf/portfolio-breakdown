"""Shared constituent facts; consumers retain their own coverage policies.

A known equity amount does not imply a resolved company. Original security
identity and reviewed analytical identity remain separate. No amounts, portfolio
denominators, provider I/O or company-equivalence decisions belong here.
"""
from dataclasses import dataclass
from typing import Mapping, Any

NON_EQUITY_KINDS = frozenset({'cash', 'bond', 'money_market', 'overnight_rate', 'non_equity', 'crypto', 'physical'})


def text(value) -> str:
    return value if isinstance(value, str) else ''


def fund_kind(fund, *, equity_declared=False) -> str:
    return ('equity' if fund.equity_fund or equity_declared else 'non_equity'
            if fund.asset_class in {'fixed_income', 'money_market'} else 'unknown')


@dataclass(frozen=True)
class Constituent:
    security_id: str
    name: str
    isin: str
    ticker: str
    weight: float
    residual: bool
    explicit_kind: str
    kind: str
    exposure_kind: str
    analysis_asset_id: str
    analysis_asset_name: str

    @property
    def nested_equity(self) -> bool:
        return self.kind == 'etf' and self.exposure_kind == 'equity'

    @property
    def proxy_company_known(self) -> bool:
        # An equity-index proxy may omit kinds, but a named non-equity or nested
        # fund row must not become a resolved company in that index.
        return not self.residual and self.explicit_kind in {'', 'equity'}


def interpret_constituent(row: Mapping[str, Any], *, fallback_kind: str) -> Constituent:
    explicit = text(row.get('instrument_type'))
    kind = explicit or fallback_kind
    declared = text(row.get('exposure_kind')) or (
        'equity' if kind == 'equity' else 'non_equity' if kind in NON_EQUITY_KINDS else 'unknown')
    return Constituent(row['constituent_id'], row['name'], row['isin'], row['ticker'], float(row['weight']),
                       row['constituent_id'].startswith('etf-other:'), explicit, kind, declared,
                       text(row.get('analysis_asset_id')), text(row.get('analysis_asset_name')))
