"""ETF snapshots and value-preserving expansion into normalized exposures."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
import math
from pathlib import Path

import pandas as pd
import yaml

from portfolio_app.exposure.taxonomy import copy_classifications
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.valuation import portfolio_weights
from portfolio_app.funds.constituents import fund_kind, interpret_constituent


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
    equity_fund: bool = False
    proxy_source: str = ''
    notes: str = ''
    provider: str = ''
    product_url: str = ''
    asset_class: str = ''
    replication: str = ''
    breakdown_basis: str = 'holdings'
    wkn: str = ''
    summaries: dict = field(default_factory=dict)
    basket: pd.DataFrame | None = None


def snapshot_age_days(fund: FundSnapshot, today: date | None = None) -> int:
    return ((today or date.today()) - fund.as_of).days


def validate_constituents(frame: pd.DataFrame, *, allow_signed: bool = False) -> pd.DataFrame:
    required = {"constituent_id", "name", "ticker", "isin", "weight"}
    if not required.issubset(frame.columns) or frame.empty:
        raise DataError("ETF holdings must contain constituent_id, name, ticker, isin, and weight rows.")
    result = frame.copy()
    for column in required - {"weight"}:
        result[column] = result[column].fillna("").astype(str).str.strip()
    result["ticker"] = result["ticker"].str.upper()
    result["isin"] = result["isin"].str.upper()
    weights = pd.to_numeric(result["weight"], errors="coerce")
    if (~weights.map(math.isfinite) | (weights < (-1 if allow_signed else 0)) | (weights > 1)).any():
        raise DataError("ETF weights must be finite fractions from 0 to 1.")
    if math.fsum(weights) > 1 + 1e-12:
        raise DataError("ETF constituent weights exceed 100%; correct the source data instead of renormalizing it.")
    if allow_signed and math.fsum(weights) <= 0:
        raise DataError('A substitute basket must have positive net weight.')
    if result["constituent_id"].eq("").any() or result["name"].eq("").any() or not result["constituent_id"].is_unique:
        raise DataError("ETF constituents require unique nonempty IDs and nonempty names.")
    if result["constituent_id"].str.startswith("etf-other:").any():
        raise DataError("The etf-other: ID prefix is reserved for residual ETF allocations.")
    result["weight"] = weights.astype(float)
    return result


def _read_constituents(path: Path, *, allow_signed: bool = False) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    return validate_constituents(frame, allow_signed=allow_signed)


class FundCatalog:
    """Reuse validated constituent files across reads of a changing fund directory."""

    def __init__(self):
        self._files = {}

    def load(self, directory: Path) -> list[FundSnapshot]:
        current = {}

        def read(path: Path, *, allow_signed: bool = False) -> pd.DataFrame:
            stat = path.stat()
            key = (path.resolve(), allow_signed)
            version = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            previous = current.get(key) or self._files.get(key)
            if previous is None or previous[0] != version:
                previous = (version, _read_constituents(path, allow_signed=allow_signed))
            current[key] = previous
            return previous[1].copy()

        funds = _load_funds(directory, read)
        self._files = current
        return funds


def load_funds(directory: Path) -> list[FundSnapshot]:
    return _load_funds(directory, _read_constituents)


def _load_funds(directory, read_constituents):
    funds = []
    for manifest in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            frame = read_constituents(manifest.parent / raw["holdings_file"])
            fund = FundSnapshot(
                fund_id=raw["fund_id"], name=raw["name"], isin=raw["isin"].upper(),
                tickers=tuple(ticker.upper() for ticker in raw["tickers"]),
                as_of=date.fromisoformat(str(raw["as_of"])), source=raw["source"],
                constituents=frame,
                manifest_path=manifest,
                equity_fund=raw.get('equity_fund', False),
                proxy_source=raw.get('proxy_source', ''), notes=raw.get('notes', ''),
                **{key: raw.get(key, default) for key, default in {
                    'provider': '', 'product_url': '', 'asset_class': '', 'replication': '',
                    'breakdown_basis': 'holdings', 'wkn': '', 'summaries': {},
                }.items()},
                basket=read_constituents(manifest.parent / raw['basket_file'], allow_signed=True) if raw.get('basket_file') else None,
            )
            if not fund.fund_id or not fund.name or len(fund.isin) != 12:
                raise ValueError("Fund ID, name, and a 12-character ISIN are required")
            if not isinstance(fund.equity_fund, bool):
                raise ValueError('equity_fund must be true or false')
            if not isinstance(fund.proxy_source, str) or not isinstance(fund.notes, str):
                raise ValueError('proxy_source and notes must be text')
            if fund.breakdown_basis not in {'holdings', 'economic', 'proxy'}:
                raise ValueError('Unknown breakdown basis')
            if not isinstance(fund.summaries, dict):
                raise ValueError('Fund summaries must be a mapping')
            if any(not isinstance(getattr(fund, key), str) for key in
                   ('provider', 'product_url', 'asset_class', 'replication', 'wkn')):
                raise ValueError('Fund metadata must be text')
            funds.append(fund)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
            raise DataError(f"Invalid ETF snapshot {manifest.name}: {exc}") from exc
    if len({fund.fund_id for fund in funds}) != len(funds) or len({fund.isin for fund in funds}) != len(funds):
        raise DataError("ETF snapshot IDs and ISINs must be unique.")
    return funds


def _position_listing(position: dict) -> tuple[str, str]:
    # An explicit ISIN or WKN is authoritative even when the ticker conflicts.
    for field in ('isin', 'wkn', 'ticker'):
        value = str(position.get(field, '')).strip().upper()
        if value:
            return field, value
    return '', ''


def matching_fund(position: dict, funds: list[FundSnapshot]) -> FundSnapshot | None:
    field, value = _position_listing(position)
    if not value:
        return None
    if field == 'ticker':
        matches = [fund for fund in funds if value in fund.tickers]
    else:
        matches = [fund for fund in funds if getattr(fund, field) == value]
    return matches[0] if len(matches) == 1 else None


def group_fund_positions(funds: list[FundSnapshot], rows: Iterable[dict]) -> list[list[dict]]:
    """Group positions in input order for each fund, as when checking its listings separately."""
    by_listing = {}
    groups = []
    for fund in funds:
        group = []
        groups.append(group)
        listings = {('isin', fund.isin), ('wkn', fund.wkn)}
        listings.update(('ticker', ticker) for ticker in fund.tickers)
        for key in listings:
            if key[1]:
                by_listing.setdefault(key, []).append(group)
    if not by_listing:
        return groups
    for row in rows:
        for group in by_listing.get(_position_listing(row), ()):
            group.append(row)
    return groups


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
            raise DataError("SMH is the US-listed fund's price ticker. For the UCITS fund use a qualified ticker such as SMH.L (USD) or VVSM.DE.")


def constituent_records(frame: pd.DataFrame):
    """Read normalized text and weight columns in bulk, retaining provider metadata."""
    canonical = all(dtype == 'float64' or (isinstance(dtype, pd.StringDtype) and dtype.na_value is not pd.NA)
                    for dtype in frame.dtypes)
    if not canonical or not len(frame.columns):
        # Let pandas box arbitrary extension types, nullable values and date metadata.
        return iter(frame.to_dict('records'))
    names = tuple(frame.columns)
    columns = [series.to_numpy(dtype=object) for _, series in frame.items()]
    return (dict(zip(names, values)) for values in zip(*columns))


def fund_breakdown(fund: FundSnapshot) -> pd.DataFrame:
    result = validate_constituents(fund.constituents)
    residual = max(0.0, 1 - math.fsum(result["weight"]))
    if residual > 1e-12:
        result = pd.concat([result, pd.DataFrame([{
            "constituent_id": f"etf-other:{fund.fund_id}", "name": f"{fund.name} / Other",
            "ticker": "", "isin": "", "weight": residual,
        }])], ignore_index=True)
    return result


def constituent_resolver(holdings: pd.DataFrame):
    """Index source identities once for large fund snapshots."""
    by_isin, by_ticker, by_id = {}, {}, {}
    for row in holdings.to_dict('records'):
        by_id[row['id']] = row
        if row.get('isin'):
            by_isin.setdefault(row['isin'], []).append(row)
        if row.get('ticker'):
            by_ticker.setdefault(row['ticker'], []).append(row)

    def resolve(constituent):
        analysis_asset = constituent.get('analysis_asset_id')
        if isinstance(analysis_asset, str) and analysis_asset:
            return analysis_asset, constituent['analysis_asset_name']
        company_asset = constituent.get('company_asset_id')
        if isinstance(company_asset, str) and company_asset:
            if company_asset not in by_id:
                raise DataError('A reviewed company link points to an unavailable asset')
            return company_asset, by_id[company_asset]['name']
        isin, ticker = constituent['isin'], constituent['ticker']
        matches = by_isin.get(isin, [])
        if not matches and ticker:
            matches = [r for r in by_ticker.get(ticker, []) if not isin or r.get('isin', '') in ('', isin)]
        if len({r['id'] for r in matches}) > 1:
            raise DataError(f"Several asset IDs match ETF constituent {ticker}; use one stable asset ID across its positions.")
        if matches:
            return matches[0]['id'], matches[0]['name']
        if constituent['constituent_id'] in by_id:
            raise DataError(f"ETF constituent ID {constituent['constituent_id']} conflicts with another instrument; edit the constituent ID.")
        return constituent['constituent_id'], constituent['name']

    return resolve


def resolve_constituent_asset(constituent: dict, holdings: pd.DataFrame) -> tuple[str, str]:
    return constituent_resolver(holdings)(constituent)


def resolved_constituents(funds, holdings):
    resolve = constituent_resolver(holdings)
    for fund in funds:
        for row in constituent_records(fund.constituents):
            yield resolve(row)[0], row


def add_provider_classifications(result, asset_id, row):
    for column, taxonomy in [('sector', 'sector'), ('country', 'geography'),
                             ('issuer', 'issuer'), ('market_currency', 'currency'),
                             ('maturity_band', 'maturity'), ('credit_rating', 'credit_quality')]:
        label = row.get(column)
        if isinstance(label, str) and label.strip() and label not in {'Unknown', '-'}:
            result.setdefault(asset_id, {}).setdefault(taxonomy, ((label.strip(),),))


def fund_classifications(classifications, funds, holdings):
    """Provider sector/country paths are fallbacks; local taxonomies always win."""
    result = copy_classifications(classifications)
    for asset_id, row in resolved_constituents(funds, holdings):
        add_provider_classifications(result, asset_id, row)
    return result


def _component_columns(fund, resolve):
    components = {column: [] for column in ('asset_id', 'asset_name', 'ticker', 'isin',
                                             'instrument_type', 'exposure_kind', 'source_type')}
    weights = []
    fallback = fund_kind(fund)
    for constituent in constituent_records(fund_breakdown(fund)):
        asset, name = resolve(constituent)
        interpreted = interpret_constituent(constituent, fallback_kind=fallback)
        components['asset_id'].append(asset)
        components['asset_name'].append(name)
        components['ticker'].append(interpreted.ticker)
        components['isin'].append(interpreted.isin)
        components['instrument_type'].append(interpreted.kind)
        components['exposure_kind'].append(interpreted.exposure_kind)
        components['source_type'].append('etf_other' if interpreted.residual else 'etf_constituent')
        weights.append(interpreted.weight)
    return components, weights


def expand_etfs(exposures: pd.DataFrame, funds: list[FundSnapshot], holdings: pd.DataFrame, *, additional_measures=(),
                resolved_components=None, output_columns=None) -> pd.DataFrame:
    measures = ("value", *additional_measures)
    columns = list(dict.fromkeys([*exposures.columns, "instrument_type", "exposure_kind"]))
    if output_columns is not None:
        required = set(output_columns).union(measures)
        columns = [column for column in columns if column in required]
    resolve = constituent_resolver(holdings)
    if exposures.empty:
        return pd.DataFrame(columns=columns)
    # A caller can reuse the validated components across amounts and account scopes
    # within one revision of the fund catalog and the full holdings resolver.
    by_fund = {} if resolved_components is None else resolved_components
    result = {column: [] for column in columns}
    missing = float("nan")
    for source in exposures.to_dict("records"):
        fund = matching_fund(source, funds)
        if fund is None:
            for column, values in result.items():
                values.append(source.get(column, missing))
            continue
        key = id(fund) if resolved_components is None else (fund.isin, fund.fund_id)
        if key not in by_fund:
            by_fund[key] = _component_columns(fund, resolve)
        components, weights = by_fund[key]
        source["direct_or_indirect"] = "indirect"
        source["wkn"] = ""
        # Fund quantities, quotes and targets do not describe its constituents.
        for column in ("shares", "acquisition_price", "current_price", "fx_to_reporting", "target_allocation", "portfolio_weight"):
            source[column] = missing
        amounts = {key: source[key] for key in measures}
        for column, values in result.items():
            if column in amounts:
                amount = amounts[column]
                values.extend(amount * weight for weight in weights)
            elif column in components:
                values.extend(components[column])
            else:
                values.extend([source.get(column, missing)] * len(weights))
    return pd.DataFrame(result)


def effective_exposure_table(exposures: pd.DataFrame) -> pd.DataFrame:
    columns = ["Asset", "Ticker", "Direct", "ETF-derived", "Total", "Allocation %"]
    if exposures.empty:
        return pd.DataFrame(columns=columns)
    records = []
    for _, rows in exposures.groupby("asset_id", sort=False):
        records.append({
            "Asset": rows.iloc[0]["asset_name"], "Ticker": rows.iloc[0]["ticker"],
            "Direct": rows.loc[rows["direct_or_indirect"] == "direct", "value"].sum(),
            "ETF-derived": rows.loc[rows["direct_or_indirect"] == "indirect", "value"].sum(),
            "Total": rows["value"].sum(),
        })
    result = pd.DataFrame(records)
    result["Allocation %"] = 100 * portfolio_weights(result["Total"])
    return result[columns].sort_values("Total", ascending=False, ignore_index=True)
