"""Local position persistence with validation, backups, and stale-edit detection."""

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from uuid import uuid4

import pandas as pd

from portfolio_app.holdings import DataError, parse_holdings

EMPTY_CSV = "id,name,ticker,isin,shares,acquisition_price,acquisition_currency,portfolio,account,target_allocation\n"


@dataclass(frozen=True)
class HoldingsSnapshot:
    holdings: pd.DataFrame
    revision: str | None


def read_snapshot(path: Path) -> HoldingsSnapshot:
    try:
        content = path.read_bytes() if path.exists() else None
        return HoldingsSnapshot(
            parse_holdings(content.decode("utf-8-sig") if content is not None else EMPTY_CSV),
            sha256(content).hexdigest() if content is not None else None,
        )
    except (OSError, UnicodeError) as exc:
        raise DataError(f"Cannot read holdings CSV {path}: {exc}") from exc


@contextmanager
def _write_lock(path: Path):
    """Coordinate app writers across tabs/processes without a database."""
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with _write_lock(lock_path):
        original = path.read_bytes() if path.exists() else None
        revision = sha256(original).hexdigest() if original is not None else None
        if revision != expected_revision:
            raise DataError("Holdings changed since this form was opened. Reload the form before saving.")
        content = original.decode("utf-8-sig") if original is not None else EMPTY_CSV
        current = parse_holdings(content)
        raw = pd.read_csv(StringIO(content), dtype=str, keep_default_na=False)
        values = {key: str(value).strip() for key, value in fields.items()}
        if "position_id" in values:
            raise DataError("Position identity cannot be entered as a CSV field.")
        for column in ("ticker", "isin", "acquisition_currency"):
            if column in values:
                values[column] = values[column].upper()
        if position_id is None:
            if not values.get("id"):
                values["id"] = _new_asset_id(values, current)
            index = len(raw)
        else:
            matches = current.index[current["position_id"] == position_id]
            if len(matches) != 1:
                raise DataError("The selected position no longer exists. Reload the form.")
            index = int(matches[0])
            if "id" in values and values["id"] != current.at[index, "id"]:
                raise DataError("An existing position's asset ID cannot be changed here.")
        for column in values:
            if column not in raw:
                raw[column] = ""
        if position_id is None:
            raw.loc[index] = {column: "" for column in raw.columns}
        for column, value in values.items():
            raw.at[index, column] = value
        csv = raw.to_csv(index=False)
        candidate = parse_holdings(csv)
        selected = candidate.iloc[index]
        others = candidate.drop(index)
        duplicate = others["id"].eq(selected["id"]) & others["account"].eq(selected["account"]) & others["portfolio"].eq(selected["portfolio"])
        if duplicate.any():
            raise DataError("A position for this instrument, account, and portfolio already exists. Edit its total shares instead of adding another position.")
        if validate:
            validate(candidate)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(csv)
                handle.flush()
                os.fsync(handle.fileno())
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
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return str(selected["id"])
