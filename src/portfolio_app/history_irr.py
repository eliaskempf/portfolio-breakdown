"""XIRR root isolation on the entire positive discount-factor domain.

For integer day offsets n, NPV is a sparse polynomial sum(a[n]*x**n), x>0.
Use z=log(x). A verified investment-balance certificate handles long schedules
in linear space. Otherwise differentiate after removing an exponential at a
coefficient sign boundary. Recursively isolating derivative roots partitions the
real line into monotone intervals, including narrow roots a sample grid misses.
Coefficient dominance gives finite bounds outside which no roots exist. Close
to a stationary zero we withhold a result, rather than assert uniqueness of an
ill-conditioned/tangent root. Ordinary one-sign-change schedules bypass recursion.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext, DecimalException, ROUND_FLOOR, ROUND_CEILING
import math

from portfolio_app.history_diagnostics import HistoryIssue as Issue


@dataclass(frozen=True)
class IrrResult:
    value: float | None
    reason: str = ''


class UncertainRoot(ValueError):
    pass


class IsolationLimit(UncertainRoot):
    """The bounded solver exhausted work, independently of evidence coverage."""


@dataclass
class _Budget:
    remaining: int = 100_000

    def take(self, count):
        self.remaining -= count
        if self.remaining < 0:
            raise IsolationLimit('XIRR root isolation reached its calculation limit')


def _normalized(terms):
    first = terms[0][0]
    return tuple((n-first, a) for n,a in terms if a)


def _value(terms, z, budget=None):
    if budget is not None:
        budget.take(len(terms))
    # All exponentials <= 1; scale also makes residuals relative to the schedule.
    shift = max(n*z for n,_ in terms)
    scale = max(abs(a) for _,a in terms)
    values = [(a/scale)*(n*z-shift).exp() for n,a in terms]
    return sum(values, Decimal(0)), sum(map(abs,values), Decimal(0))


def _bounds(terms, budget=None):
    if budget is not None:
        budget.take(2*len(terms))
    nlast, last = terms[-1]
    first = terms[0][1]
    count = Decimal(2*(len(terms)-1))
    low = min(Decimal(0), *( (abs(first)/(count*abs(a))).ln()/n for n,a in terms[1:] ))
    high = max(Decimal(0), *( (count*abs(a)/abs(last)).ln()/(nlast-n) for n,a in terms[:-1] ))
    return low-1, high+1


def _bracket(terms, low, high, budget=None):
    left = _value(terms,low,budget)[0]
    right = _value(terms,high,budget)[0]
    if not left: return low
    if not right: return high
    if (left>0) == (right>0):
        raise UncertainRoot('Root bracket does not change sign')
    # This only refines a sign-changing bracket. Callers must independently
    # establish uniqueness through isolation or the investment certificate.
    from scipy.optimize import brentq
    scale=max(abs(a) for _,a in terms)
    floating=tuple((n,float(a/scale)) for n,a in terms)
    def residual(z):
        if budget is not None:
            budget.take(len(floating))
        shift=max(n*z for n,_ in floating)
        return math.fsum(a*math.exp(n*z-shift) for n,a in floating)
    try:
        middle=Decimal(str(brentq(residual,float(low),float(high),xtol=1e-15)))
    except IsolationLimit:
        raise
    except (ValueError,OverflowError):
        middle=(low+high)/2
    for _ in range(256):
        value,_=_value(terms,middle,budget)
        if not value:
            return middle
        if (left>0) == (value>0):
            low,left=middle,value
        else:
            high=middle
        shift=max(n*middle for n,_ in terms)
        if budget is not None:
            budget.take(len(terms))
        slope=sum(((a/scale)*n*(n*middle-shift).exp() for n,a in terms),Decimal(0))
        step=value/slope if slope else None
        if step is not None and abs(step)<=Decimal('1e-45')*max(Decimal(1),abs(middle)):
            return middle
        candidate=middle-step if step is not None else low
        middle=candidate if low<candidate<high else (low+high)/2
        if high-low<=Decimal('1e-45')*max(Decimal(1),abs(middle)):
            return middle
    raise UncertainRoot('Root interval did not converge')


def _prefix_bounds(terms, low, high, budget):
    """Outward-rounded bounds on every discounted prefix over a log interval.

    Decimal.exp is correctly rounded to nearest regardless of context rounding;
    its adjacent representable values therefore enclose the exact exponential.
    All other arithmetic uses directed rounding. One fixed positive scale keeps
    exponents nonpositive without changing signs or cancellation identities.
    """
    budget.take(2*len(terms))
    with localcontext() as context:
        context.rounding = ROUND_CEILING
        shift = max(Decimal(0), terms[-1][0]*high)
    lower = upper = Decimal(0)
    result = []
    for n, amount in terms:
        with localcontext() as context:
            context.rounding = ROUND_FLOOR
            lower_exp = max(Decimal(0), (n*low-shift).exp().next_minus())
        with localcontext() as context:
            context.rounding = ROUND_CEILING
            upper_exp = (n*high-shift).exp().next_plus()
        with localcontext() as context:
            context.rounding = ROUND_FLOOR
            lower += amount*(lower_exp if amount > 0 else upper_exp)
        with localcontext() as context:
            context.rounding = ROUND_CEILING
            upper += amount*(upper_exp if amount > 0 else lower_exp)
        result.append((lower, upper))
    return result


def _investment_root(terms, low, high, budget):
    """Certify a unique root using the signs of discounted invested balances.

    If r>0 is a root, synthetic division of P(x) by (x-r) has coefficients
    q[k] = -sum(a[i]*r**i, i<=k)/r**(k+1). If every proper prefix has the same
    strict sign at r, the quotient has one coefficient sign and no positive
    roots. Sparse gaps repeat the same prefix and need no dense polynomial.
    We enclose a root and every prefix using interval arithmetic, so a guessed
    rate or a small residual alone can never establish this certificate.
    """
    if (terms[0][1] > 0) == (terms[-1][1] > 0):
        return None
    candidate = _bracket(terms, low, high, budget)
    radius = Decimal('1e-32')*max(Decimal(1), abs(candidate))
    left, right = candidate-radius, candidate+radius
    at_left = _prefix_bounds(terms, left, left, budget)[-1]
    at_right = _prefix_bounds(terms, right, right, budget)[-1]
    if not ((at_left[1] < 0 < at_right[0]) or (at_right[1] < 0 < at_left[0])):
        return None
    prefixes = _prefix_bounds(terms, left, right, budget)[:-1]
    positive = terms[0][1] > 0
    if all(lower > 0 if positive else upper < 0 for lower, upper in prefixes):
        return candidate
    return None


def _roots(terms):
    return _isolate(terms, _Budget())


def _isolate(terms, budget, depth=0):
    terms=_normalized(terms)
    signs=[a>0 for _,a in terms]
    changes=sum(a!=b for a,b in zip(signs,signs[1:]))
    if not changes:
        return []
    low,high=_bounds(terms,budget)
    if changes==1:
        return [_bracket(terms,low,high,budget)]
    if depth == 0 and len(terms) > 32:
        certified = _investment_root(terms,low,high,budget)
        if certified is not None:
            return [certified]
    if depth >= 64:
        raise IsolationLimit('XIRR root isolation reached its calculation limit')
    # For G(z)=exp(-pivot*z)*F(z), derivative coefficients before pivot
    # reverse sign. Choosing the first sign boundary removes one variation,
    # so recursion depends on sign complexity, not the number of dated flows.
    boundary = next(i for i in range(1,len(terms)) if signs[i] != signs[i-1])
    pivot = terms[boundary][0]
    stationary=_isolate(tuple((n,a*(n-pivot)) for n,a in terms if n != pivot),budget,depth+1)
    cuts=[low, *(root for root in stationary if low<root<high), high]
    evaluated=[_value(terms,cut,budget) for cut in cuts]
    for value,scale in evaluated[1:-1]:
        if abs(value) <= Decimal('1e-30')*scale:
            raise UncertainRoot('Tangent or numerically inseparable roots')
    return [_bracket(terms,left,right,budget) for left,right,(vl,_),(vr,_) in
            zip(cuts,cuts[1:],evaluated,evaluated[1:]) if (vl>0)!=(vr>0)]


def solve_xirr(opening, ending, flows, start, end):
    days=(date.fromisoformat(end)-date.fromisoformat(start)).days
    if days<=0 or opening<0 or ending<0:
        return IrrResult(None,Issue('Annualization needs positive duration and nonnegative endpoint equity','duration'))
    with localcontext() as context:
        context.prec=80
        amounts={start:-opening}
        for flow in flows:
            if flow.amount is None:
                return IrrResult(None,Issue('Missing cash amount or dated FX','transaction'))
            amounts[flow.date]=amounts.get(flow.date,Decimal(0))-flow.amount
        amounts[end]=amounts.get(end,Decimal(0))+ending
        terms=tuple(((date.fromisoformat(stamp)-date.fromisoformat(start)).days,amount)
                    for stamp,amount in sorted(amounts.items()) if amount)
        if not terms:
            return IrrResult(None,Issue('A unique XIRR cannot be established for an empty net schedule','irr_unresolved'))
        try:
            roots=_roots(terms)
            if not roots:
                return IrrResult(None,Issue('No admissible XIRR solution for these cash flows','irr_none'))
            if len(roots)>1:
                return IrrResult(None,Issue('Multiple XIRR solutions; a unique return cannot be established','irr_multiple'))
            root=roots[0]
            value,scale=_value(_normalized(terms),root)
            if not scale or abs(value)/scale>Decimal('1e-25'):
                raise UncertainRoot('XIRR residual could not be verified')
            normalized=_normalized(terms)
            derivative=tuple((n,a*n) for n,a in normalized if n)
            slope,slope_scale=_value(derivative,root)
            if not slope_scale or abs(slope)/slope_scale<Decimal('1e-20'):
                raise UncertainRoot('XIRR is numerically ill-conditioned')
            result=math.expm1(float(-365*root))
            if not math.isfinite(result) or result<=-1:
                raise UncertainRoot('Return is outside the representable numeric range')
            # Verify the rate actually returned, not just its high-precision precursor.
            returned=-Decimal(str(math.log1p(result)))/365
            residual,scale=_value(normalized,returned)
            if abs(residual)/scale>Decimal('1e-8'):
                raise UncertainRoot('Returned rate loses numerical precision')
            return IrrResult(result)
        except IsolationLimit:
            return IrrResult(None,Issue('XIRR calculation limit reached; the full cash-flow schedule could not be verified','irr_complexity'))
        except (UncertainRoot,DecimalException,ValueError,OverflowError,RecursionError):
            return IrrResult(None,Issue('A unique XIRR cannot be established with reliable numerical conditioning','irr_unresolved'))
