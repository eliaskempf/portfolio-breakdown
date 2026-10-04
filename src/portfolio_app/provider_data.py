"""Small validation helpers shared by official ETF adapters."""
from hashlib import sha256
from html.parser import HTMLParser
import math

import pandas as pd

from portfolio_app.etf import validate_constituents
from portfolio_app.holdings import DataError
from portfolio_app.instruments import valid_isin


class ProductHTML(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.meta, self.links, self.rows = {}, [], []
        self._row = None
        self.feed(content.decode('utf-8'))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            self.meta[attrs.get('name', '')] = attrs.get('content', '')
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
        if tag == 'tr':
            self._row = []

    def handle_data(self, data):
        if self._row is not None and data.strip():
            self._row.append(data.strip())

    def handle_endtag(self, tag):
        if tag == 'tr' and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def weight(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DataError('Provider weight must be a finite number')
    return float(value)


def security_id(provider, isin, fallback):
    isin = str(isin or '').strip().upper()
    if isin:
        if not valid_isin(isin):
            raise DataError('Invalid security ISIN in provider export')
        return 'isin:' + isin, isin
    if not fallback:
        raise DataError('Missing provider security identity')
    return provider + ':' + sha256(str(fallback).encode()).hexdigest()[:20], ''


def published_frame(records, *, partial_reason=''):
    """Never rescale published weights; an explicit partial view preserves Other.

    A small rounding excess can require a partial view. Larger excesses, short
    securities and malformed rows fail closed. Callers handle identified cash
    liabilities separately and must explain their resulting partial view.
    """
    if not records:
        raise DataError('No supported holdings in provider export')
    frame = pd.DataFrame(records)
    # Validate rows and identities before selecting a partial subset.
    for value in frame.weight:
        if not 0 <= weight(value) <= 1:
            raise DataError('Short or invalid security weights are unsupported')
    if frame.constituent_id.duplicated().any() or frame.name.fillna('').str.strip().eq('').any():
        raise DataError('Duplicate security identities or missing names in provider export')
    total = math.fsum(frame.weight)
    if total > 1 + 1e-12:
        if not partial_reason and total > 1.0001:
            raise DataError('Provider security weights exceed 100% beyond published rounding')
        partial_reason = partial_reason or 'published rounding exceeds 100%'
    if partial_reason:
        frame = frame.nlargest(10, 'weight').copy()
        if len(frame) == len(records) and total > 1 + 1e-12:
            raise DataError('Cannot form a valid partial holdings view')
        notes = f'Partial holdings: up to ten largest supported securities because {partial_reason}. Published whole-fund weights are not rescaled; the remainder stays in Other.'
    else:
        notes = 'Published whole-fund weights are not rescaled; any unreported holdings or rounding remainder stays in Other.'
    return validate_constituents(frame), notes
