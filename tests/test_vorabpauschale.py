"""Independently calculated invented fund examples; no personal data or network."""
from decimal import Decimal as D
import pytest

from portfolio_app.vorabpauschale import (FundInputs, TaxLot, calculate_fund, estimate_tax,
                                         RULES, number)
from portfolio_app.holdings import DataError


def run(start='100', end='110', distribution='0', month=1, qty='100', year=2025, exemption='0'):
    return calculate_fund(FundInputs(year, D(start), D(end), D(distribution), D(exemption)),
                          (TaxLot(D(qty), f'{year}-{month:02d}-15'),))


def test_annual_rates_and_receipt_year_are_explicit():
    assert [RULES[y].basis_rate for y in range(2018, 2027)] == list(map(D,
        ['.0087', '.0052', '.0007', '-.0045', '-.0005', '.0255', '.0229', '.0253', '.032']))
    assert RULES[2026].receipt_date == '2027-01-04'
    with pytest.raises(DataError):
        FundInputs(2027)


def test_accumulation_and_partial_exemption():
    result = run(exemption='.30')
    assert result.gross == D('177.10')  # 100 shares * EUR100 * .0253 * .7
    assert result.taxable == D('123.970')
    assert estimate_tax(result.taxable).total == D('32.69')


@pytest.mark.parametrize('end,distribution,expected', [
    ('100.50', '.10', '50.00'), ('101', '0', '100.00'),
    ('110', '1', '77.10'), ('110', '2', '0.00'),
    ('99', '.10', '0.00'), ('100', '0', '0.00'), ('99', '3', '0.00')])
def test_return_cap_and_distributions(end, distribution, expected):
    assert run(end=end, distribution=distribution).gross == D(expected)


@pytest.mark.parametrize('month,expected', [(1, '177.10'), (7, '88.55'), (12, '14.76')])
def test_purchase_month_includes_remaining_year(month, expected):
    assert run(month=month).gross == D(expected)


def test_distribution_subtracted_before_month_reduction():
    assert run(distribution='1', month=7).gross == D('38.55')


def test_round_once_after_fractional_lots():
    lots = tuple(TaxLot(D('.01'), '2025-12-15') for _ in range(100))
    result = calculate_fund(FundInputs(2025, D(100), D(110), D(0)), lots)
    assert result.gross == D('.15')


def test_old_shares_need_no_acquisition_cost():
    result = calculate_fund(FundInputs(2025, D(100), D(110), D(0)), (TaxLot(D(100), '2009-10-01'),))
    assert result.gross == D('177.10')


def test_unknown_is_not_zero_but_nonpositive_basis_rate_is_zero():
    assert calculate_fund(FundInputs(2025), (TaxLot(D(100), None),)).gross is None
    for year in (2021, 2022):
        assert calculate_fund(FundInputs(year), (TaxLot(D(100), None),)).gross == 0
    assert calculate_fund(FundInputs(2025), ()).gross == 0
    assert calculate_fund(FundInputs(2025), (), ownership_issues=('Missing history',)).gross is None


@pytest.mark.parametrize('rate,capital,church,total', [
    ('0', '250.00', '0.00', '263.75'),
    ('.08', '245.10', '19.61', '278.19'),
    ('.09', '244.50', '22.01', '279.95')])
def test_tax_formula_and_solidarity_truncation(rate, capital, church, total):
    result = estimate_tax(D(1100), D(100), D(rate))
    assert result.capital_tax == D(capital)
    assert result.church_tax == D(church)
    assert result.total == D(total)


def test_exhausting_allowance_never_produces_negative_tax():
    result = estimate_tax(D(100), D(200))
    assert result.allowance_used == 100
    assert result.total == 0


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '-1', 'oops'])
def test_invalid_amounts(bad):
    with pytest.raises(DataError):
        number(bad)
