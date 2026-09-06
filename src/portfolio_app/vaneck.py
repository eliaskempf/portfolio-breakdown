"""Narrow offline importer for VanEck's Semiconductor UCITS holdings XLSX."""

import argparse
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from http.cookiejar import CookieJar
from io import BytesIO
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.request import HTTPCookieProcessor, Request, build_opener
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pandas as pd
import yaml

from portfolio_app.etf import FundSnapshot, validate_constituents
from portfolio_app.holdings import DataError

SOURCE = "https://www.vaneck.com/no/en/investments/semiconductor-etf/downloads/holdings/"


def parse_holdings(path: Path | BytesIO) -> tuple[str, pd.DataFrame]:
    """Read the known export without requiring a general-purpose Excel dependency."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as archive:
        strings = ["".join(node.itertext()) for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.findall(".//s:sheetData/s:row", ns):
        values = {}
        for cell in row:
            value = cell.find("s:v", ns)
            text = "" if value is None else value.text or ""
            values[re.sub(r"\d", "", cell.attrib["r"])] = strings[int(text)] if cell.get("t") == "s" else text
        rows.append(values)
    stamp = re.search(r"All Holdings\s+(\d{2}/\d{2}/\d{4})", rows[0].get("A", "")) if rows else None
    if not stamp or len(rows) < 4 or rows[2].get("G") != "% of Net Assets" or rows[2].get("D") != "ISIN":
        raise DataError("Unrecognized VanEck holdings export; expected the All Holdings XLSX format.")
    as_of = datetime.strptime(stamp.group(1), "%m/%d/%Y").date().isoformat()
    records = []
    for row in rows[3:]:
        if not row.get("B") or row["B"] == "Other/Cash":
            continue
        ticker = row.get("C", "").strip()
        if not ticker.endswith(" US") or not row.get("G", "").endswith("%"):
            raise DataError(f"Unexpected constituent format: {row}")
        ticker = ticker.removesuffix(" US").upper()
        records.append({
            "constituent_id": "tsmc" if ticker == "TSM" else ticker.lower(),
            "name": row["B"], "ticker": ticker, "isin": row["D"],
            "weight": float(Decimal(row["G"].removesuffix("%")) / 100),
        })
    return as_of, validate_constituents(pd.DataFrame(records))


def download_holdings() -> bytes:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    request = Request(SOURCE, headers={"User-Agent": "Mozilla/5.0 (Portfolio breakdown)"})
    with opener.open(request, timeout=20) as response:
        content = response.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise DataError("VanEck holdings download exceeded the expected size.")
    return content


def refresh_snapshot(
    fund: FundSnapshot,
    *,
    fetch: Callable[[], bytes] = download_holdings,
    today: date | None = None,
) -> FundSnapshot:
    """Validate first, then atomically switch the manifest to a new CSV snapshot.

    The previous CSV is retained. A failed download, parse, or manifest write
    therefore cannot destroy the last working snapshot.
    """
    if fund.isin != "IE00BMC38736" or fund.manifest_path is None:
        raise DataError("Online updates are supported only for a configured VanEck Semiconductor UCITS snapshot.")
    stamp, frame = parse_holdings(BytesIO(fetch()))
    as_of = date.fromisoformat(stamp)
    if as_of < fund.as_of:
        raise DataError(f"VanEck returned an older snapshot ({stamp}); keeping {fund.as_of}.")
    if as_of > (today or date.today()):
        raise DataError(f"VanEck returned a future holdings date ({stamp}).")
    prior_ids = {row.isin: row.constituent_id for row in fund.constituents.itertuples() if row.isin}
    frame["constituent_id"] = [prior_ids.get(row.isin, row.constituent_id) for row in frame.itertuples()]
    frame = validate_constituents(frame)
    manifest = fund.manifest_path
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if raw["isin"].upper() != fund.isin:
        raise DataError("ETF configuration changed during the update; reload the page and try again.")
    csv = frame.to_csv(index=False)
    filename = f"{manifest.stem}-{stamp}-{sha256(csv.encode()).hexdigest()[:12]}.csv"
    destination = manifest.parent / filename
    staged: list[Path] = []
    try:
        if not destination.exists():
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=manifest.parent, suffix=".tmp", delete=False) as handle:
                staged.append(Path(handle.name))
                handle.write(csv)
            staged[-1].replace(destination)
        raw.update(as_of=stamp, holdings_file=filename, source=SOURCE)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=manifest.parent, suffix=".tmp", delete=False) as handle:
            staged.append(Path(handle.name))
            yaml.safe_dump(raw, handle, sort_keys=False)
        staged[-1].replace(manifest)
    finally:
        for path in staged:
            path.unlink(missing_ok=True)
    return replace(fund, as_of=as_of, source=SOURCE, constituents=frame)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert the VanEck Semiconductor UCITS All Holdings XLSX to normalized CSV")
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("output_csv", type=Path)
    args = parser.parse_args()
    as_of, frame = parse_holdings(args.xlsx)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output_csv, index=False)
    print(f"Imported {len(frame)} constituents as of {as_of}; update the snapshot manifest as_of date to match.")


if __name__ == "__main__":
    main()
