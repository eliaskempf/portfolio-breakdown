"""Reviewable company groups across direct equities and multiple ETF sources.

Exact securities and reviewed issuer mappings take precedence. Optional name
estimates require an exact normalized full name and an unambiguous source set;
substring/fuzzy matching is deliberately excluded.
"""
from collections import defaultdict
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
import re
import unicodedata

import pandas as pd
import yaml

from portfolio_app.display_names import display_name
from portfolio_app.etf import FundSnapshot, matching_fund
from portfolio_app.holdings import DataError
from portfolio_app.storage import save_document


@dataclass
class MergeSettings:
    disabled: set[str]
    revision: str | None = None


def load_settings(path: Path) -> MergeSettings:
    try:
        content = path.read_bytes() if path.exists() else None
        raw = yaml.safe_load(content) if content else {}
        if not isinstance(raw, dict) or set(raw) - {'disabled'}:
            raise ValueError('Expected disabled source identities')
        disabled = raw.get('disabled', [])
        if not isinstance(disabled, list) or any(not isinstance(x, str) or not x.startswith(('fund:', 'direct:')) for x in disabled):
            raise ValueError('Invalid disabled source identities')
        return MergeSettings(set(disabled), sha256(content).hexdigest() if content is not None else None)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise DataError(f'Invalid company merge preferences: {exc}') from exc


def save_settings(path: Path, settings: MergeSettings) -> None:
    save_document(path, yaml.safe_dump({'disabled': sorted(settings.disabled)}), settings.revision)


def load_company_names(path: Path) -> dict[str, str]:
    """Optional reviewed issuer labels, distinct from original security names."""
    if not path.exists():
        return {}
    try:
        names = yaml.safe_load(path.read_text())
        if not isinstance(names, dict) or any(not isinstance(k, str) or not k.strip()
                                             or not isinstance(v, str) or not v.strip() for k, v in names.items()):
            raise ValueError('Expected company IDs and nonempty company names')
        return names
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise DataError(f'Invalid company names: {exc}') from exc


def normalized_name(name: str) -> str:
    name = unicodedata.normalize('NFKC', name).casefold().replace('&', ' and ')
    name = re.sub(r'[^\w\s]', ' ', name)
    words = name.split()
    # Keep country/subsidiary/share-class qualifiers and distinctive words.
    legal = {'inc', 'incorporated', 'corp', 'corporation', 'ltd', 'limited', 'plc', 'ag', 'nv', 'sa', 'se', 'co'}
    while words and words[-1] in legal:
        words.pop()
    return ' '.join(words)


def identity(asset_id: str, isin: str) -> str:
    return f'security:{isin}' if isin else f'instrument:{asset_id}'


def inventory(holdings: pd.DataFrame, funds: list[FundSnapshot], reviewed: dict) -> list[dict]:
    items = []
    for row in holdings.drop_duplicates('id').to_dict('records'):
        if row.get('instrument_type') == 'equity':
            items.append(dict(node=f"direct:{row['id']}", asset_id=row['id'], name=row['name'], isin=row.get('isin', ''),
                              ticker=row.get('ticker', ''), source='Direct holdings', origin='direct', date='', fund=''))
    equity_isins = {r['isin'] for f in funds for r in f.constituents.to_dict('records') if r.get('instrument_type') == 'equity' and r['isin']}
    held = {f.isin for r in holdings.to_dict('records') if (f := matching_fund(r, funds)) is not None}
    for fund in funds:
        if fund.isin not in held:
            continue
        for row in fund.constituents.to_dict('records'):
            kind = row.get('instrument_type')
            key = identity(row['constituent_id'], row['isin'])
            legacy_equity = (not isinstance(kind, str) or not kind) and (fund.equity_fund or row['isin'] in equity_isins or key in reviewed)
            if row['weight'] <= 0 or (kind != 'equity' and not legacy_equity):
                continue
            ticker = row.get('source_ticker') or row['ticker']
            items.append(dict(node=f"fund:{fund.isin}:{row['constituent_id']}", asset_id=row['constituent_id'], name=row['name'],
                              isin=row['isin'], ticker=ticker if isinstance(ticker, str) else '',
                              source=display_name(fund.name) + (' (proxy)' if fund.proxy_source else ''),
                              origin=fund.isin, date=str(fund.as_of), fund=fund.isin))
    return items


@dataclass
class CompanyGroup:
    key: str
    name: str
    members: list[dict]
    asset_id: str
    basis: str
    enabled: bool


