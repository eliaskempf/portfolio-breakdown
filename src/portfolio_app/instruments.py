"""Public listing search, independent of holdings and Streamlit."""

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yfinance as yf

from portfolio_app.display_names import display_name


@dataclass(frozen=True)
class Instrument:
    ticker: str
    name: str
    exchange: str = ""
    kind: str = "EQUITY"
    isin: str = ""
    currency: str = ""

    @property
    def label(self) -> str:
        return f"{display_name(self.name)} · {self.ticker} · {self.exchange or 'Exchange unavailable'} · {self.kind}"


# Public listing metadata, not portfolio holdings. VanEck trading information:
# https://www.vaneck.com/uk/en/library/fact-sheets/smh-fact-sheet.pdf
CATALOG = (
    *(Instrument(ticker, "VanEck Semiconductor UCITS ETF", exchange, "ETF", "IE00BMC38736", currency)
      for ticker, exchange, currency in (
          ("VVSM.DE", "Xetra", "EUR"), ("SMH.L", "London", "USD"),
          ("SMGB.L", "London", "GBP"), ("SMH.MI", "Milan", "EUR"), ("SMH.PA", "Paris", "EUR"),
      )),
    Instrument("NVDA", "NVIDIA Corporation", "NASDAQ", isin="US67066G1040", currency="USD"),
    Instrument("ENR.DE", "Siemens Energy AG", "Xetra", isin="DE000ENER6Y0", currency="EUR"),
    Instrument("TSM", "Taiwan Semiconductor Manufacturing Company ADR", "NYSE", isin="US8740391003", currency="USD"),
    Instrument("ANET", "Arista Networks", "NYSE", isin="US0404132054", currency="USD"),
)


def normalized_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", query).casefold().split())[:120]


def catalog_search(query: str) -> list[Instrument]:
    query = normalized_query(query)
    if len(query) < 2:
        return []
    tokens = re.findall(r"[a-z0-9]+", query)
    if not tokens:
        return []
    matches = []
    for listing in CATALOG:
        words = f"{listing.name} {listing.ticker} {listing.isin} {listing.exchange}".casefold()
        if listing.isin == "IE00BMC38736":
            words += " van eck semiconductors semiconductor chips chip smh ucits etf"
        if all(token in words for token in tokens):
            matches.append(listing)
    return sorted(matches, key=lambda listing: listing.ticker.casefold() != query)


def merge_search_results(query: str, local: list[Instrument], remote: list[Instrument]) -> list[Instrument]:
    # Prefer complete catalog metadata when Yahoo abbreviates the same listing.
    by_ticker = {item.ticker: item for item in local}
    for item in remote:
        by_ticker.setdefault(item.ticker, item)
    exact = normalized_query(query).upper()
    return sorted(by_ticker.values(), key=lambda item: item.ticker != exact)[:24]


def result_groups(results: list[Instrument]) -> list[dict]:
    groups = {}
    for item in results:
        key = item.isin or item.ticker
        if key not in groups:
            groups[key] = {"name": display_name(item.name), "kind": {"ETF": "ETF", "CRYPTOCURRENCY": "Crypto"}.get(item.kind, "Equity"),
                           "isin": item.isin, "ucits": "UCITS" in item.name.upper(), "listings": []}
        groups[key]["listings"].append({"ticker": item.ticker, "exchange": item.exchange or "Exchange unavailable", "currency": item.currency})
    return list(groups.values())[:10]


def valid_isin(value: object) -> str:
    """Return a normalized ISIN only when its format and check digit are valid."""
    isin = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        return ""
    digits = "".join(str(ord(char) - 55) if char.isalpha() else char for char in isin)
    total = 0
    for index, digit in enumerate(reversed(digits)):
        number = int(digit) * (2 if index % 2 else 1)
        total += number // 10 + number % 10
    return isin if total % 10 == 0 else ""


def normalize_results(quotes: list[dict]) -> list[Instrument]:
    results = {}
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        ticker = str(quote.get("symbol") or "").strip().upper()
        kind = str(quote.get("quoteType") or "").upper()
        if not ticker or kind not in {"EQUITY", "ETF", "CRYPTOCURRENCY"} or ticker in results:
            continue
        results[ticker] = Instrument(
            ticker=ticker,
            name=str(quote.get("longname") or quote.get("shortname") or ticker).strip(),
            exchange=str(quote.get("exchDisp") or quote.get("exchange") or ""),
            kind=kind,
            isin=valid_isin(quote.get("isin")),
            currency=str(quote.get("currency") or ""),
        )
    return list(results.values())


def parse_isin_candidates(payload: str) -> list[str]:
    """Read the suggestion endpoint's quoted fields without executing JavaScript."""
    quoted = r'"(?:[^"\\]|\\.)*"'
    rows = re.findall(r"new Array\((" + quoted + r"(?:\s*,\s*" + quoted + r")*)\)", payload)
    candidates = []
    for row in rows:
        try:
            fields = json.loads("[" + row + "]")
        except ValueError:
            continue
        if len(fields) < 3 or fields[1] not in {"Stocks", "ETFs", "Funds"}:
            continue
        for token in fields[2].split("|"):
            if (isin := valid_isin(token)) and isin not in candidates:
                candidates.append(isin)
    return candidates


def lookup_isin_candidates(query: str) -> list[str]:
    # Same public endpoint used by yfinance.get_isin, but with bounded requests
    # and explicit candidates instead of accepting its loose name fallback.
    url = "https://markets.businessinsider.com/ajax/SearchController_Suggest?" + urlencode({"max_results": 10, "query": query})
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as response:
        payload = response.read(500_001)
    if len(payload) > 500_000:
        return []
    return parse_isin_candidates(payload.decode("utf-8"))


class InstrumentSearch:
    def __init__(self, cache_dir: Path):
        cache_dir.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(cache_dir))
        self.last_error = ""

    def search(self, query: str) -> list[Instrument]:
        query = query.strip()
        if not query:
            return []
        local = catalog_search(query)
        self.last_error = ""
        try:
            response = yf.Search(query, max_results=20, news_count=0, lists_count=0,
                                 include_cb=False, timeout=10)
            results = normalize_results(response.quotes)
        except Exception:
            self.last_error = "Live search is temporarily unavailable. Showing matching listings from the local catalog."
            if local:
                return local
            raise
        # An exact ISIN search associates the returned listing with that identifier.
        if isin := valid_isin(query):
            results = [replace(result, isin=isin) for result in results]
        return merge_search_results(query, local, results)

    def details(self, listing: Instrument) -> Instrument:
        """Unavailable optional metadata must not prevent adding the listing."""
        if listing.isin and listing.currency:
            return listing
        ticker = yf.Ticker(listing.ticker)
        try:
            info = ticker.get_info() or {}
        except Exception:
            info = {}
        result = replace(listing, currency=str(info.get("currency") or listing.currency))
        if listing.kind == "CRYPTOCURRENCY":
            return result
        if result.isin:
            return result
        for query in dict.fromkeys([listing.ticker, listing.name]):
            try:
                candidates = lookup_isin_candidates(query)
                # Require a reverse lookup to this exact exchange-qualified symbol;
                # never substitute an ordinary share for an ADR or another ETF.
                for candidate in candidates[:3]:
                    if any(item.ticker == listing.ticker for item in self.search(candidate)):
                        return replace(result, isin=candidate)
            except Exception:
                break
        return result
