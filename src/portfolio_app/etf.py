"""ETF snapshots and value-preserving expansion into normalized exposures."""

from dataclasses import dataclass
from datetime import date
import math
from pathlib import Path

import pandas as pd
import yaml

from portfolio_app.holdings import DataError
from portfolio_app.valuation import portfolio_weights


@dataclass(frozen=True)
class FundSnapshot:
    fund_id: str
    name: str
    isin: str
    tickers: tuple[str, ...]
    as_of: date
    source: str
    constituents: pd.DataFrame
    manifest_path: Path | None = None


def snapshot_age_days(fund: FundSnapshot, today: date | None = None) -> int:
    return ((today or date.today()) - fund.as_of).days


def validate_constituents(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"constituent_id", "name", "ticker", "isin", "weight"}
    if not required.issubset(frame.columns) or frame.empty:
        raise DataError("ETF holdings must contain constituent_id, name, ticker, isin, and weight rows.")
    result = frame.copy()
    for column in required - {"weight"}:
        result[column] = result[column].fillna("").astype(str).str.strip()
    result["ticker"] = result["ticker"].str.upper()
    result["isin"] = result["isin"].str.upper()
    weights = pd.to_numeric(result["weight"], errors="coerce")
    if (~weights.map(math.isfinite) | (weights < 0) | (weights > 1)).any():
        raise DataError("ETF weights must be finite fractions from 0 to 1.")
    if math.fsum(weights) > 1 + 1e-12:
        raise DataError("ETF constituent weights exceed 100%; correct the source data instead of renormalizing it.")
    if result["constituent_id"].eq("").any() or result["name"].eq("").any() or not result["constituent_id"].is_unique:
        raise DataError("ETF constituents require unique nonempty IDs and nonempty names.")
    if result["constituent_id"].str.startswith("etf-other:").any():
        raise DataError("The etf-other: ID prefix is reserved for residual ETF allocations.")
    result["weight"] = weights.astype(float)
    return result


def load_funds(directory: Path) -> list[FundSnapshot]:
    funds = []
    for manifest in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            frame = pd.read_csv(manifest.parent / raw["holdings_file"], dtype=str, keep_default_na=False)
            fund = FundSnapshot(
                fund_id=raw["fund_id"], name=raw["name"], isin=raw["isin"].upper(),
                tickers=tuple(ticker.upper() for ticker in raw["tickers"]),
                as_of=date.fromisoformat(str(raw["as_of"])), source=raw["source"],
                constituents=validate_constituents(frame),
                manifest_path=manifest,
            )
            if not fund.fund_id or not fund.name or len(fund.isin) != 12:
                raise ValueError("Fund ID, name, and a 12-character ISIN are required")
            funds.append(fund)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
            raise DataError(f"Invalid ETF snapshot {manifest.name}: {exc}") from exc
    if len({fund.fund_id for fund in funds}) != len(funds) or len({fund.isin for fund in funds}) != len(funds):
        raise DataError("ETF snapshot IDs and ISINs must be unique.")
    return funds


def matching_fund(position: dict, funds: list[FundSnapshot]) -> FundSnapshot | None:
    isin = str(position.get("isin", "")).strip().upper()
    ticker = str(position.get("ticker", "")).strip().upper()
    # An explicit ISIN is authoritative; a conflicting ticker must not select another fund.
    return next((fund for fund in funds if (isin == fund.isin if isin else ticker in fund.tickers)), None)


def smh_group_candidates(holdings: pd.DataFrame, funds: list[FundSnapshot]) -> tuple[set[str], set[str], set[str]]:
    """Held UCITS listings, matching direct stocks, and Nvidia/TSMC defaults.

    Only named, positive-weight constituents of the configured snapshot qualify.
    An explicit stock ISIN must match; ticker fallback requires a missing ISIN.
    """
    fund = next((fund for fund in funds if fund.isin == "IE00BMC38736"), None)
    if fund is None:
        return set(), set(), set()
    fund_ids, stocks, defaults = set(), set(), set()
    constituents = fund.constituents.loc[fund.constituents["weight"] > 0].to_dict("records")
    for position in holdings.to_dict("records"):
        if matching_fund(position, [fund]):
            fund_ids.add(position["id"])
            continue
        isin, ticker = position.get("isin", ""), position.get("ticker", "")
        matches = [row for row in constituents if (isin == row["isin"] if isin else ticker and ticker == row["ticker"])]
        if matches:
            stocks.add(position["id"])
            if any(row["ticker"] in {"NVDA", "TSM"} for row in matches):
                defaults.add(position["id"])
    return fund_ids, stocks, defaults


