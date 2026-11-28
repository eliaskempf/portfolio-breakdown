"""Reconstruct source fingerprints from the CSV text used by released pandas parsers.

The 3.0.6 algorithm follows precise_xstrtod in pandas/_libs/src/parser/tokenizer.c.
In 3.1.1, tokenizer.cpp uses fast_float for standard decimal input. The original
sources and redistribution notices are identified in the project's LICENSE.
"""
from decimal import Decimal
import math
import platform
import sys
import re


_TOKEN = re.compile(
    r'([+-]?)([0-9]*)(?:\.([0-9]*))?(?:[eE]([ \t\r\n\f\v]*[+-]?[0-9]+))?'
)


def _exponent(text, bound):
    """Clamp beyond the range that any digits in this token could offset."""
    if text is None:
        return 0
    text = text.lstrip()
    digits = text.lstrip('+-').lstrip('0') or '0'
    if len(digits) > len(str(bound)):
        return -bound if text.startswith('-') else bound
    value = int(digits)
    return -value if text.startswith('-') else value


def _fallback_float(sign, whole, fraction, exponent, modern, fused_digits):
    # Extra integer digits change the scale and extra fraction digits are
    # discarded. Only 3.0.6 counts leading zeros towards its 17 digits.
    fraction = fraction or ''
    scale = _exponent(exponent, len(whole) + len(fraction) + 617)
    if modern:
        # The 3.1.1 fallback skips leading zeros before selecting 17 digits.
        whole = whole.lstrip('0')
        if not whole:
            significant = fraction.lstrip('0')
            scale -= len(fraction) - len(significant)
            fraction = significant
    digits = whole[:17] + fraction[:max(0, 17 - len(whole))]
    scale += len(whole) - len(digits)
    if modern:
        value = float(int(digits or '0'))
    else:
        value = 0.0
        for digit in digits:
            if fused_digits:
                # The 3.0.6 macOS arm64 wheel fuses the multiply and add. All
                # accumulator values are integers, so round the exact sum once.
                value = float(int(value) * 10 + int(digit))
            else:
                value = value * 10.0 + int(digit)
    if sign == '-':
        value = -value
    if scale > 308:
        return 0.0 if value == 0 else math.copysign(math.inf, value)
    if scale < -616:
        return 0.0
    if scale < -308:
        return value / float(f'1e{-308 - scale}') / 1e308
    # Decimal literals give the correctly rounded constants from pandas' table.
    power = float(f'1e{abs(scale)}')
    return value * power if scale > 0 else value / power


def spaced_exponent(token: str) -> tuple[str, float] | None:
    """Return the exact decimal spelling and the former reader's value, if valid."""
    match = _TOKEN.fullmatch(token)
    if match:
        sign, whole, fraction, exponent = match.groups()
        if (whole or fraction) and exponent and exponent[0].isspace():
            fused = sys.platform == 'darwin' and platform.machine() == 'arm64'
            value = _fallback_float(sign, whole, fraction, exponent, modern=False, fused_digits=fused)
            if math.isfinite(value) and value >= 0:
                return token[:match.start(4)] + exponent.lstrip(), value
    return None


def _number(token, integer_column, modern, fused_digits):
    match = _TOKEN.fullmatch(token)
    if not match:
        return None
    sign, whole, fraction, exponent = match.groups()
    if not whole and not fraction:
        return None
    integer_token = fraction is None and exponent is None
    if integer_column and not integer_token:
        return None
    if integer_token:
        try:
            integer = int(token)
        except ValueError:
            # Both releases also call int(token) when inferring integer cells.
            return None
        if integer_column:
            if not 0 <= integer < 2**64:
                return None
            return str(Decimal(str(float(integer))))
    if modern and sign != '+':
        # fast_float does not accept a leading '+', so pandas falls back to
        # precise_xstrtod for those cells. Its usual path rounds to binary64.
        try:
            value = float(token)
        except ValueError:
            return None
    else:
        value = _fallback_float(sign, whole, fraction, exponent, modern, fused_digits)
    if not math.isfinite(value) or value < 0:
        return None
    return str(Decimal(str(value)))


def _fingerprint(shares, price, currency, integer_shares, integer_price, modern, fused_digits):
    quantity = _number(shares, integer_shares, modern, fused_digits)
    cost = _number(price, integer_price, modern, fused_digits) if price else ''
    if quantity is None or cost is None:
        return None
    return quantity, cost, currency


def legacy_fingerprint(shares: str, acquisition_price: str, currency: str, *,
                       integer_shares: bool, integer_price: bool,
                       fused_digits: bool = False) -> tuple[str, str, str] | None:
    """Full 3.0.6 fingerprint for stripped CSV cells and their column dtypes."""
    return _fingerprint(shares, acquisition_price, currency, integer_shares, integer_price, False, fused_digits)


def modern_fingerprint(shares: str, acquisition_price: str, currency: str, *,
                       integer_shares: bool, integer_price: bool,
                       fused_digits: bool = False) -> tuple[str, str, str] | None:
    """Full 3.1.1 fingerprint for the same cells, including on an older runtime."""
    return _fingerprint(shares, acquisition_price, currency, integer_shares, integer_price, True, fused_digits)
