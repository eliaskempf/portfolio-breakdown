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


# Public index symbols and issuer-verified listings (IE00B4L5Y983).
# https://www.ishares.com/uk/professionals/en/products/251882/SWDA
# Yahoo closes use the existing history service, including GBp normalization.
REFERENCES = MappingProxyType({item.ticker: item for item in (
    ChartReference('^GDAXI', 'DAX', 'EUR', 'Gross total return'),
    ChartReference('^STOXX50E', 'EURO STOXX 50', 'EUR', 'Price return'),
    ChartReference('^FTSE', 'FTSE 100', 'GBP', 'Price return'),
    *(ChartReference(ticker, 'iShares Core MSCI World UCITS ETF (Acc)', currency,
                     'Accumulating ETF tracking MSCI World Net', False)
      for ticker, currency in [('EUNL.DE', 'EUR'), ('IWDA.L', 'USD'), ('SWDA.L', 'GBP')]),
)})
PRODUCT_REFERENCES = MappingProxyType(dict(
    FDAX='^GDAXI', FDXM='^GDAXI', FDXS='^GDAXI', FESX='^STOXX50E', FTUK='^FTSE',
    FMWN='EUNL.DE', FMWO='IWDA.L', FMWB='SWDA.L'))


def reference_for(contract):
    """Never infer a custom underlying from a name or look-through holding."""
    product = CATALOG.get(contract.product)
    if (product is None or contract.exchange != 'EUREX'
            or contract.currency != product.currency
            or contract.return_convention != product.return_convention):
        return None
    return REFERENCES[PRODUCT_REFERENCES[contract.product]]
