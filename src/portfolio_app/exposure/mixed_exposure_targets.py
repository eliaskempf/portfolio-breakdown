"""Disjoint exposure targets for categories and directly owned source positions."""
import json
import math

import pandas as pd

from portfolio_app.exposure.portfolio_exposure import finite
from portfolio_app.portfolio.holdings import DataError


def target_key(category, position=''):
    return json.dumps(['position', position] if position else ['category', category])


def validate_mixed_targets(targets, allocation):
    if targets.version != 3 or targets.policy != 'composition' or targets.overlay_bucket:
        raise DataError('Mixed exposure weights require a version 3 composition policy.')
    categories = {b.id for b in allocation.buckets}
    if set(targets.composition) != categories or set(targets.members) - categories:
        raise DataError('Set an exposure target for every category; review removed or moved categories.')
    seen = set()
    for owner, members in targets.members.items():
        if not isinstance(members, dict) or not members or allocation.flexible(owner):
            raise DataError('Flexible categories use one combined exposure target; member targets belong to fixed categories.')
        if any(not isinstance(pid, str) or not pid or pid in seen for pid in members):
            raise DataError('Investment exposure targets require unique source-position identities.')
        seen.update(members)
    values = [*targets.composition.values(), *(v for members in targets.members.values() for v in members.values())]
    if any(not finite(v) or not 0 <= v <= 1 for v in values):
        raise DataError('Exposure targets must be finite nonnegative fractions between zero and one.')
    for parent in {'', *categories}:
        children = allocation.children(parent)
        members = targets.members.get(parent, {})
        if (children or members) and not math.isclose(
                sum(targets.composition[b.id] for b in children) + sum(members.values()), 1., abs_tol=1e-9):
            raise DataError('Exposure targets for direct investments and subcategories within each parent must total 100%.')


def composition_parts(targets, allocation):
    """Map terminal target IDs to (owner, source position or empty, global share)."""
    parts = {}
    def visit(parent, fraction):
        children = allocation.children(parent)
        members = targets.members.get(parent, {})
        if parent and not children and not members:
            parts[target_key(parent)] = (parent, '', fraction)
        for pid, share in members.items():
            parts[target_key(parent, pid)] = (parent, pid, fraction * share)
        for child in children:
            visit(child.id, fraction * targets.composition[child.id])
    visit('', 1.)
    return parts


def source_target_key(targets, category, position):
    """The planner and observed exposure use exactly the same disjoint cell."""
    return target_key(category, position if category in targets.members else '')


def validate_source_membership(result, targets, allocation):
    sources = result.exposures[['source_position_id', 'bucket_id']]
    if {'source_position_id', 'bucket_id'} <= set(result.capital):
        sources = pd.concat([sources, result.capital[['source_position_id', 'bucket_id']]])
    sources = sources.drop_duplicates()
    owners = sources.groupby('source_position_id').bucket_id.agg(set).to_dict()
    for owner, members in targets.members.items():
        if any(owners.get(pid) != {owner} for pid in members):
            raise DataError('Review exposure targets: a targeted investment was moved or removed.')
    internal = {b.parent for b in allocation.buckets if b.parent}
    for row in sources.itertuples():
        if row.bucket_id in internal or row.bucket_id in targets.members:
            if row.source_position_id not in targets.members.get(row.bucket_id, {}):
                raise DataError('Set explicit exposure targets for every direct investment beside subcategories, including zeros.')


def mixed_exposure_gaps(result, targets, allocation, *, parent=None):
    validate_source_membership(result, targets, allocation)
    parts = composition_parts(targets, allocation)
    rows = result.exposures.copy()
    rows['_target'] = [source_target_key(targets, row.bucket_id, row.source_position_id) for row in rows.itertuples()]
    grouped = rows.groupby('_target')[targets.field].sum().to_dict()
    unresolved = set(rows.loc[rows.status.str.contains('Unresolved|Unknown', case=False), '_target'])
    source_names = dict(zip(result.capital.get('source_position_id', []), result.capital.get('name', [])))
    source_names.update({row.source_position_id: source_names.get(row.source_position_id, row.asset_name)
                         for row in rows.itertuples()})
    # Preserve unassigned exposure and empty planned categories as explicit cells.
    for key, owner in zip(rows['_target'], rows.bucket_id):
        parts.setdefault(key, (owner, '', 0.))
    names = {b.id: b.name for b in allocation.buckets} | {'': 'Unassigned'}
    memberships = {key: [key] for key in parts}
    owners = {key: (owner, pid) for key, (owner, pid, _) in parts.items()}
    if parent is not None:
        if parent and parent not in names:
            raise DataError('Unknown target hierarchy selection.')
        children = allocation.children(parent)
        memberships, owners = {}, {}
        for child in children:
            key = target_key(child.id)
            descendants = allocation.descendants(child.id)
            memberships[key] = [k for k, (owner, _, _) in parts.items() if owner in descendants]
            owners[key] = (child.id, '')
        for key, (owner, pid, _) in parts.items():
            if owner == parent:
                memberships[key] = [key]
                owners[key] = (owner, pid)
    total = targets.multiple * result.denominator
    actual_total = sum(grouped.values())
    records = []
    for key, members in memberships.items():
        owner, pid = owners[key]
        current = sum(grouped.get(k, 0.) for k in members)
        desired = total * sum(parts[k][2] for k in members)
        current_share = current * 100 / actual_total if actual_total > 0 else float('nan')
        target_share = desired * 100 / total if total > 0 else float('nan')
        records.append({'Bucket ID': key, 'Owner ID': owner, 'Position ID': pid,
            'Category': source_names.get(pid, pid) if pid else names.get(owner, 'Unknown category'),
            f'Current exposure ({result.reporting_currency})': current,
            f'Desired exposure ({result.reporting_currency})': desired,
            f'Gap ({result.reporting_currency})': desired-current,
            'Current (% equity)': current*100/result.denominator,
            'Desired (% equity)': desired*100/result.denominator,
            'Gap (pp)': (desired-current)*100/result.denominator,
            'Current composition (%)': current_share, 'Desired composition (%)': target_share,
            'Composition gap (pp)': target_share-current_share,
            'Coverage': 'Unassigned' if not owner else 'Unresolved constituents' if any(k in unresolved for k in members) else 'Known'})
    return pd.DataFrame(records)
