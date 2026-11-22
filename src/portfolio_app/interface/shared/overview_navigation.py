"""Overview mode navigation shares one mounted portfolio snapshot."""
from datetime import date
from time import monotonic

import streamlit as st

from portfolio_app.interface.shared.input_cache import file_stamp, fund_sources, monitoring_snapshot
from portfolio_app.interface.shared.view_state import finish_view_inputs, preserve_view_inputs
from portfolio_app.monitoring.monitoring_store import FILE as MONITORING_FILE
from portfolio_app.workspace.display_preferences import FILE as DISPLAY_FILE, load_preferences


def source_stamp(directory):
    """Cheap identity checks for workspace documents and the ETF inputs the app reads."""
    # A mode click saves display preferences. Only other display settings can
    # invalidate the mounted screen. Private caches/backups can be arbitrarily large.
    documents = tuple(file_stamp(path) for path in sorted(directory.glob('*'))
                      if path.suffix in {'.csv', '.json', '.yaml'} and path.name not in (DISPLAY_FILE, MONITORING_FILE))
    prefs = load_preferences(directory)
    return (date.today(), documents, fund_sources(directory), prefs.week_start, prefs.allocation_view,
            file_stamp(directory / '.cache' / 'purchase-fx'))


class ViewSources:
    """Check the source documents behind a mounted portfolio view, including external edits."""

    def __init__(self, directory):
        self.prepared_at = monotonic()
        self.directory = directory
        self.inputs = source_stamp(directory)
        self.monitoring_revision = monitoring_snapshot(directory)[1]

    def notices_recorded(self, previous, published):
        if self.monitoring_revision == previous:
            self.monitoring_revision = published

    def current(self):
        return (monotonic() - self.prepared_at < 60 and source_stamp(self.directory) == self.inputs
                and monitoring_snapshot(self.directory)[1] == self.monitoring_revision)


def render_panel(draw, is_current):
    """Redraw the mounted Overview. The application owns changes to its inputs."""
    mounted = False

    @st.fragment
    def panel():
        nonlocal mounted
        if mounted:
            if not is_current():
                st.rerun()
            preserve_view_inputs()
        draw()
        if mounted:
            finish_view_inputs()
        mounted = True

    panel()
