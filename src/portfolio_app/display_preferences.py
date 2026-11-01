"""Revision-checked workspace display preferences, separate from currency changes."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from portfolio_app.holdings import DataError
from portfolio_app.storage import save_document

FILE = 'display-preferences.json'
WEEK_STARTS = ('Monday', 'Sunday')
OVERVIEW_VIEWS = ('Allocation', 'Performance', 'Analytics')


@dataclass(frozen=True)
class DisplayPreferences:
    week_start: str = 'Monday'
    revision: str | None = None
    overview_view: str = ''


def load_preferences(directory):
    path = Path(directory)/FILE
    try:
        content = path.read_bytes() if path.exists() else None
        if content is None:
            return DisplayPreferences()
        raw = json.loads(content)
        if (raw.get('version') != 1 or raw.get('week_start') not in WEEK_STARTS
                or raw.get('overview_view', '') not in ('', *OVERVIEW_VIEWS)):
            raise ValueError
        return DisplayPreferences(raw['week_start'], sha256(content).hexdigest(), raw.get('overview_view', ''))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise DataError('Cannot read display preferences. Restore display-preferences.json from a backup.') from exc


def save_preferences(directory, week_start, expected_revision, *, overview_view=None):
    if week_start not in WEEK_STARTS:
        raise DataError('Choose Monday or Sunday.')
    if overview_view is None:
        overview_view = load_preferences(directory).overview_view
    if overview_view not in ('', *OVERVIEW_VIEWS):
        raise DataError('Choose an available Overview view.')
    save_document(Path(directory)/FILE, json.dumps({'version':1, 'week_start':week_start,
        'overview_view':overview_view})+'\n', expected_revision)


def save_overview_view(directory, view):
    preferences = load_preferences(directory)
    if preferences.overview_view != view:
        save_preferences(directory, preferences.week_start, preferences.revision, overview_view=view)