@dataclass
class MergePlan:
    groups: list[CompanyGroup]

    def apply(self, holdings: pd.DataFrame, funds: list[FundSnapshot], classifications: dict):
        """Annotate analysis copies; original identities and positions stay intact."""
        links = {}
        labels = {asset: dict(paths) for asset, paths in classifications.items()}
        for group in self.groups:
            name = group.name + (' *' if group.basis == 'Estimated name match' and group.enabled else '')
            if group.enabled:
                combined = labels.setdefault(group.asset_id, {})
                # Canonical local labels win; fill only missing taxonomies.
                for member in group.members:
                    for taxonomy, paths in classifications.get(member['asset_id'], {}).items():
                        combined.setdefault(taxonomy, paths)
            for member in group.members:
                asset = group.asset_id if group.enabled else ('separate:' + member['node'] if member['fund'] else member['asset_id'])
                label = name if group.enabled else display_name(member['name'])
                if not group.enabled:
                    labels.setdefault(asset, dict(classifications.get(member['asset_id'], {})))
                links[member['node']] = (asset, label)
        direct = holdings.copy()
        direct['analysis_asset_id'] = [links.get(f'direct:{r.id}', ('', ''))[0] for r in direct.itertuples()]
        direct['analysis_asset_name'] = [links.get(f'direct:{r.id}', ('', ''))[1] for r in direct.itertuples()]
        snapshots = []
        for fund in funds:
            frame = fund.constituents.copy()
            pairs = [links.get(f'fund:{fund.isin}:{r.constituent_id}', ('', '')) for r in frame.itertuples()]
            frame['analysis_asset_id'] = [p[0] for p in pairs]
            frame['analysis_asset_name'] = [p[1] for p in pairs]
            snapshots.append(replace(fund, constituents=frame))
        return direct, snapshots, labels


def build_plan(holdings: pd.DataFrame, funds: list[FundSnapshot], reviewed: dict, settings: MergeSettings,
               classifications: dict | None = None, company_names: dict[str, str] | None = None) -> MergePlan:
    items = inventory(holdings, funds, reviewed)
    parents = list(range(len(items)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    def join(indices):
        roots = sorted({root(i) for i in indices})
        for r in roots[1:]:
            parents[r] = roots[0]
    keys = defaultdict(list)
    for i, item in enumerate(items):
        key = identity(item['asset_id'], item['isin'])
        keys[('reviewed', reviewed[key]) if key in reviewed else ('security', key)].append(i)
    for indices in keys.values():
        join(indices)
    names = defaultdict(list)
    for i, item in enumerate(items):
        name = normalized_name(item['name'])
        if len(name) >= 4:
            names[name].append(i)
    estimated = set()
    for indices in names.values():
        components = {root(i) for i in indices}
        if len(components) < 2:
            continue
        members = [i for i in range(len(items)) if root(i) in components]
        per_source = defaultdict(set)
        verified = set()
        for i in members:
            per_source[items[i]['origin']].add(root(i))
            key = identity(items[i]['asset_id'], items[i]['isin'])
            if key in reviewed:
                verified.add(reviewed[key])
        # Two different rows in one fund or conflicting reviewed companies make
        # the name ambiguous. Do not join them even via a third provider.
        if len(verified) > 1 or any(len(parts) > 1 for parts in per_source.values()):
            continue
        join(members)
        estimated.update(members)
    groups = defaultdict(list)
    for i in range(len(items)):
        groups[root(i)].append(i)
    result = []
    for indices in groups.values():
        if len(indices) < 2:
            continue
        members = [items[i] for i in indices]
        canonical = min(members, key=lambda m: (m['origin'] != 'direct', m['asset_id'] not in (classifications or {}),
                                                not bool(m['isin']), m['asset_id'], m['node']))
        is_estimate = bool(set(indices) & estimated)
        has_review = any(identity(m['asset_id'], m['isin']) in reviewed for m in members)
        basis = 'Estimated name match' if is_estimate else 'Reviewed company mapping' if has_review else 'Same security identity'
        enabled = not any(m['node'] in settings.disabled for m in members)
        key = sha256('\0'.join(sorted(m['node'] for m in members)).encode()).hexdigest()[:20]
        issuers = {reviewed[identity(m['asset_id'], m['isin'])] for m in members
                   if identity(m['asset_id'], m['isin']) in reviewed}
        issuer_name = (company_names or {}).get(next(iter(issuers)), '') if len(issuers) == 1 else ''
        result.append(CompanyGroup(key, issuer_name or display_name(canonical['name']), members, canonical['asset_id'], basis, enabled))
    return MergePlan(sorted(result, key=lambda g: (g.name.casefold(), g.key)))
