"""In-memory source decimals for validating saved holding snapshots across parser releases."""
from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType

import pandas as pd

ATTRIBUTE = 'holding_csv_summaries'


@dataclass(frozen=True)
class CsvSummary:
    instrument_id: str
    tokens: tuple[str, str, str]
    parsed: tuple[float, float | None, str]
    integer_shares: bool
    integer_price: bool

    @cached_property
    def parser_snapshots(self):
        from portfolio_app.portfolio.legacy_numeric import legacy_fingerprint, modern_fingerprint
        modes = dict(integer_shares=self.integer_shares, integer_price=self.integer_price)
        # The archived parsers used fused digit accumulation on Apple Silicon.
        # All fields of a candidate must come from the same parser and platform.
        variants = [legacy_fingerprint(*self.tokens, **modes, fused_digits=fused) for fused in (False, True)]
        variants.append(modern_fingerprint(*self.tokens, **modes))
        return tuple(dict.fromkeys(candidate for candidate in variants if candidate is not None))


class CsvSummaries(str):
    """An in-memory handle. JSON/Arrow exports see only the generic marker string."""
    def __new__(cls, rows):
        obj = super().__new__(cls, 'parsed CSV')
        object.__setattr__(obj, '_rows', MappingProxyType(dict(rows)))
        return obj

    def __setattr__(self, key, value):
        raise AttributeError('CSV source facts are immutable')

    def get(self, position_id):
        return self._rows.get(position_id)

    def without(self, positions):
        return CsvSummaries((key, source) for key, source in self._rows.items() if key not in positions)

    def __eq__(self, other):
        return self is other or isinstance(other, CsvSummaries) and self._rows == other._rows

    def __ne__(self, other):
        return not self == other

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return type(self), (dict(self._rows),)


class CostRow(dict):
    """Detached row that keeps its CSV source without adding a serializable field."""
    def __init__(self, values=(), source=None):
        super().__init__(values)
        self.csv_summary = source


def source_for(row):
    if isinstance(row, pd.Series):
        sources = row.attrs.get(ATTRIBUTE)
        if isinstance(sources, CsvSummaries):
            source = sources.get(row.get('position_id', row.name))
            if source is not None and row.get('id') == source.instrument_id:
                return source
        return None
    source = getattr(row, 'csv_summary', None)
    return source if source is not None and row.get('id') == source.instrument_id else None


def cost_records(frame):
    sources = frame.attrs.get(ATTRIBUTE)
    if not isinstance(sources, CsvSummaries) or not {'cost_basis_details', 'purchase_history'}.intersection(frame.columns):
        return frame.to_dict('records')
    records = frame.to_dict('records', into=CostRow)
    for row in records:
        row.csv_summary = sources.get(row.get('position_id'))
    return records
