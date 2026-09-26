"""Bulk purchases update one summary position, with atomic source-record storage."""

from collections.abc import Callable, Mapping, Sequence
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import re
from uuid import uuid4

import pandas as pd

from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.positions import EMPTY_CSV, save_position

COLUMNS = ("date", "shares", "price", "fees")
HISTORY_COLUMN = "purchase_history"


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _number(value: object, label: str, *, optional: bool = False) -> Decimal | None:
    text = _text(value).replace(",", ".")
    if not text and optional:
        return None
    if not re.fullmatch(r"\d+(?:\.\d+)?", text) or len(text) > 40:
        raise DataError(f"{label}: enter a nonnegative decimal number without thousands separators.")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise DataError(f"{label}: invalid number.") from None
    if number > Decimal("1e18"):
        raise DataError(f"{label}: number is too large.")
    return number


@dataclass(frozen=True)
class Purchase:
    date: str
    shares: Decimal
    price: Decimal | None
    fees: Decimal

    def record(self) -> dict[str, str]:
        return {"date": self.date, "shares": str(self.shares),
                "price": "" if self.price is None else str(self.price), "fees": str(self.fees)}


def validate_purchases(rows: Sequence[Mapping], *, today: date | None = None) -> list[Purchase]:
    if len(rows) > 1000:
        raise DataError("Enter at most 1,000 purchases per batch.")
    result = []
    for index, row in enumerate(rows, start=1):
        if not any(_text(value) for value in row.values()):
            continue
        if set(row) - set(COLUMNS):
            raise DataError("Purchase columns must be date, shares, price, and fees. Select one currency and instrument for the whole batch.")
        day = _text(row.get("date"))
        if day:
            try:
                parsed = date.fromisoformat(day)
                if parsed.isoformat() != day or parsed > (today or date.today()):
                    raise ValueError
            except ValueError:
                raise DataError(f"Purchase {index}: date must be YYYY-MM-DD, no later than today, or blank.") from None
        shares = _number(row.get("shares"), f"Purchase {index} shares")
        if not shares:
            raise DataError(f"Purchase {index}: shares must be greater than zero.")
        result.append(Purchase(day, shares,
                               _number(row.get("price"), f"Purchase {index} price", optional=True),
                               _number(_text(row.get("fees")) or "0", f"Purchase {index} fees")))
    if not result:
        raise DataError("Enter at least one purchase.")
    return result


def parse_purchase_text(text: str) -> list[dict[str, str]]:
    """CSV, semicolon CSV, or clipboard TSV, with strict headers/row lengths."""
    if len(text) > 1_000_000:
        raise DataError("Purchase input is too large (maximum 1 MB).")
    text = text.lstrip("\ufeff").strip()
    if not text:
        return []
    header = text.splitlines()[0]
    delimiter = "\t" if "\t" in header else ";" if ";" in header else ","
    try:
        reader = csv.reader(StringIO(text), delimiter=delimiter, strict=True)
        columns = [value.strip().lower() for value in next(reader)]
        if len(set(columns)) != len(columns) or not {"shares", "price"} <= set(columns) or set(columns) - set(COLUMNS):
            raise DataError("Use headers shares and price, with optional date and fees. One instrument and currency per batch.")
        rows = []
        for line, values in enumerate(reader, start=2):
            if not values or not any(value.strip() for value in values):
                continue
            if len(values) != len(columns):
                raise DataError(f"Input line {line}: expected {len(columns)} columns. Use tabs/semicolons or quote decimal commas.")
            rows.append(dict(zip(columns, values, strict=True)))
            if len(rows) > 1000:
                raise DataError("Enter at most 1,000 purchases per batch.")
    except (csv.Error, StopIteration):
        raise DataError("Cannot read purchase table; check its headers, delimiters and quotes.") from None
    return rows


def read_opening(path: Path, revision: str | None, position_id: str | None) -> dict[str, str]:
    """Read exact CSV decimals against the same revision used by the save guard."""
    content = path.read_bytes() if path.exists() else None
    if (sha256(content).hexdigest() if content is not None else None) != revision:
        raise DataError("Holdings changed while this batch was open. Reload the position form.")
    text = content.decode("utf-8-sig") if content is not None else EMPTY_CSV
    frame = parse_holdings(text)
    if position_id is None:
        return {}
    matches = frame.index[frame["position_id"] == position_id]
    if len(matches) != 1:
        raise DataError("The selected position no longer exists. Reload the position form.")
    raw = pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)
    return {key: value.strip() for key, value in raw.iloc[int(matches[0])].items()}


def read_purchase_history(value: str) -> list[dict]:
    if not value:
        return []
    try:
        batches = json.loads(value)
        if not isinstance(batches, list) or any(
            not isinstance(batch, dict) or batch.get("version") != 1
            or batch.get("mode") not in {"add", "reconcile"}
            or not isinstance(batch.get("purchases"), list)
            or not isinstance(batch.get("currency"), str)
            or not isinstance(batch.get("saved_at"), str)
            or any(not isinstance(row, dict) or set(row) != set(COLUMNS)
                   or any(not isinstance(cell, str) for cell in row.values()) for row in batch["purchases"])
            for batch in batches
        ):
            raise ValueError
        return batches
    except (ValueError, TypeError):
        raise DataError("Stored purchase history is invalid. Restore it from a backup before adding purchases.") from None


