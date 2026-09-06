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
