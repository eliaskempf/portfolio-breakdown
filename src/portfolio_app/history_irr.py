"""XIRR root isolation on the entire positive discount-factor domain.

For integer day offsets n, NPV is a sparse polynomial sum(a[n]*x**n), x>0.
Use z=log(x) and remove the positive lowest exponential. Differentiation then
removes its constant term. Recursively isolating derivative roots partitions the
real line into monotone intervals, including narrow roots a sample grid misses.
Coefficient dominance gives finite bounds outside which no roots exist. Close
to a stationary zero we withhold a result, rather than assert uniqueness of an
ill-conditioned/tangent root. Ordinary one-sign-change schedules bypass recursion.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext, DecimalException
import math

from portfolio_app.history_diagnostics import HistoryIssue as Issue


@dataclass(frozen=True)
class IrrResult:
    value: float | None
    reason: str = ''


class UncertainRoot(ValueError):
    pass


def _normalized(terms):
    first = terms[0][0]
    return tuple((n-first, a) for n,a in terms if a)


def _value(terms, z):
    # All exponentials <= 1; scale also makes residuals relative to the schedule.
    shift = max(n*z for n,_ in terms)
    scale = max(abs(a) for _,a in terms)
    values = [(a/scale)*(n*z-shift).exp() for n,a in terms]
    return sum(values, Decimal(0)), sum(map(abs,values), Decimal(0))


def _bounds(terms):
    nlast, last = terms[-1]
    first = terms[0][1]
    count = Decimal(2*(len(terms)-1))
    low = min(Decimal(0), *( (abs(first)/(count*abs(a))).ln()/n for n,a in terms[1:] ))
    high = max(Decimal(0), *( (count*abs(a)/abs(last)).ln()/(nlast-n) for n,a in terms[:-1] ))
    return low-1, high+1


def _bracket(terms, low, high):
    left = _value(terms,low)[0]
    right = _value(terms,high)[0]
    if not left: return low
    if not right: return high
    if (left>0) == (right>0):
        raise UncertainRoot('Root bracket does not change sign')
    # Decimal bisection refines even where double precision cannot separate roots.
    for _ in range(256):
        middle=(low+high)/2
        value, _ = _value(terms,middle)
        if not value or high-low <= Decimal('1e-45')*max(Decimal(1),abs(middle)):
            return middle
        if (left>0) == (value>0):
            low,left=middle,value
        else:
            high=middle
    raise UncertainRoot('Root interval did not converge')


def _roots(terms):
    terms=_normalized(terms)
    signs=[a>0 for _,a in terms]
    changes=sum(a!=b for a,b in zip(signs,signs[1:]))
    if not changes:
        return []
    low,high=_bounds(terms)
    if changes==1:
        return [_bracket(terms,low,high)]
    if len(terms)>128:
        raise UncertainRoot('Mixed-sign schedule exceeds supported isolation complexity')
    stationary=_roots(tuple((n,a*n) for n,a in terms[1:]))
    cuts=[low, *(root for root in stationary if low<root<high), high]
    evaluated=[_value(terms,cut) for cut in cuts]
    for value,scale in evaluated[1:-1]:
        if abs(value) <= Decimal('1e-30')*scale:
            raise UncertainRoot('Tangent or numerically inseparable roots')
    return [_bracket(terms,left,right) for left,right,(vl,_),(vr,_) in
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
        except (UncertainRoot,DecimalException,ValueError,OverflowError,RecursionError):
            return IrrResult(None,Issue('A unique XIRR cannot be established with reliable numerical conditioning','irr_unresolved'))
