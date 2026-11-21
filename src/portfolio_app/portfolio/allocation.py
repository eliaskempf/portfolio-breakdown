"""Non-overlapping allocation ownership and parent-relative budgets."""
from dataclasses import dataclass, replace
from io import StringIO
import math
from pathlib import Path
from uuid import uuid4

import pandas as pd
import yaml

from portfolio_app.portfolio.holdings import DataError, parse_holdings
from portfolio_app.portfolio.positions import EMPTY_CSV
from portfolio_app.workspace.storage import revision, save_document


@dataclass(frozen=True)
class Bucket:
    id: str
    name: str
    parent: str = ''
    target: float | None = None
    sell_protected: bool = False
    allocation_mode: str = 'targets'
    buy_priority: tuple[str, ...] = ()
    sell_priority: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.buy_priority, (tuple, list)) or not isinstance(self.sell_priority, (tuple, list)):
            raise ValueError('Trade priorities must be ordered position lists.')
        object.__setattr__(self, 'buy_priority', tuple(self.buy_priority))
        object.__setattr__(self, 'sell_priority', tuple(self.sell_priority))


@dataclass(frozen=True)
class Allocation:
    buckets: tuple[Bucket, ...]
    version: int = 2

    def children(self, parent: str = '') -> list[Bucket]:
        return [b for b in self.buckets if b.parent == parent]

    def leaves(self, parent: str = '') -> set[str]:
        children = self.children(parent)
        return set().union(*(self.leaves(b.id) for b in children)) if children else ({parent} if parent else set())

    def descendants(self, parent: str = '') -> set[str]:
        """Ownership scope, including positions assigned directly to the parent."""
        result = {parent} if parent else set()
        pending = [parent]
        by_parent = {}
        for bucket in self.buckets:
            by_parent.setdefault(bucket.parent, []).append(bucket.id)
        while pending:
            for key in by_parent.get(pending.pop(), ()):
                if key not in result:
                    result.add(key)
                    pending.append(key)
        return result

    def flexible(self, key: str) -> bool:
        return any(b.id == key and b.allocation_mode == 'flexible' for b in self.buckets)

    def relative_target(self, key: str, parent: str = '') -> float:
        by_id = {b.id: b for b in self.buckets}
        result = 1.
        while key != parent:
            b = by_id.get(key)
            if b is None or b.target is None:
                return float('nan')
            result *= b.target
            key = b.parent
        return result

    def global_target(self, key: str) -> float:
        return self.relative_target(key)


def validate_allocation(config: Allocation, holdings: pd.DataFrame | None = None) -> None:
    ids = [b.id for b in config.buckets]
    if config.version not in (2, 3) or len(set(ids)) != len(ids) or any(not key or key == 'unassigned' for key in ids):
        raise DataError('Allocation version must be 2 or 3, with unique nonempty bucket IDs (unassigned is reserved).')
    for b in config.buckets:
        if not b.name or (b.parent and b.parent not in ids):
            raise DataError('Each bucket needs a name and an existing parent.')
        if b.allocation_mode not in {'targets', 'flexible'}:
            raise DataError('Choose Member targets or Flexible allocation.')
        if b.allocation_mode == 'flexible' and config.children(b.id):
            raise DataError('Switch to Member targets before adding subcategories to a flexible category.')
        for priority in (b.buy_priority, b.sell_priority):
            if any(not isinstance(key, str) or not key for key in priority) or len(set(priority)) != len(priority):
                raise DataError('Trade priorities must contain unique position identities.')
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
        if assigned - set(ids):
            raise DataError('Positions must belong to existing categories. Move positions before removing a category.')
        for b in config.buckets:
            members = set(holdings.loc[holdings.bucket_id.eq(b.id), 'position_id']) if {'bucket_id', 'position_id'} <= set(holdings) else set()
            if (set(b.buy_priority) | set(b.sell_priority)) - members:
                raise DataError(f'Trade priorities for {b.name} reference positions outside the category. Review its priorities.')


def load_allocation(path: Path, holdings: pd.DataFrame | None = None) -> Allocation | None:
    if path.with_name('.allocation-publication.json').exists():
        from portfolio_app.workspace.workspace_lock import workspace_lock
        with workspace_lock(path.parent):
            pass
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
    owners = holdings.get('bucket_id', pd.Series(dtype=str))
    if any(config.children(b.id) and owners.eq(b.id).any() for b in config.buckets):
        config = replace(config, version=3)
    content = allocation_content(config)
    save_document(path, content, expected_revision)


def allocation_content(config: Allocation) -> str:
    version = 3 if config.version == 3 or any(b.allocation_mode != 'targets' or b.buy_priority or b.sell_priority for b in config.buckets) else 2
    records = []
    for b in config.buckets:
        record = vars(b).copy()
        if version == 2:
            for field in ('allocation_mode', 'buy_priority', 'sell_priority'):
                record.pop(field)
        else:
            record['buy_priority'] = list(b.buy_priority)
            record['sell_priority'] = list(b.sell_priority)
        records.append(record)
    return yaml.safe_dump({'version': version, 'buckets': records}, sort_keys=False)


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
    result['target_allocation'] = [config.global_target(key) * target if key and not config.flexible(key) else float('nan')
                                   for key, target in zip(result.bucket_id, result.within_bucket_target)]
    return result


def ignore_empty_by_bucket(holdings: pd.DataFrame, config: Allocation | None = None) -> pd.DataFrame:
    from portfolio_app.portfolio.rebalancing import ignore_empty_positions
    groups = []
    for owner, rows in holdings.groupby('bucket_id', dropna=False, sort=False):
        if config is not None and config.flexible(owner):
            groups.append(rows.loc[rows.shares > 0].copy())
            continue
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
    complete = valued.current_value_reporting.notna().all()
    total = float(valued.current_value_reporting.sum()) + extra_cash
    records = []
    for b in (*config.buckets, Bucket('unassigned', 'Unassigned')):
        if b.id == 'unassigned' and not valued.bucket_id.eq('').any():
            continue
        mask = valued.bucket_id.eq('') if b.id == 'unassigned' else valued.bucket_id.isin(config.descendants(b.id))
        rows = valued.loc[mask]
        value = float(rows.current_value_reporting.sum())
        known = rows.current_value_reporting.notna().all()
        target = config.global_target(b.id)
        current = value / total if complete and total > 0 else float('nan')
        parent_rows = valued.loc[valued.bucket_id.isin(config.descendants(b.parent))] if b.parent else valued
        parent_value = float(parent_rows.current_value_reporting.sum()) + (extra_cash if not b.parent else 0.)
        parent_complete = parent_rows.current_value_reporting.notna().all()
        records.append({'Bucket': b.name, 'Parent': b.parent, 'Value': value if known else float('nan'),
                        'Known subtotal': value, 'Current portfolio %': current * 100,
                        'Current parent %': 100 * value / parent_value if known and parent_complete and parent_value > 0 else float('nan'),
                        'Target parent %': b.target * 100 if b.target is not None else float('nan'),
                        'Target portfolio %': target * 100, 'Gap (pp)': (current - target) * 100,
                        'Status': 'Missing prices' if not known else 'Planned capacity' if value == 0 else 'Valued'})
    if extra_cash:
        records.append({'Bucket': 'Unallocated contribution', 'Value': extra_cash, 'Status': 'Unallocated cash',
                        'Current portfolio %': 100 * extra_cash / total if complete and total else float('nan')})
    return pd.DataFrame(records)
