"""Local position persistence with validation, backups, and stale-edit detection."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
from portfolio_app.workspace.workspace_lock import document_write
import re
from portfolio_app.workspace.atomic_file import staged_text
from uuid import uuid4

import pandas as pd

from portfolio_app.portfolio.holdings import DataError, parse_holdings
from portfolio_app.workspace.locking import write_lock as _write_lock

EMPTY_CSV = "id,name,ticker,isin,shares,acquisition_price,acquisition_currency,portfolio,account,target_allocation\n"


@dataclass(frozen=True)
class HoldingsSnapshot:
    holdings: pd.DataFrame
    revision: str | None


def read_snapshot(path: Path) -> HoldingsSnapshot:
    if path.with_name('.history-publication.json').exists() or path.with_name('.allocation-publication.json').exists():
        from portfolio_app.workspace.workspace_lock import workspace_lock
        with workspace_lock(path.parent):
            pass
    try:
        content = path.read_bytes() if path.exists() else None
        return HoldingsSnapshot(
            parse_holdings(content.decode("utf-8-sig") if content is not None else EMPTY_CSV),
            sha256(content).hexdigest() if content is not None else None,
        )
    except (OSError, UnicodeError) as exc:
        raise DataError(f"Cannot read holdings CSV {path}: {exc}") from exc



def _new_asset_id(fields: Mapping[str, str], holdings: pd.DataFrame) -> str:
    ticker = fields.get("ticker", "").strip().upper()
    isin = fields.get("isin", "").strip().upper()
    same_isin = holdings.loc[holdings["isin"].eq(isin)] if isin else holdings.iloc[:0]
    # Distinct quote listings need separate identities so currencies/prices remain
    # consistent within each ID. An ISIN alone cannot distinguish those listings.
    duplicate = (bool(ticker) and holdings["ticker"].eq(ticker).any()) or (
        not same_isin.empty and (not ticker or same_isin["ticker"].eq("").any())
    )
    if duplicate:
        raise DataError("This instrument already exists. Select it under Existing instrument to reuse its classifications.")
    base = fields.get("ticker") or fields.get("isin") or fields.get("name") or "asset"
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "asset"
    asset_id = base
    suffix = 2
    while asset_id in set(holdings["id"]):
        asset_id = f"{base}-{suffix}"
        suffix += 1
    return asset_id


@document_write
def save_position(
    path: Path,
    fields: Mapping[str, str],
    *,
    expected_revision: str | None,
    position_id: str | None = None,
    validate: Callable[[pd.DataFrame], None] | None = None,
) -> str:
    """Append a summary position or replace one row; never add purchase quantities.

    The revision binds a generated position_id to the exact file the user saw.
    Existing columns and unchanged cell values survive the round trip.
    """
    if position_id is not None:
        from portfolio_app.portfolio.actual_portfolio import assert_cash_editable
        assert_cash_editable(path, [position_id])
        from portfolio_app.ledger.security_history import assert_security_editable
        assert_security_editable(path, [position_id], fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    category_publication = None
    with _write_lock(lock_path):
        original = path.read_bytes() if path.exists() else None
        revision = sha256(original).hexdigest() if original is not None else None
        if revision != expected_revision:
            raise DataError("Holdings changed since this form was opened. Reload the form before saving.")
        content = original.decode("utf-8-sig") if original is not None else EMPTY_CSV
        current = parse_holdings(content)
        csv, selected = position_content(content, fields, position_id=position_id, validate=validate)
        candidate = parse_holdings(csv)
        ownership_changed = 'bucket_id' in candidate and (position_id is None or selected.bucket_id != current.loc[selected.name, 'bucket_id'])
        config_path = path.parent / 'allocation.yaml'
        if ownership_changed and config_path.exists():
            from dataclasses import replace
            from portfolio_app.portfolio.allocation import load_allocation, validate_allocation
            from portfolio_app.workspace.storage import revision as document_revision
            config_revision = document_revision(config_path)
            config = load_allocation(config_path, current)
            buckets = []
            for bucket in config.buckets:
                members = set(candidate.loc[candidate.bucket_id.eq(bucket.id), 'position_id'])
                buckets.append(replace(bucket, buy_priority=tuple(key for key in bucket.buy_priority if key in members),
                    sell_priority=tuple(key for key in bucket.sell_priority if key in members)))
            version = 3 if any(config.children(b.id) and candidate.bucket_id.eq(b.id).any() for b in config.buckets) else config.version
            proposed = replace(config, buckets=tuple(buckets), version=version)
            validate_allocation(proposed, candidate)
            if proposed != config:
                category_publication = (proposed, config_revision)
        if category_publication is None:
            with staged_text(path, lambda handle: handle.write(csv), sync=True) as temporary:
                if original is not None:
                    backup_dir = path.parent / ".backups"
                    backup_dir.mkdir(exist_ok=True)
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    backup = backup_dir / f"{path.stem}-{stamp}-{uuid4().hex[:8]}.csv"
                    with backup.open("xb") as handle:
                        handle.write(original)
                # Also catch external CSV edits that happened during validation/staging.
                latest = path.read_bytes() if path.exists() else None
                if latest != original:
                    raise DataError("Holdings changed during the save. Reload the form before saving.")
                temporary.replace(path)
            return str(selected["id"])
    # The workspace barrier is still held by @document_write. Release the
    # holdings lock before the pair publisher acquires its document locks.
    from portfolio_app.portfolio.category_commands import publish_category_edit
    proposed, config_revision = category_publication
    publish_category_edit(path.parent, proposed, csv, holdings_revision=expected_revision,
                          allocation_revision=config_revision)
    return str(selected['id'])


def position_content(content, fields, *, position_id=None, validate=None):
    """Validate a position change without publishing it; shared by atomic trade saves."""
    current = parse_holdings(content)
    raw = pd.read_csv(StringIO(content), dtype=str, keep_default_na=False)
    values = {key: str(value).strip() for key, value in fields.items()}
    if "position_id" in values:
        raise DataError("Position identity cannot be entered as a CSV field.")
    if "position_key" in values:
        raise DataError("Persistent position identity cannot be edited.")
    for column in ("ticker", "isin", "acquisition_currency"):
        if column in values:
            values[column] = values[column].upper()
    if position_id is None:
        if not values.get("id"):
            values["id"] = _new_asset_id(values, current)
        existing = current.loc[current.id.eq(values['id'])]
        if not existing.empty and 'short_name' not in values:
            values['short_name'] = existing.iloc[0].short_name
        index = len(raw)
    else:
        matches = current.index[current["position_id"] == position_id]
        if len(matches) != 1:
            raise DataError("The selected position no longer exists. Reload the form.")
        index = int(matches[0])
        if "id" in values and values["id"] != current.at[index, "id"]:
            raise DataError("An existing position's asset ID cannot be changed here.")
    # A changed balance/cost is a replacement, not an inferred disposal.
    from portfolio_app.performance.cost_basis import FIELD, encode_components, aggregate_component, fingerprint, same_summary
    if position_id is not None and FIELD not in values:
        before = current.loc[index].to_dict()
        after = {**before, **values}
        if not same_summary(fingerprint(before), fingerprint(after)):
            values[FIELD] = encode_components([aggregate_component(after)], after)
    for column in values:
        if column not in raw:
            raw[column] = ""
    if position_id is None:
        raw.loc[index] = {column: "" for column in raw.columns}
        if "position_key" in raw:
            raw.at[index, "position_key"] = uuid4().hex
    for column, value in values.items():
        raw.at[index, column] = value
    instrument_id = current.at[index, 'id'] if position_id is not None else values['id']
    for column in ('name', 'short_name'):
        if column in values and (position_id is not None or column == 'short_name'):
            raw.loc[raw['id'] == instrument_id, column] = values[column]
    csv = raw.to_csv(index=False)
    candidate = parse_holdings(csv)
    selected = candidate.iloc[index]
    others = candidate.drop(index)
    duplicate = others["id"].eq(selected["id"]) & others["account"].eq(selected["account"]) & others["portfolio"].eq(selected["portfolio"])
    if "bucket_id" in candidate:
        duplicate &= others.bucket_id.eq(selected.bucket_id)
    if duplicate.any():
        raise DataError("A position for this instrument, account, and portfolio already exists. Edit its total shares instead of adding another position.")
    if validate:
        validate(candidate)
    return csv, selected
