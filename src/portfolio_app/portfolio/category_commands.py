"""Revision-checked category edits with recoverable ownership publication."""
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from uuid import uuid4

import pandas as pd

from portfolio_app.portfolio.allocation import Allocation, Bucket, allocation_content, validate_allocation
from portfolio_app.portfolio.holdings import DataError, parse_holdings
from portfolio_app.workspace.atomic_file import staged_text
from portfolio_app.workspace.storage import revision, save_document
from portfolio_app.workspace.workspace_lock import workspace_lock

INTENT = '.allocation-publication.json'


def _write(path, content):
    with staged_text(path, lambda handle: handle.write(content), sync=True) as temporary:
        temporary.replace(path)


def recover_category_publication(directory):
    intent = directory / INTENT
    if not intent.exists():
        return
    payload = json.loads(intent.read_text())
    if payload.get('version') != 1 or set(payload.get('documents', {})) != {'holdings.csv', 'allocation.yaml'}:
        raise DataError('Invalid pending category save; preserve the recovery file.')
    for name, versions in payload['documents'].items():
        path = directory / name
        current = path.read_bytes().decode('utf-8') if path.exists() else None
        if current not in (versions['before'], versions['after']):
            raise DataError('Portfolio changed during category recovery; preserve both versions for review.')
    for name, versions in payload['documents'].items():
        path = directory / name
        if versions['before'] is None:
            path.unlink(missing_ok=True)
        else:
            _write(path, versions['before'])
    intent.unlink()


def publish_category_edit(directory, config, holdings_content, *, holdings_revision, allocation_revision):
    directory = Path(directory)
    with workspace_lock(directory):
        if revision(directory/'holdings.csv') != holdings_revision or revision(directory/'allocation.yaml') != allocation_revision:
            raise DataError('Portfolio changed. Review the category edit again.')
        validate_allocation(config, parse_holdings(holdings_content))
        documents = {'holdings.csv': holdings_content, 'allocation.yaml': allocation_content(config)}
        payload = {'version': 1, 'documents': {name: {
            'before': (directory/name).read_bytes().decode('utf-8') if (directory/name).exists() else None,
            'after': content} for name, content in documents.items()}}
        _write(directory/INTENT, json.dumps(payload))
        try:
            for name, content in documents.items():
                save_document(directory/name, content, holdings_revision if name == 'holdings.csv' else allocation_revision)
            (directory/INTENT).unlink()
        except BaseException:
            recover_category_publication(directory)
            raise


def create_category(directory, config, members, name, target, *, mode='flexible', holdings_revision, allocation_revision):
    """Move reviewed sibling positions together; preserve all financial facts."""
    directory = Path(directory)
    with workspace_lock(directory):
        if revision(directory/'holdings.csv') != holdings_revision or revision(directory/'allocation.yaml') != allocation_revision:
            raise DataError('Portfolio changed. Review the category edit again.')
        content = (directory/'holdings.csv').read_text(encoding='utf-8-sig')
        positions = parse_holdings(content)
        if not members or not set(members) <= set(positions.position_id):
            raise DataError('Select existing positions for the new category.')
        selected = positions.loc[positions.position_id.isin(members)]
        if selected.bucket_id.nunique() != 1 or not selected.bucket_id.iloc[0]:
            raise DataError('Select positions sharing one assigned parent category.')
        parent = selected.bucket_id.iloc[0]
        if config.flexible(parent):
            raise DataError('Switch the parent to Member targets before creating a subcategory.')
        if not name.strip():
            raise DataError('Enter a category name.')
        raw = pd.read_csv(StringIO(content), dtype=str, keep_default_na=False)
        if 'position_key' not in raw:
            raw['position_key'] = [uuid4().hex for _ in range(len(raw))]
        key = uuid4().hex
        raw.loc[selected.index, 'bucket_id'] = key
        # Targets change denominator when moved; fixed allocations need explicit review.
        if mode == 'targets':
            raise DataError('Create a flexible category, then review its member targets before enabling them.')
        candidate = Allocation((*config.buckets, Bucket(key, name.strip(), parent, target, allocation_mode=mode)), 3)
        publish_category_edit(directory, candidate, raw.to_csv(index=False), holdings_revision=holdings_revision,
                              allocation_revision=allocation_revision)
        return key


def save_category_settings(directory, config, key, mode, buy, sell, *, holdings_revision, allocation_revision):
    directory = Path(directory)
    with workspace_lock(directory):
        if revision(directory/'holdings.csv') != holdings_revision or revision(directory/'allocation.yaml') != allocation_revision:
            raise DataError('Portfolio changed. Review category settings again.')
        content = (directory/'holdings.csv').read_text(encoding='utf-8-sig')
        positions = parse_holdings(content)
        raw = pd.read_csv(StringIO(content), dtype=str, keep_default_na=False)
        if 'position_key' not in raw:
            raw['position_key'] = [uuid4().hex for _ in range(len(raw))]
        identities = dict(zip(positions.position_id, raw.position_key))
        if not (set(buy) | set(sell)) <= set(identities):
            raise DataError('Trade priority positions changed. Review them again.')
        candidate = Allocation(tuple(replace(b, allocation_mode=mode,
            buy_priority=tuple(identities[k] for k in buy), sell_priority=tuple(identities[k] for k in sell)) if b.id == key else b
            for b in config.buckets), 3)
        publish_category_edit(directory, candidate, raw.to_csv(index=False), holdings_revision=holdings_revision,
                              allocation_revision=allocation_revision)


def move_category_positions(directory, config, members, destination, *, holdings_revision, allocation_revision):
    """Move source ownership and retire obsolete priority entries in one publication."""
    directory = Path(directory)
    with workspace_lock(directory):
        if revision(directory/'holdings.csv') != holdings_revision or revision(directory/'allocation.yaml') != allocation_revision:
            raise DataError('Portfolio changed. Review the position move again.')
        if destination not in config.descendants():
            raise DataError('Choose an existing destination category.')
        content = (directory/'holdings.csv').read_text(encoding='utf-8-sig')
        positions = parse_holdings(content)
        if not members or not set(members) <= set(positions.position_id):
            raise DataError('Select existing positions to move.')
        raw = pd.read_csv(StringIO(content), dtype=str, keep_default_na=False)
        raw.loc[positions.position_id.isin(members), 'bucket_id'] = destination
        moved = set(positions.loc[positions.position_id.isin(members) & positions.bucket_id.ne(destination), 'position_id'])
        candidate = Allocation(tuple(replace(b, buy_priority=tuple(key for key in b.buy_priority if key not in moved),
            sell_priority=tuple(key for key in b.sell_priority if key not in moved)) for b in config.buckets), 3)
        publish_category_edit(directory, candidate, raw.to_csv(index=False), holdings_revision=holdings_revision,
                              allocation_revision=allocation_revision)
