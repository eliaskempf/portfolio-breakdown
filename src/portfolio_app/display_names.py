"""Conservative display labels; never used as instrument identities or persisted."""

import re
import unicodedata


ACRONYMS = {"ADR", "ADS", "AI", "AMD", "ASML", "AT&T", "BASF", "BMW", "BP", "BYD",
            "ETF", "ETN", "EUR", "GBP", "HSBC", "IBM", "IT", "LG", "LVMH", "MSCI",
            "KLA", "NXP", "SAP", "SK", "S&P", "TSMC", "UBS", "UCITS", "USD"}
BRANDS = {"nvidia": "Nvidia", "vaneck": "VanEck", "ishares": "iShares",
          "invesco": "Invesco", "xtrackers": "Xtrackers", "asml": "ASML", "kla": "KLA", "nxp": "NXP", "onsemi": "onsemi"}
# Public company aliases, matched as complete names (with optional share
# qualifiers). Never match merely "Taiwan", which could be another company.
ALIASES = {
    "taiwan semiconductor manufacturing": "TSMC",
    "taiwan semiconductor manufacturing co l": "TSMC",
    "amazon.com": "Amazon", "advanced micro devices": "AMD",
    "meta platforms": "Meta", "vertiv holdings": "Vertiv",
    "cadence design systems": "Cadence", "asml holding": "ASML",
    "space exploration technologies": "SpaceX", "stmicroelectronics": "STMicroelectronics",
    "on semiconductor": "onsemi", "sk hynix": "SK Hynix",
}
# Only remove trailing legal forms, including before explicit share qualifiers.
LEGAL_END = re.compile(
    r"(?:,?\s+(?:incorporated|inc\.?|corporation|corp\.?|limited|ltd\.?|plc|ag|"
    r"co\.?\s+ltd\.?|company|co\.?|nv|n\.v\.))(?=$|\s+(?:[-–—]\s*)?(?:class\b|ADR\b|ADS\b))",
    re.IGNORECASE,
)


def display_name(name: str) -> str:
    """Tidy provider capitalization and legal suffixes, preserving share details.

    Mixed-case custom labels remain as entered. Acronyms, UCITS, currency,
    accumulation/distribution and share-class qualifiers are retained.
    """
    original = " ".join(unicodedata.normalize("NFKC", name).split())
    clean = original
    while (shorter := LEGAL_END.sub("", clean)) != clean:
        clean = shorter
    clean = clean or original
    qualifier = re.search(r"\s+(?:[-–—]\s*)?(?:class\b|ADR\b|ADS\b).*$", clean, re.IGNORECASE)
    base = clean[:qualifier.start()] if qualifier else clean
    if alias := ALIASES.get(base.casefold()):
        return alias + (" " + display_name(qualifier[0].strip()) if qualifier else "")
    uniform_case = clean.isupper() or clean.islower()
    words = []
    for word in clean.split():
        if word.casefold() in BRANDS:
            word = BRANDS[word.casefold()]
        elif uniform_case and word.upper() in ACRONYMS:
            word = word.upper()
        elif uniform_case:
            # Preserve single-letter classes and common instrument abbreviations.
            word = re.sub(r"[A-Za-z]+", lambda match: match[0].upper()
                          if match[0].upper() in ACRONYMS or len(match[0]) == 1
                          else match[0].capitalize(), word)
        words.append(word)
    return " ".join(words)


def instrument_name(row) -> str:
    """A presentation-only label; full source names and identities are retained."""
    get = row.get if hasattr(row, "get") else lambda key, default="": getattr(row, key, default)
    short = get("short_name", "")
    if isinstance(short, str) and short.strip():
        return short.strip()
    return compact_fund_name(get("name", ""))


def compact_fund_name(name: str) -> str:
    """Short chart labels; full share-class information stays in position details.

    Only compact explicitly named funds. Do not guess truncated provider names,
    or remove currency from a hedging qualifier or leverage from an index.
    """
    original = display_name(name)
    original = re.sub(r"\b(MSCI Europe Momentum)\s+Factor\b", r"\1", original, flags=re.I)
    if not re.search(r"\b(?:UCITS|ETF)\b", original, flags=re.I):
        return original
    clean = re.sub(r"^Amundi Index Solutions\s*[-–—]\s*(?=Amundi\b)", "", original, flags=re.I)
    clean = re.sub(r"\b(?:UCITS\s+ETF|UCITS|ETF)\b", "", clean, flags=re.I)
    # Peel off trailing listing currency, distribution policy and share codes.
    # A suffix such as 'EUR Hedged' stops the loop and remains intact.
    suffix = re.compile(
        r"(?:[\s(]+|[-–—]\s*)(?:USD|EUR|GBP|CHF|JPY|CAD|AUD|"
        r"Acc(?:umulating|umulation)?|Dist(?:ributing|ribution)?|[1-9][CD]|[ACDI])\)?\.?$",
        re.I,
    )
    clean = clean.strip()
    while (shorter := suffix.sub("", clean).strip()) != clean:
        clean = shorter
    clean = re.sub(r"\(\s*\)", "", clean)
    return " ".join(clean.strip(" -–—").split()) or original


def named_holdings(holdings):
    result = holdings.copy()
    result["name"] = [instrument_name(row) for _, row in result.iterrows()]
    return result
