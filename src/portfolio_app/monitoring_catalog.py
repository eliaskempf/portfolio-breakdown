"""Broad equity benchmarks and portfolio-specific choices for monitoring."""
from portfolio_app.futures_chart_reference import ChartReference, REFERENCES
from portfolio_app.msci_history import INDEX_NAMES


BENCHMARKS = {f'MSCI:{code}:NETR:{currency}': ChartReference(
    f'MSCI:{code}:NETR:{currency}', name, currency, 'Net total return', source='MSCI')
    for code, name in INDEX_NAMES.items() for currency in ('EUR', 'USD', 'GBP')}
KNOWN_REFERENCES = {**REFERENCES, **BENCHMARKS}


def portfolio_instruments(holdings):
    """Unique tickers of currently held market-priced assets, never cash or derivatives."""
    choices = {}
    for _, row in holdings.iterrows():
        symbol = row.get('ticker')
        if (isinstance(symbol, str) and symbol.strip() and row.get('shares', 1) > 0
                and row.get('instrument_type') not in {'cash', 'future', 'futures', 'option'}):
            choices.setdefault(symbol, row['name'])
    return dict(sorted(choices.items(), key=lambda item: (item[1].casefold(), item[0])))
