"""Invented test/demo positions. Never read or copy the user's data directory."""

import csv
import json
from pathlib import Path

import yaml

from portfolio_app.holdings import DataError


def create_demo_data(directory: Path) -> Path:
    """Create a separate, explicitly synthetic dataset without overwriting files."""
    marker = directory / ".synthetic-demo"
    if marker.exists():
        return directory
    if directory.exists() and any(directory.iterdir()):
        raise DataError("The demo directory is not empty. Use an empty directory; existing data will not be replaced.")
    directory.mkdir(parents=True, exist_ok=True)
    # Quantities, costs, portfolios, accounts, prices, and weights are invented.
    instruments = [
        ("nvda", "Nvidia", "NVDA", "US67066G1040", 2, "11.25", "AI Sleeve", "Demo account A"),
        ("tsmc", "TSMC", "TSM", "US8740391003", 3, "22.50", "AI Sleeve", "Demo account A"),
        ("enr", "Siemens Energy", "ENR.DE", "DE000ENER6Y0", 4, "33.75", "AI Sleeve", "Demo account B"),
        ("anet", "Arista Networks", "ANET", "US0404132054", 5, "", "AI Sleeve", "Demo account B"),
        ("vst", "Vistra", "VST", "US92840M1027", 6, "", "Core", "Demo account A"),
        ("nvda", "Nvidia", "NVDA", "US67066G1040", 1, "", "Core", "Demo account B"),
        ("unpriced", "Synthetic unpriced example", "", "", 2, "", "Core", "Demo account A"),
    ]
    with (directory / "holdings.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "name", "ticker", "isin", "shares", "acquisition_price", "portfolio", "account"])
        writer.writerows(instruments)
    paths = {
        "nvda": ("AI", "Compute", "GPUs"),
        "tsmc": ("AI", "Compute", "Semiconductor Manufacturing"),
        "enr": ("AI", "AI Infrastructure", "Energy", "Grid"),
        "anet": ("AI", "AI Infrastructure", "Networking"),
        "vst": ("AI", "AI Infrastructure", "Energy", "Generation"),
    }
    sectors = {
        "nvda": ["Technology", "Semiconductors"], "tsmc": ["Technology", "Semiconductors"],
        "enr": ["Industrials", "Energy Infrastructure"], "anet": ["Technology", "Networking"],
        "vst": ["Utilities", "Electricity"],
    }
    classifications = {asset: {"classifications": {
        "ai": [list(path)], "sector": [sectors[asset]], "asset_class": [["Equity"]],
    }} for asset, path in paths.items()}
    (directory / "classifications.yaml").write_text(yaml.safe_dump(classifications), encoding="utf-8")
    stamp = "2026-09-04T20:00:00+00:00"
    prices = {ticker: {"price": price, "currency": currency, "observed_at": stamp}
              for ticker, price, currency in [("NVDA", 100, "USD"), ("TSM", 50, "USD"), ("ENR.DE", 20, "EUR"), ("ANET", 40, "USD"), ("VST", 30, "USD")]}
    (directory / "demo_prices.json").write_text(json.dumps({
        "prices": prices, "fx": {"USD": {"price": 0.8, "currency": "EUR", "observed_at": stamp}},
    }), encoding="utf-8")
    etfs = directory / "etfs"
    etfs.mkdir()
    (etfs / "smh_ucits.yaml").write_text(yaml.safe_dump({
        "fund_id": "smh_ucits", "name": "VanEck Semiconductor UCITS ETF (synthetic demo weights)",
        "isin": "IE00BMC38736", "tickers": ["VVSM.DE", "SMH.L", "SMGB.L", "SMH.MI", "SMH.PA"],
        "as_of": "2026-09-04", "source": "https://www.vaneck.com/no/en/investments/semiconductor-etf/downloads/holdings/",
        "holdings_file": "smh_ucits.csv",
    }), encoding="utf-8")
    with (etfs / "smh_ucits.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["constituent_id", "name", "ticker", "isin", "weight"])
        writer.writerows([("nvda", "Nvidia", "NVDA", "US67066G1040", 0.08), ("tsmc", "TSMC", "TSM", "US8740391003", 0.07)])
    marker.write_text("Invented demo data; never use as actual portfolio or fund holdings.\n", encoding="utf-8")
    return directory
