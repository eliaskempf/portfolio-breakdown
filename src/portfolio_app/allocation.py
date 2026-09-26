"""Non-overlapping allocation ownership and parent-relative budgets."""
from dataclasses import dataclass
from io import StringIO
import math
from pathlib import Path
from uuid import uuid4

import pandas as pd
import yaml

from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.positions import EMPTY_CSV
from portfolio_app.storage import revision, save_document


@dataclass(frozen=True)
class Bucket:
    id: str
    name: str
    parent: str = ''
    target: float | None = None
    sell_protected: bool = False


@dataclass(frozen=True)
class Allocation:
    buckets: tuple[Bucket, ...]
    version: int = 2

    def children(self, parent: str = '') -> list[Bucket]:
        return [b for b in self.buckets if b.parent == parent]

    def leaves(self, parent: str = '') -> set[str]:
        children = self.children(parent)
        return set().union(*(self.leaves(b.id) for b in children)) if children else ({parent} if parent else set())

    def global_target(self, key: str) -> float:
        by_id = {b.id: b for b in self.buckets}
        result = 1.
        while key:
            b = by_id.get(key)
            if b is None or b.target is None:
                return float('nan')
            result *= b.target
            key = b.parent
        return result


def validate_allocation(config: Allocation, holdings: pd.DataFrame | None = None) -> None:
    ids = [b.id for b in config.buckets]
    if config.version != 2 or len(set(ids)) != len(ids) or any(not key or key == 'unassigned' for key in ids):
        raise DataError('Allocation version must be 2, with unique nonempty bucket IDs (unassigned is reserved).')
    for b in config.buckets:
        if not b.name or (b.parent and b.parent not in ids):
            raise DataError('Each bucket needs a name and an existing parent.')
        if not isinstance(b.sell_protected, bool):
            raise DataError('Sell protection must be true or false.')
    for b in config.buckets:
        seen = {b.id}
        parent = b.parent
        while parent:
            if parent in seen:
                raise DataError('Allocation parents cannot form a cycle.')
            seen.add(parent)
            parent = next(node.parent for node in config.buckets if node.id == parent)
        if b.target is not None and (not math.isfinite(b.target) or not 0 <= b.target <= 1):
            raise DataError('Bucket targets must be blank or fractions between zero and one.')
    if holdings is not None:
        assigned = set(holdings.get('bucket_id', pd.Series(dtype=str))) - {''}
        if assigned - config.leaves():
            raise DataError('Positions must belong to existing leaf buckets. Move positions before removing or nesting a bucket.')


def load_allocation(path: Path, holdings: pd.DataFrame | None = None) -> Allocation | None:
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text())
        config = Allocation(tuple(Bucket(**b) for b in raw['buckets']), raw['version'])
        validate_allocation(config, holdings)
        return config
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise DataError(f'Invalid allocation configuration: {exc}') from exc


def save_allocation(path: Path, config: Allocation, holdings: pd.DataFrame, expected_revision: str | None) -> None:
    validate_allocation(config, holdings)
    content = yaml.safe_dump({'version': 2, 'buckets': [vars(b) for b in config.buckets]}, sort_keys=False)
    save_document(path, content, expected_revision)


def migration_preview(holdings: pd.DataFrame) -> tuple[Allocation, pd.DataFrame]:
    """Propose ownership from existing sleeve metadata; never activate or normalize."""
    rows = holdings.copy()
    buckets = []
    rows['bucket_id'] = ''
    rows['within_bucket_target'] = rows.get('target_allocation', float('nan'))
    for index, name in enumerate(dict.fromkeys(rows.portfolio)):
        if name:
            key = f'bucket-{index + 1}'
            buckets.append(Bucket(key, name))
            rows.loc[rows.portfolio == name, 'bucket_id'] = key
    return Allocation(tuple(buckets)), rows


