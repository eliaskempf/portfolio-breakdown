"""Independent polynomial and NPV oracles for invented investor schedules."""
from datetime import date,timedelta
from decimal import Decimal as D
import math
import pytest
from scipy.optimize import brentq

from portfolio_app.personal_performance import ExternalFlow,xirr


def solve(amounts, days=None):
    days=days or [365*i for i in range(len(amounts))]
    stamps=[(date(2020,1,1)+timedelta(days=n)).isoformat() for n in days]
    flows=tuple(ExternalFlow(str(i),stamps[i],i,-D(str(amount))) for i,amount in enumerate(amounts[1:-1],1))
    # End-date external flow also allows testing a negative final net investor flow.
    if amounts[-1]<0:
        flows+= (ExternalFlow('end',stamps[-1],99,-D(str(amounts[-1]))),)
    return xirr(-D(str(amounts[0])),D(str(max(0,amounts[-1]))),flows,stamps[0],stamps[-1])


def test_unique_mixed_sign_root_matches_independent_polynomial():
    value,reason=solve([-100,40,-20,100])
    # Derivative in discount factor: 40-40x+300x² > 0 everywhere.
    assert 40**2-4*300*40<0
    discount=brentq(lambda x:-100+40*x-20*x*x+100*x**3,0,2,xtol=1e-14)
    assert not reason and value==pytest.approx(1/discount-1,rel=1e-10)
    assert abs(sum(a/(1+value)**i for i,a in enumerate([-100,40,-20,100])))<1e-9


def test_multiple_roots_and_no_roots_are_distinct():
    value,reason=solve([-100,350,-350,100])
    assert value is None and reason.kind=='irr_multiple'
    for x in (.5,1,2):
        assert -100+350*x-350*x*x+100*x**3==0
    value,reason=solve([-100,100,-100])
    assert value is None and reason.kind=='irr_none'  # Discriminant < 0.


def test_tangent_and_narrow_roots_never_select_an_arbitrary_rate():
    assert solve([-100,200,-100])[0] is None  # -(x-1)^2.
    # Exact Decimal coefficients: three narrowly separated positive roots.
    roots=[D('1'),D('1.000001'),D('1.000002')]
    coefficients=[-roots[0]*roots[1]*roots[2], sum(roots[i]*roots[j] for i,j in ((0,1),(0,2),(1,2))),-sum(roots),D(1)]
    value,reason=solve(coefficients)
    assert value is None and reason.kind in ('irr_multiple','irr_unresolved')


def test_irregular_days_scale_invariance_and_short_period():
    amounts=[-100,40,-20,130];days=[0,121,302,800]
    rate,reason=solve(amounts,days)
    expected=brentq(lambda r:sum(a/(1+r)**(d/365) for a,d in zip(amounts,days)),-.5,1)
    assert not reason and rate==pytest.approx(expected)
    assert solve([D(a)*D('1e40') for a in amounts],days)[0]==pytest.approx(rate)
    assert solve([-100,101],[0,1])[0]==pytest.approx(1.01**365-1)


def test_zero_rate_extreme_rates_and_all_zero_schedule():
    assert solve([-100,100])[0]==pytest.approx(0,abs=1e-12)
    assert solve([-100,1])[0]==pytest.approx(-.99)
    assert solve([-1,1000000])[0]==pytest.approx(999999)
    assert solve([0,0])[0] is None
    assert solve([-100,100],[0,0])[0] is None


@pytest.mark.parametrize('positive',[D('.25'),D('1.2'),D('3')])
def test_unique_positive_root_with_complex_pair_has_known_independent_answer(positive):
    # (x-p)*(x²-0.6x+4.09); quadratic roots are 0.3 ± 2i.
    coefficients=[-positive*D('4.09'),D('4.09')+positive*D('.6'),-positive-D('.6'),D(1)]
    result,reason=solve(coefficients)
    assert not reason and result==pytest.approx(float(1/positive-1),abs=1e-12)


@pytest.mark.parametrize('length', [91, 201, 501])
def test_long_schedule_certifies_known_unique_root_without_term_count_limit(monkeypatch, length):
    import portfolio_app.history_irr as module
    # (x-p)*(x²-0.6x+4.09)*(1+x+...+x^(length-1)). The latter factors
    # have no positive real roots, so the only admissible root is p.
    p = D('1.2')
    cubic = [-p*D('4.09'), D('4.09')+p*D('.6'), -p-D('.6'), D(1)]
    coefficients = [D(0)]*(length+3)
    for offset in range(length):
        for index, amount in enumerate(cubic):
            coefficients[offset+index] += amount
    original, calls = module._isolate, []
    def counted(terms, budget, depth=0):
        calls.append(len(terms))
        return original(terms, budget, depth)
    monkeypatch.setattr(module, '_isolate', counted)
    value, reason = solve(coefficients)
    assert not reason and value == pytest.approx(float(1/p-1), abs=1e-12)
    assert len(calls) < 10  # Preserve isolation without peeling ninety coefficients.


def test_long_irregular_savings_and_withdrawals_verify_full_schedule(monkeypatch):
    from decimal import localcontext
    import portfolio_app.history_irr as module
    days = [0, *(13*i+i%3 for i in range(1, 401)), 5300]
    amounts = [D(-1000), *(D(15) if i%3 == 0 else D(-30) for i in range(1, 401))]
    with localcontext() as context:
        context.prec = 80
        rate = D('.065')
        amounts.append(-sum(a*((1+rate).ln()*D(days[-1]-day)/365).exp()
                            for a, day in zip(amounts, days)))
    def no_recursive_isolation(*args, **kwargs):
        raise AssertionError('The positive invested-balance certificate should suffice')
    original = module._isolate
    def top_only(terms, budget, depth=0):
        if depth:
            no_recursive_isolation()
        return original(terms, budget, depth)
    monkeypatch.setattr(module, '_isolate', top_only)
    value, reason = solve(amounts, days)
    assert not reason and value == pytest.approx(.065, abs=1e-12)
    assert abs(sum(float(a)/(1+value)**(day/365) for a, day in zip(amounts, days))) < 1e-8


def test_long_multiple_root_schedule_never_passes_investment_certificate():
    # Positive geometric factor introduces no roots; roots remain .5, 1 and 2.
    coefficients = [D(0)]*204
    for offset in range(201):
        for i, amount in enumerate([-100, 350, -350, 100]):
            coefficients[offset+i] += amount
    value, reason = solve(coefficients, list(range(len(coefficients))))
    assert value is None and reason.kind == 'irr_multiple'


def test_calculation_limit_has_its_own_diagnostic(monkeypatch):
    import portfolio_app.history_irr as module
    original = module._Budget
    monkeypatch.setattr(module, '_Budget', lambda: original(remaining=0))
    value, reason = solve([-100, 40, -20, 100])
    assert value is None and reason.kind == 'irr_complexity'
    assert 'calculation limit' in str(reason)


def test_prefix_interval_bounds_enclose_direct_high_precision_evaluation():
    from decimal import localcontext
    from portfolio_app.history_irr import _prefix_bounds, _Budget
    terms = ((0, D('-1e30')), (7, D('3e30')), (400, D('-2e30')))
    low, high = D('-.0001'), D('.0002')
    with localcontext() as context:
        context.prec = 30
        bounds = _prefix_bounds(terms, low, high, _Budget())
    with localcontext() as context:
        context.prec = 100
        shift = terms[-1][0]*high
        for z in (low, (low+high)/2, high):
            prefix = D(0)
            for (n, a), (lower, upper) in zip(terms, bounds):
                prefix += a*(n*z-shift).exp()
                assert lower <= prefix <= upper
