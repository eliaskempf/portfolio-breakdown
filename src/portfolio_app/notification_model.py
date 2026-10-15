"""Actionable notification presentation independent of Streamlit."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Destination:
    kind: Literal['rule', 'pricing', 'costs', 'exposure', 'settlements', 'account', 'sources']
    identity: str = ''


@dataclass(frozen=True)
class Notice:
    id: str
    title: str
    message: str
    severity: str
    destination: Destination
    action: str
    observed_at: str = ''
