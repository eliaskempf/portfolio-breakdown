"""Load editable position data without depending on market data or the UI."""

from pathlib import Path
import math
from io import StringIO

import pandas as pd


class DataError(ValueError):
    """An editable input file is invalid."""


def load_holdings(path: Path) -> pd.DataFrame:
    try:
        return parse_holdings(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        raise DataError(f"Cannot read holdings CSV {path}: {exc}") from exc


def parse_holdings(content: str) -> pd.DataFrame:
    """Validate CSV content for both file loading and writes before persistence."""
    try:
        frame = pd.read_csv(StringIO(content), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise DataError(f"Cannot read holdings CSV: {exc}") from exc
    required = {"id", "name", "shares"}
    if missing := required - set(frame.columns):
        raise DataError(f"Holdings CSV is missing columns: {', '.join(sorted(missing))}")
    if "position_id" in frame.columns:
        raise DataError("position_id is reserved for internal position identity.")
    for column in frame.columns:
        frame[column] = frame[column].str.strip()
    for column in ("ticker", "isin", "portfolio", "account", "acquisition_price"):
        if column not in frame:
            frame[column] = ""
    frame["ticker"] = frame["ticker"].str.upper()
    frame["isin"] = frame["isin"].str.upper()
    if "acquisition_currency" in frame:
        frame["acquisition_currency"] = frame["acquisition_currency"].str.upper()
        invalid_currency = frame["acquisition_currency"].ne("") & ~frame["acquisition_currency"].str.fullmatch(r"[A-Z]{3}")
        if invalid_currency.any():
            raise DataError("acquisition_currency must be blank or a three-letter currency such as EUR.")
    if "target_allocation" in frame:
        raw_targets = frame["target_allocation"]
        targets = pd.to_numeric(raw_targets.str.removesuffix("%"), errors="coerce")
        targets = targets.where(~raw_targets.str.endswith("%"), targets / 100)
        invalid = raw_targets.ne("") & (~targets.map(math.isfinite) | (targets < 0) | (targets > 1))
        if invalid.any():
            raise DataError("target_allocation must be blank, a fraction from 0 to 1, or a percentage such as 15%.")
        frame["target_allocation"] = targets.astype(float)
    for column in ("id", "name"):
        if frame[column].eq("").any():
            raise DataError(f"Every holding needs a nonempty {column}.")
    for column in ("shares", "acquisition_price"):
        original = frame[column]
        numeric = pd.to_numeric(original, errors="coerce")
        invalid = (~numeric.map(math.isfinite) | (numeric < 0)) & original.ne("")
        if column == "shares":
            invalid |= original.eq("")
        if invalid.any():
            rows = ", ".join(str(i + 2) for i in frame.index[invalid])
            raise DataError(f"{column} must be a finite nonnegative number (CSV rows {rows}).")
        frame[column] = numeric.astype(float)
    for asset_id, positions in frame.groupby("id", sort=False):
        for column in ("name", "ticker", "isin"):
            if positions[column].nunique() > 1:
                raise DataError(f"Asset {asset_id!r} has inconsistent {column}; use distinct IDs for distinct instruments.")
    frame.insert(0, "position_id", [f"position-{i}" for i in range(len(frame))])
    return frame


def metadata_dimensions(holdings: pd.DataFrame) -> list[str]:
    excluded = {"position_id", "id", "name", "ticker", "isin", "shares", "acquisition_price", "acquisition_currency", "target_allocation", "purchase_history"}
    return [column for column in holdings.columns if column not in excluded]
