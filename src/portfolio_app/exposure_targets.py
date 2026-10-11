"""Versioned exposure policy; never consumed by the capital trade optimizer."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math

import pandas as pd

from portfolio_app.holdings import DataError
from portfolio_app.portfolio_exposure import finite
from portfolio_app.storage import save_document

FILE = 'exposure-targets.json'


@dataclass(frozen=True)
class ExposureTargets:
    multiple: float
    policy: str
    composition: dict[str, float]
    overlay_bucket: str = ''
    version: int = 1
    universe: str = 'equity'
    weights: str = 'leaves'

    @property
    def field(self):
        return 'market_exposure_reporting' if self.universe == 'market' else 'exposure_reporting'


def leaf_composition(targets, allocation):
    """Parent-relative fractions multiply down the stable-ID ownership tree."""
    if targets.weights == 'leaves':
        return targets.composition
    result = {}
    def visit(parent, fraction):
        children = allocation.children(parent)
        if not children:
            result[parent] = fraction
        for child in children:
            visit(child.id, fraction * targets.composition[child.id])
    visit('', 1.)
    return result


def validate_targets(targets, allocation):
    from portfolio_app.allocation import validate_allocation
    validate_allocation(allocation)
    if type(targets.version) is not int or targets.version not in (1, 2) or not finite(targets.multiple) or targets.multiple < 0:
        raise DataError('Exposure targets require a supported version and a nonnegative finite total multiple.')
    if targets.universe not in {'equity', 'market'} or targets.weights not in {'leaves', 'parent_relative'}:
        raise DataError('Choose an explicit equity/market universe and leaf/parent-relative weights.')
    if targets.version == 1 and (targets.universe != 'equity' or targets.weights != 'leaves'):
        raise DataError('Version 1 policies retain equity-only leaf composition; conversion requires version 2.')
    if not isinstance(targets.composition, dict) or not isinstance(targets.overlay_bucket, str):
        raise DataError('Exposure composition must map stable bucket IDs to fractions.')
    leaves = allocation.leaves()
    if targets.policy == 'composition':
        if any(not finite(v) or not 0 <= v <= 1 for v in targets.composition.values()):
            raise DataError('Exposure composition needs nonnegative fractions between zero and one.')
        if targets.weights == 'leaves':
            if not targets.composition or set(targets.composition) - leaves or not math.isclose(sum(targets.composition.values()), 1., abs_tol=1e-9):
                raise DataError('Exposure composition must reference existing leaf IDs and sum to 100%, with nonnegative fractions.')
        else:
            if set(targets.composition) != {b.id for b in allocation.buckets}:
                raise DataError('Set a parent-relative exposure target for every stable category ID.')
            for parent in {b.parent for b in allocation.buckets}:
                if not math.isclose(sum(targets.composition[b.id] for b in allocation.children(parent)), 1., abs_tol=1e-9):
                    raise DataError('Exposure targets among each group of siblings must total 100%; review reparented categories.')
        if targets.overlay_bucket:
            raise DataError('Whole-equity composition must not include an overlay-only bucket.')
    elif targets.policy == 'overlay_only':
        if targets.overlay_bucket not in leaves or targets.composition:
            raise DataError('Overlay-only policy needs one existing leaf and no composition targets.')
    else:
        raise DataError('Choose whole-equity composition or overlay-only policy explicitly.')


def load_exposure_targets(directory, allocation):
    path = directory / FILE
    if not path.exists():
        return None, None
    try:
        data = path.read_bytes()
        raw = json.loads(data)
        target = ExposureTargets(**raw) if raw is not None else None
        if target and allocation is None:
            raise DataError('Saved exposure targets require an allocation hierarchy. Restore allocation.yaml before continuing.')
        if target:
            validate_targets(target, allocation)
        return target, sha256(data).hexdigest()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise DataError(f'Invalid exposure targets: {exc}') from exc


def save_exposure_targets(directory, targets, allocation, expected_revision):
    if targets:
        validate_targets(targets, allocation)
    save_document(directory / FILE, json.dumps(asdict(targets) if targets else None, indent=2) + '\n', expected_revision)


def exposure_gaps(result, targets, allocation, *, parent=None):
    validate_targets(targets, allocation)
    if not result.denominator or not result.exposure_complete:
        raise DataError('Complete positive net equity and exposure coverage are required for exposure gaps.')
    if result.exposures[targets.field].isna().any():
        raise DataError('Unknown exposure amounts prevent complete target gaps.')
    grouped = result.exposures.groupby('bucket_id')[targets.field].sum().to_dict()
    keys = sorted(allocation.leaves() | set(grouped))
    desired = {key: 0. for key in keys}
    total = targets.multiple * result.denominator
    if targets.policy == 'composition':
        desired.update({key: total * fraction for key, fraction in leaf_composition(targets, allocation).items()})
    else:
        funded = result.exposures.loc[~result.exposures.source_type.eq('future')]
        desired.update(funded.groupby('bucket_id')[targets.field].sum().to_dict())
        desired[targets.overlay_bucket] += total - sum(desired.values())
        if any(v < -1e-9 for v in desired.values()):
            raise DataError('This overlay-only target requires a negative category target; signed targets are not supported.')
    names = {b.id: b.name for b in allocation.buckets} | {'': 'Unassigned'}
    memberships = {key: {key} for key in keys}
    if parent is not None:
        if parent and parent not in names:
            raise DataError('Unknown target hierarchy selection.')
        children = allocation.children(parent)
        keys = [b.id for b in children] if children else [parent]
        memberships = {key: allocation.leaves(key) for key in keys}
        if not parent and '' in grouped:
            keys.append('')
            memberships[''] = {''}
    actual_total = sum(grouped.values())
    records = []
    for key in keys:
        current = sum(grouped.get(leaf, 0.) for leaf in memberships[key])
        target = sum(desired.get(leaf, 0.) for leaf in memberships[key])
        current_composition = 100*current/actual_total if actual_total > 0 else float('nan')
        desired_composition = 100*target/total if total > 0 else float('nan')
        records.append({'Bucket ID': key, 'Category': names.get(key, 'Unknown category'), f'Current exposure ({result.reporting_currency})': current,
                        f'Desired exposure ({result.reporting_currency})': target, f'Gap ({result.reporting_currency})': target-current,
                        'Current (% equity)': 100*current/result.denominator,
                        'Desired (% equity)': 100*target/result.denominator,
                        'Gap (pp)': 100*(target-current)/result.denominator,
                        'Current composition (%)': current_composition,
                        'Desired composition (%)': desired_composition,
                        'Composition gap (pp)': desired_composition-current_composition,
                        'Coverage': 'Unassigned' if not key else 'Unresolved constituents' if result.exposures.loc[result.exposures.bucket_id.isin(memberships[key]), 'status'].str.contains('Unresolved|Unknown', case=False).any() else 'Known'})
    return pd.DataFrame(records)
