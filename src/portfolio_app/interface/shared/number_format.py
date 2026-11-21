"""Directional changes retain numeric source values for calculations and sorting."""
from math import isfinite


def change_number(value, *, decimals=2, suffix='') -> str:
    if value is None or not isfinite(value):
        return '—'
    direction = '▲ ' if value > 0 else '▼ ' if value < 0 else ''
    return f'{direction}{abs(value):,.{decimals}f}{suffix}'


def return_percent(value) -> str:
    return change_number(value, suffix='%')


def change_columns(frame, *, percent=(), points=()):
    """Style changes without converting numeric columns to sortable strings."""
    formats = {key: return_percent for key in percent if key in frame}
    formats.update({key: change_number for key in points if key in frame})
    return frame.style.format(formats) if formats else frame
