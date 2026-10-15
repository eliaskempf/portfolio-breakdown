"""Revision-checked workspace display preferences, separate from currency changes."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from portfolio_app.holdings import DataError
from portfolio_app.storage import save_document

FILE = 'display-preferences.json'
WEEK_STARTS = ('Monday', 'Sunday')


@dataclass(frozen=True)
class DisplayPreferences:
    week_start: str = 'Monday'
    revision: str | None = None


def load_preferences(directory):
    path = Path(directory)/FILE
    try:
        content = path.read_bytes() if path.exists() else None
        if content is None:
            return DisplayPreferences()
        raw = json.loads(content)
        if raw.get('version') != 1 or raw.get('week_start') not in WEEK_STARTS:
            raise ValueError
        return DisplayPreferences(raw['week_start'], sha256(content).hexdigest())
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise DataError('Cannot read display preferences. Restore display-preferences.json from a backup.') from exc


def save_preferences(directory, week_start, expected_revision):
    if week_start not in WEEK_STARTS:
        raise DataError('Choose Monday or Sunday.')
    save_document(Path(directory)/FILE, json.dumps({'version':1, 'week_start':week_start})+'\n', expected_revision)
