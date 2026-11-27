"""Frozen source fingerprints for the pandas versions shipped with the app."""
import pytest

from portfolio_app.portfolio.legacy_numeric import legacy_fingerprint, modern_fingerprint


@pytest.mark.parametrize('token, legacy, modern', [
    ('7.4883761085352051', '7.4883761085352045', '7.488376108535205'),
    ('+7.4883761085352051', '7.4883761085352045', '7.4883761085352045'),
    ('+03006.174017347934765017', '3006.174017347934', '3006.1740173479348'),
    ('0.00000000000000000001', '0.0', '1E-20'),
    ('1.2345678901234567e35', '1.2345678901234568E+35', '1.2345678901234566E+35'),
    ('5e-324', '5E-324', '5E-324'),
    ('1.23e-500', '0.0', '0.0'),
    ('-0.0', '-0.0', '-0.0'),
    ('1.7976931348623157e308', '1.7976931348623157E+308', '1.7976931348623157E+308'),
    ('+1e' + '0' * 5000 + '2', '100.0', '100.0'),
])
def test_known_float_column_fingerprints(token, legacy, modern):
    modes = dict(integer_shares=True, integer_price=False)
    assert legacy_fingerprint('2', token, 'EUR', **modes) == ('2.0', legacy, 'EUR')
    assert modern_fingerprint('2', token, 'EUR', **modes) == ('2.0', modern, 'EUR')


def test_whole_fingerprint_uses_one_binary_arithmetic_mode():
    tokens = ('2388.0313341626393', '541.8146449332170322540953424744528176', 'EUR')
    modes = dict(integer_shares=False, integer_price=False)
    assert legacy_fingerprint(*tokens, **modes) == ('2388.0313341626397', '541.814644933217', 'EUR')
    assert legacy_fingerprint(*tokens, fused_digits=True, **modes) == (
        '2388.0313341626393', '541.8146449332171', 'EUR')
    assert modern_fingerprint(*tokens, **modes) == ('2388.0313341626393', '541.814644933217', 'EUR')


def test_integer_dtypes_precede_the_holdings_float_cast():
    # The same text beside a fractional or empty cell is inferred as float.
    token = '90071992547409931'
    assert legacy_fingerprint(token, token, 'USD', integer_shares=True, integer_price=False) == (
        '9.007199254740994E+16', '9.007199254740992E+16', 'USD')
    assert legacy_fingerprint(token, '', '', integer_shares=False, integer_price=False) == (
        '9.007199254740992E+16', '', '')
    for parser in (legacy_fingerprint, modern_fingerprint):
        assert parser('18446744073709551615', '', '', integer_shares=True, integer_price=False) == (
            '1.8446744073709552E+19', '', '')
        assert parser('18446744073709551616', '', '', integer_shares=True, integer_price=False) is None


@pytest.mark.parametrize('token', ['', 'NaN', 'Infinity', '-2', '1_000', '1,2', '1e', '.', '1.7976931348623159e308'])
def test_unrepresentable_quantity_gives_no_alternate_fingerprint(token):
    for parser in (legacy_fingerprint, modern_fingerprint):
        assert parser(token, '', '', integer_shares=False, integer_price=False) is None
