"""Reviewed quote-feed exclusions, shared by search, spot prices and history."""

# Reviewed 2026-10-07 against the exchange's own quote and NAV:
# https://www.londonstockexchange.com/stock/0E2B/amundi
# The EQS line repeats an obsolete price with current dates. Do not infer a
# split factor or silently substitute a different trading venue for a holding.
UNRELIABLE_QUOTES = {
    '0E2B.IL': (
        'The 0E2B.IL quote feed repeats an unreliable price. '
        'Use Connect live prices to select CSH2.PA (Paris) or SMART.MI (Milan) '
        'for ISIN LU1190417599, or enter a dated manual price.'
    ),
}


def quote_issue(ticker: str) -> str:
    return UNRELIABLE_QUOTES.get(ticker.strip().upper(), '')