def migrate(path: Path, config: Allocation, preview: pd.DataFrame, *, expected_revision: str | None) -> None:
    """Activate only after additive CSV changes; legacy targets remain untouched."""
    config_path = path.parent / 'allocation.yaml'
    if config_path.exists():
        raise DataError('Allocation is already enabled; use its editor.')
    if revision(path) != expected_revision:
        raise DataError('Holdings changed. Rebuild the migration preview.')
    content = path.read_text(encoding='utf-8-sig') if path.exists() else EMPTY_CSV
    current = parse_holdings(content)
    raw = pd.read_csv(StringIO(content), dtype=str, keep_default_na=False)
    if list(preview.position_id) != list(current.position_id):
        raise DataError('Migration preview no longer matches the holdings.')
    if 'position_key' not in raw:
        raw['position_key'] = [uuid4().hex for _ in range(len(raw))]
    for column in ('bucket_id', 'within_bucket_target'):
        raw[column] = preview[column].fillna('').to_numpy()
    candidate = parse_holdings(raw.to_csv(index=False))
    validate_allocation(config, candidate)
    save_document(path, raw.to_csv(index=False), expected_revision)
    save_allocation(config_path, config, candidate, None)


def analysis_targets(holdings: pd.DataFrame, config: Allocation) -> pd.DataFrame:
    result = holdings.copy()
    result['bucket_id'] = result.get('bucket_id', '')
    result['within_bucket_target'] = result.get('within_bucket_target', float('nan'))
    result['target_allocation'] = [config.global_target(key) * target if key else float('nan')
                                   for key, target in zip(result.bucket_id, result.within_bucket_target)]
    return result


def ignore_empty_by_bucket(holdings: pd.DataFrame) -> pd.DataFrame:
    from portfolio_app.rebalancing import ignore_empty_positions
    groups = []
    for _, rows in holdings.groupby('bucket_id', dropna=False, sort=False):
        temp = rows.copy()
        temp['target_allocation'] = temp.within_bucket_target
        # Unknown targets do not block the overview or calculations in other buckets.
        if temp.target_allocation.isna().any():
            kept = temp.loc[temp.shares > 0].copy()
            if (temp.shares == 0).any():
                kept['target_allocation'] = float('nan')
        else:
            kept = ignore_empty_positions(temp)
        kept['within_bucket_target'] = kept.target_allocation
        groups.append(kept)
    return pd.concat(groups) if groups else holdings.iloc[:0].copy()


def macro_table(valued: pd.DataFrame, config: Allocation, *, extra_cash: float = 0.) -> pd.DataFrame:
    complete = valued.current_value_eur.notna().all()
    total = float(valued.current_value_eur.sum()) + extra_cash
    records = []
    for b in (*config.buckets, Bucket('unassigned', 'Unassigned')):
        mask = valued.bucket_id.eq('') if b.id == 'unassigned' else valued.bucket_id.isin(config.leaves(b.id))
        rows = valued.loc[mask]
        value = float(rows.current_value_eur.sum())
        known = rows.current_value_eur.notna().all()
        target = config.global_target(b.id)
        current = value / total if complete and total > 0 else float('nan')
        parent_rows = valued.loc[valued.bucket_id.isin(config.leaves(b.parent))] if b.parent else valued
        parent_value = float(parent_rows.current_value_eur.sum()) + (extra_cash if not b.parent else 0.)
        parent_complete = parent_rows.current_value_eur.notna().all()
        records.append({'Bucket': b.name, 'Parent': b.parent, 'EUR value': value if known else float('nan'),
                        'Known EUR subtotal': value, 'Current portfolio %': current * 100,
                        'Current parent %': 100 * value / parent_value if known and parent_complete and parent_value > 0 else float('nan'),
                        'Target parent %': b.target * 100 if b.target is not None else float('nan'),
                        'Target portfolio %': target * 100, 'Gap (pp)': (current - target) * 100,
                        'Status': 'Missing prices' if not known else 'Planned capacity' if value == 0 else 'Valued'})
    if extra_cash:
        records.append({'Bucket': 'Unallocated contribution', 'EUR value': extra_cash, 'Status': 'Unallocated cash',
                        'Current portfolio %': 100 * extra_cash / total if complete and total else float('nan')})
    return pd.DataFrame(records)
