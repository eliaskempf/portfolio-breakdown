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

from portfolio_app.exposure.taxonomy import copy_classifications
from portfolio_app.interface.shared.display_names import display_name
from portfolio_app.funds.etf import FundSnapshot, constituent_records, matching_fund
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.workspace.storage import save_document


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
    equity_isins = set()
    for fund in funds:
        frame = fund.constituents
        if 'instrument_type' in frame:
            equity_isins.update(filter(None, frame.loc[frame.instrument_type.eq('equity'), 'isin'].to_numpy(dtype=object)))
    held = {f.isin for r in holdings.to_dict('records') if (f := matching_fund(r, funds)) is not None}
    for fund in funds:
        if fund.isin not in held or fund.constituents.empty:
            continue
        source = display_name(fund.name) + (' (proxy)' if fund.proxy_source else '')
        as_of = str(fund.as_of)
        fields = ['constituent_id', 'isin', 'name', 'ticker', 'weight']
        fields.extend(field for field in ('instrument_type', 'source_ticker') if field in fund.constituents)
        for row in constituent_records(fund.constituents[fields]):
            kind = row.get('instrument_type')
            legacy_equity = (not isinstance(kind, str) or not kind) and (
                fund.equity_fund or row['isin'] in equity_isins or identity(row['constituent_id'], row['isin']) in reviewed)
            if row['weight'] <= 0 or (kind != 'equity' and not legacy_equity):
                continue
            ticker = row.get('source_ticker', '') or row['ticker']
            items.append(dict(node=f"fund:{fund.isin}:{row['constituent_id']}", asset_id=row['constituent_id'], name=row['name'],
                              isin=row['isin'], ticker=ticker if isinstance(ticker, str) else '',
                              source=source, origin=fund.isin, date=as_of, fund=fund.isin))
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
        links = defaultdict(dict)
        labels = copy_classifications(classifications)
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
                source = None if member['node'].startswith('direct:') else member['fund']
                links[source][str(member['asset_id'])] = (asset, label)

        def annotated(frame, id_column, fund):
            result = frame.copy()
            matches = links.get(fund)
            if matches and len(frame):
                pairs = [matches.get(str(asset_id), ('', '')) for asset_id in frame[id_column].to_numpy(dtype=object)]
                result['analysis_asset_id'] = [asset for asset, _ in pairs]
                result['analysis_asset_name'] = [label for _, label in pairs]
            else:
                result['analysis_asset_id'] = ''
                result['analysis_asset_name'] = ''
            return result

        direct = annotated(holdings, 'id', None)
        snapshots = [replace(fund, constituents=annotated(fund.constituents, 'constituent_id', fund.isin)) for fund in funds]
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
    # Start with exact identity components. Keep their origins and reviewed issuer
    # alongside the union-find tree so checking a name never scans unrelated assets.
    representatives, origins, issuers = {}, defaultdict(set), {}
    names = defaultdict(list)
    for i, item in enumerate(items):
        key = identity(item['asset_id'], item['isin'])
        exact = ('reviewed', reviewed[key]) if key in reviewed else ('security', key)
        component = representatives.setdefault(exact, i)
        parents[i] = component
        origins[component].add(item['origin'])
        if key in reviewed:
            issuers[component] = reviewed[key]
        name = normalized_name(item['name'])
        if len(name) >= 4:
            names[name].append(i)
    estimated = set()
    for indices in names.values():
        components = {root(i) for i in indices}
        if len(components) < 2:
            continue
        verified = {issuers[c] for c in components if c in issuers}
        if len(verified) > 1:
            continue
        # Check all transitive members, including aliases with a different name.
        # Grow the largest set in place only after every origin check passes.
        leader = max(components, key=lambda c: len(origins[c]))
        others = components - {leader}
        additional = set()
        for component in others:
            sources = origins[component]
            if not sources.isdisjoint(origins[leader]) or not sources.isdisjoint(additional):
                break
            additional.update(sources)
        else:
            origins[leader].update(additional)
            for component in others:
                parents[component] = leader
                del origins[component]
            if verified:
                issuers[leader] = next(iter(verified))
            # A single member marks every eventual superset as an estimated match.
            estimated.add(indices[0])
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