def validate_fund_listings(holdings: pd.DataFrame, funds: list[FundSnapshot]) -> None:
    for row in holdings.to_dict("records"):
        fund = matching_fund(row, funds)
        if fund and fund.isin == "IE00BMC38736" and row["ticker"] == "SMH":
            raise DataError("SMH is the US-listed fund's price ticker. For the UCITS fund use a qualified ticker such as SMH.L (USD) or VVSM.DE (EUR).")


def fund_breakdown(fund: FundSnapshot) -> pd.DataFrame:
    result = validate_constituents(fund.constituents)
    residual = max(0.0, 1 - math.fsum(result["weight"]))
    if residual > 1e-12:
        result = pd.concat([result, pd.DataFrame([{
            "constituent_id": f"etf-other:{fund.fund_id}", "name": f"{fund.name} / Other",
            "ticker": "", "isin": "", "weight": residual,
        }])], ignore_index=True)
    return result


def resolve_constituent_asset(constituent: dict, holdings: pd.DataFrame) -> tuple[str, str]:
    matches = holdings.iloc[:0]
    if constituent["isin"]:
        matches = holdings.loc[holdings["isin"] == constituent["isin"]]
    if matches.empty and constituent["ticker"]:
        matches = holdings.loc[holdings["ticker"] == constituent["ticker"]]
    if matches["id"].nunique() > 1:
        raise DataError(f"Several asset IDs match ETF constituent {constituent['ticker']}; use one stable asset ID across its positions.")
    if not matches.empty:
        return str(matches.iloc[0]["id"]), str(matches.iloc[0]["name"])
    # IDs attach classifications when the constituent is not directly held.
    if constituent["constituent_id"] in set(holdings["id"]):
        raise DataError(f"ETF constituent ID {constituent['constituent_id']} conflicts with another instrument; edit the constituent ID.")
    return constituent["constituent_id"], constituent["name"]


def expand_etfs(exposures: pd.DataFrame, funds: list[FundSnapshot], holdings: pd.DataFrame) -> pd.DataFrame:
    records = []
    for exposure in exposures.to_dict("records"):
        fund = matching_fund(exposure, funds)
        if fund is None:
            records.append(exposure)
            continue
        for constituent in fund_breakdown(fund).to_dict("records"):
            row = exposure.copy()
            row["asset_id"], row["asset_name"] = resolve_constituent_asset(constituent, holdings)
            row["value"] = exposure["value"] * constituent["weight"]
            row["ticker"], row["isin"] = constituent["ticker"], constituent["isin"]
            row["source_type"] = "etf_other" if constituent["constituent_id"].startswith("etf-other:") else "etf_constituent"
            row["direct_or_indirect"] = "indirect"
            # Instrument quantities, quotes, and targets do not describe its constituents.
            for field in ("shares", "acquisition_price", "current_price", "fx_to_eur", "target_allocation", "portfolio_weight"):
                if field in row:
                    row[field] = float("nan")
            records.append(row)
    return pd.DataFrame(records, columns=exposures.columns)


def effective_exposure_table(exposures: pd.DataFrame) -> pd.DataFrame:
    columns = ["Asset", "Ticker", "Direct (EUR)", "ETF-derived (EUR)", "Total (EUR)", "Allocation %"]
    if exposures.empty:
        return pd.DataFrame(columns=columns)
    records = []
    for _, rows in exposures.groupby("asset_id", sort=False):
        records.append({
            "Asset": rows.iloc[0]["asset_name"], "Ticker": rows.iloc[0]["ticker"],
            "Direct (EUR)": rows.loc[rows["direct_or_indirect"] == "direct", "value"].sum(),
            "ETF-derived (EUR)": rows.loc[rows["direct_or_indirect"] == "indirect", "value"].sum(),
            "Total (EUR)": rows["value"].sum(),
        })
    result = pd.DataFrame(records)
    result["Allocation %"] = 100 * portfolio_weights(result["Total (EUR)"])
    return result[columns].sort_values("Total (EUR)", ascending=False, ignore_index=True)
