"""Curated market-chart references; never inputs to valuation, risk or settlement."""
from dataclasses import dataclass
from types import MappingProxyType

from portfolio_app.derivative_market import CATALOG


@dataclass(frozen=True)
class ChartReference:
    ticker: str
    name: str
    currency: str
    convention: str
    index_points: bool = True
    source: str = 'Yahoo Finance'


# Direct index references. MSCI currency and NETR convention are verified
# against response metadata, never inferred from an ETF listing.
REFERENCES = MappingProxyType({item.ticker: item for item in (
    ChartReference('^GDAXI', 'DAX', 'EUR', 'Gross total return'),
    ChartReference('^STOXX50E', 'EURO STOXX 50', 'EUR', 'Price return'),
    ChartReference('^FTSE', 'FTSE 100', 'GBP', 'Price return'),
    *(ChartReference(f'MSCI:990100:NETR:{currency}', 'MSCI World', currency,
                     'Net total return', source='MSCI') for currency in ('EUR', 'USD', 'GBP')),
)})
PRODUCT_REFERENCES = MappingProxyType(dict(
    FDAX='^GDAXI', FDXM='^GDAXI', FDXS='^GDAXI', FESX='^STOXX50E', FTUK='^FTSE',
    FMWN='MSCI:990100:NETR:EUR', FMWO='MSCI:990100:NETR:USD', FMWB='MSCI:990100:NETR:GBP'))


def reference_for(contract):
    """Never infer a custom underlying from a name or look-through holding."""
    product = CATALOG.get(contract.product)
    if (product is None or contract.exchange != 'EUREX'
            or contract.currency != product.currency
            or contract.return_convention != product.return_convention):
        return None
    return REFERENCES[PRODUCT_REFERENCES[contract.product]]
