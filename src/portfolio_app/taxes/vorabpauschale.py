"""German private-investor estimates. Pure Decimal calculations, no cash postings.

Rules: InvStG §§18,20; BMF 21 May 2019, paragraphs 18.2–18.12;
EStG §32d(1); SolzG §4. Public source links accompany the annual parameters.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN, localcontext

from portfolio_app.portfolio.holdings import DataError

D = Decimal
ZERO = D(0)
CENT = D('.01')
LAW = 'https://www.gesetze-im-internet.de/invstg_2018/__18.html'
GUIDANCE = 'https://www.bzst.de/SharedDocs/BMF/DE/Downloads/bmf_schreiben_20190521_InvStG_18_anwendungsfragen.pdf?__blob=publicationFile&v=1'
RATE_SOURCE = 'https://statistiken.bundesbank.de/statistiken-de/zeitreihen/BBSIS/D.I.ZAR.ZI.EUR.S1311.B.A604.R15XX.R.A.A._Z._Z.A'
# First published observation of each year in the Bundesbank series linked from
# https://www.bundesbank.de/de/statistiken/banken-und-andere-finanzielle-unternehmen/investmentgesellschaften
# Rates are percentages here, converted to fractions below. Never extrapolate.
_RATE_ROWS = (
    (2018, '.87', '2019-01-02'), (2019, '.52', '2020-01-02'),
    (2020, '.07', '2021-01-04'), (2021, '-.45', '2022-01-03'),
    (2022, '-.05', '2023-01-02'), (2023, '2.55', '2024-01-02'),
    (2024, '2.29', '2025-01-02'), (2025, '2.53', '2026-01-02'),
    (2026, '3.20', '2027-01-04'),
)
EXEMPTIONS = (ZERO, D('.15'), D('.30'), D('.60'), D('.80'))
CHURCH_RATES = (ZERO, D('.08'), D('.09'))


@dataclass(frozen=True)
class AnnualRule:
    year: int
    basis_rate: Decimal
    receipt_date: str
    source: str = RATE_SOURCE


RULES = {y: AnnualRule(y, D(b)/100, receipt) for y, b, receipt in _RATE_ROWS}


def number(value, label='Amount', *, negative=False):
    try:
        amount = D(str(value))
        if not amount.is_finite() or (not negative and amount < 0):
            raise ValueError
        return amount
    except (ValueError, ArithmeticError):
        raise DataError(f'{label} must be a finite {"number" if negative else "nonnegative number"}.') from None


def cents(amount):
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class TaxLot:
    quantity: Decimal
    acquired: str | None  # Original acquisition date; None never means January.

    def __post_init__(self):
        object.__setattr__(self, 'quantity', number(self.quantity, 'Shares'))
        if self.acquired:
            date.fromisoformat(self.acquired)


@dataclass(frozen=True)
class FundInputs:
    year: int
    start_price: Decimal | None = None
    end_price: Decimal | None = None
    distributions: Decimal | None = None  # Whole-year gross EUR per share.
    exemption: Decimal = ZERO
    basis: str = 'Market-price estimate'
    issues: tuple[str, ...] = ()

    def __post_init__(self):
        if self.year not in RULES:
            raise DataError('No official Basiszins is registered for this year.')
        for field in ('start_price', 'end_price', 'distributions', 'exemption'):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, number(value, field.replace('_', ' ')))
        if self.exemption not in EXEMPTIONS:
            raise DataError('Unsupported Teilfreistellung.')


@dataclass(frozen=True)
class FundResult:
    gross: Decimal | None
    taxable: Decimal | None
    per_share: Decimal | None = None
    weighted_shares: Decimal | None = None
    base_return: Decimal | None = None
    issues: tuple[str, ...] = ()


def calculate_fund(inputs: FundInputs, lots: tuple[TaxLot, ...], *, ownership_issues=()):
    """Sum unrounded lot contributions, then round once per depot/share class."""
    with localcontext() as context:
        context.prec = 60
        issues = list(dict.fromkeys((*ownership_issues, *inputs.issues)))
        if issues:
            return FundResult(None, None, issues=tuple(issues))
        rule = RULES[inputs.year]
        if not sum((lot.quantity for lot in lots), ZERO) or rule.basis_rate <= 0:
            return FundResult(ZERO, ZERO, ZERO, ZERO, ZERO)
        for field, label in (('start_price', 'Year-start price'), ('end_price', 'Year-end price'),
                             ('distributions', 'Annual distributions')):
            if getattr(inputs, field) is None:
                issues.append(label + ' missing')
        for lot in lots:
            if lot.quantity and not lot.acquired:
                issues.append('Original acquisition month missing')
            elif lot.acquired and int(lot.acquired[:4]) > inputs.year:
                issues.append('Acquisition is after the calculation year')
        if issues:
            return FundResult(None, None, issues=tuple(dict.fromkeys(issues)))
        base = min(inputs.start_price * D('.7') * rule.basis_rate,
                   inputs.end_price - inputs.start_price + inputs.distributions)
        per_share = max(ZERO, base - inputs.distributions)
        weighted = sum((lot.quantity * (D(13-int(lot.acquired[5:7]))/12
                        if int(lot.acquired[:4]) == inputs.year else D(1)) for lot in lots if lot.quantity), ZERO)
        gross = cents(per_share * weighted)
        return FundResult(gross, gross * (1-inputs.exemption), per_share, weighted, base)


@dataclass(frozen=True)
class TaxResult:
    taxable: Decimal
    allowance_used: Decimal
    base: Decimal
    capital_tax: Decimal
    solidarity: Decimal
    church_tax: Decimal

    @property
    def total(self):
        return self.capital_tax + self.solidarity + self.church_tax


def estimate_tax(taxable, allowance=ZERO, church_rate=ZERO):
    taxable, allowance, church_rate = (number(x) for x in (taxable, allowance, church_rate))
    if church_rate not in CHURCH_RATES:
        raise DataError('Church tax must be 0%, 8% or 9%.')
    with localcontext() as context:
        context.prec = 60
        used = min(allowance, taxable)
        base = taxable-used
        capital = cents(base/(4+church_rate))
        # SolzG §4 expressly discards fractions of a cent.
        solidarity = (capital*D('.055')).quantize(CENT, rounding=ROUND_DOWN)
        return TaxResult(taxable, used, base, capital, solidarity, cents(capital*church_rate))