@dataclass(frozen=True)
class PurchaseSummary:
    previous_shares: Decimal
    batch_shares: Decimal
    total_shares: Decimal
    batch_cost: Decimal | None
    fees: Decimal
    average: Decimal | None
    currency: str


def repeated_batch(purchases: Sequence[Purchase], currency: str, opening: Mapping[str, str]) -> bool:
    def key(row):
        return (row["date"], Decimal(row["shares"]), Decimal(row["price"]) if row["price"] else None, Decimal(row["fees"]))

    entered = Counter(key(purchase.record()) for purchase in purchases)
    for batch in read_purchase_history(opening.get(HISTORY_COLUMN, "")):
        if batch["mode"] == "add" and batch["currency"] == currency.strip().upper():
            try:
                if Counter(key(row) for row in batch["purchases"]) == entered:
                    return True
            except InvalidOperation:
                raise DataError("Stored purchase history contains invalid numbers; restore it from a backup.") from None
    return False


def summarize_purchases(purchases: Sequence[Purchase], currency: str, opening: Mapping[str, str], *, mode: str = "add") -> PurchaseSummary:
    if mode not in {"add", "reconcile"}:
        raise DataError("Unknown purchase operation.")
    currency = currency.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise DataError("Enter a three-letter purchase currency, such as EUR.")
    if not purchases:
        raise DataError("Enter at least one purchase.")
    with localcontext() as context:
        context.prec = 50
        previous = Decimal(opening.get("shares") or "0")
        batch_shares = sum((purchase.shares for purchase in purchases), Decimal(0))
        fees = sum((purchase.fees for purchase in purchases), Decimal(0))
        cost = None if any(p.price is None for p in purchases) else sum((p.shares * p.price + p.fees for p in purchases), Decimal(0))
        if mode == "reconcile":
            tolerance = min(Decimal("0.000000001"), abs(previous) * Decimal("0.000000001"))
            if not opening or previous <= 0 or abs(batch_shares - previous) > tolerance:
                raise DataError("Historical purchases must total the shares already held. Include only shares still held, with no intervening sales or splits.")
            total = previous
            average = cost / total if cost is not None else None
        else:
            total = previous + batch_shares
            old_price = opening.get("acquisition_price", "")
            old_currency = opening.get("acquisition_currency", "").upper()
            if previous and old_price and not old_currency:
                raise DataError("The existing buy-in has no currency. Set its currency in Edit position before combining purchases.")
            if previous and old_price and old_currency != currency:
                raise DataError("Purchase currency must match the existing buy-in currency. Use original settlement costs in one currency; today's FX is not used.")
            known = cost is not None and (not previous or bool(old_price))
            average = (cost + previous * Decimal(old_price or "0")) / total if known else None
        return PurchaseSummary(previous, batch_shares, total, cost, fees, average, currency)


def save_purchase_batch(
    path: Path, fields: Mapping[str, str], rows: Sequence[Mapping], *, currency: str,
    expected_revision: str | None, position_id: str | None = None, mode: str = "add",
    allow_repeat: bool = False,
    validate: Callable[[pd.DataFrame], None] | None = None,
) -> str:
    purchases = validate_purchases(rows)
    opening = read_opening(path, expected_revision, position_id)
    if mode == 'add' and opening.get('balance_replaced_at'):
        confirmed = opening.get('holdings_confirmed_on', '')
        if not confirmed or any(not p.date or p.date <= confirmed for p in purchases):
            raise DataError('These purchases may already be included in the replacement balance. Update balances, or enter only dated purchases after its holdings-confirmation date.')
    summary = summarize_purchases(purchases, currency, opening, mode=mode)
    if mode == "add" and repeated_batch(purchases, currency, opening) and not allow_repeat:
        raise DataError("This batch matches purchases saved earlier. Confirm they are additional purchases before saving again.")
    history = read_purchase_history(opening.get(HISTORY_COLUMN, ""))
    history.append({
        "version": 1, "batch_id": uuid4().hex, "saved_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode, "currency": summary.currency, "purchases": [p.record() for p in purchases],
        "opening": {key: opening.get(key, "") for key in ("shares", "acquisition_price", "acquisition_currency")},
        "result_shares": str(summary.total_shares),
        "result_average": "" if summary.average is None else str(summary.average),
    })
    # One atomic CSV replacement commits both the summary and its source records.
    # Existing custom metadata and targets are preserved by save_position.
    values = dict(fields) if position_id is None else {}
    values.update({"shares": str(summary.total_shares),
                   "acquisition_price": "" if summary.average is None else str(summary.average),
                   "acquisition_currency": summary.currency if summary.average is not None else "",
                   HISTORY_COLUMN: json.dumps(history, separators=(",", ":"))})
    return save_position(path, values, expected_revision=expected_revision, position_id=position_id, validate=validate)
