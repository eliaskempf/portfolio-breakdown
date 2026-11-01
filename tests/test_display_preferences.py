"""Synthetic workspace calendar preferences preserve independent reporting settings."""
import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.display_preferences import FILE, load_preferences, save_preferences, save_overview_view
from portfolio_app.holdings import DataError
from test_ui import by_label


def test_default_persistence_revision_and_workspace_isolation(tmp_path):
    first,second=tmp_path/'first',tmp_path/'second'
    assert load_preferences(first).week_start=='Monday'
    assert not first.exists()
    save_preferences(first,'Sunday',None)
    saved=load_preferences(first)
    assert saved.week_start=='Sunday'
    assert load_preferences(second).week_start=='Monday'
    with pytest.raises(DataError,match='changed'):
        save_preferences(first,'Monday',None)
    save_preferences(first,'Monday',saved.revision)
    assert load_preferences(first).week_start=='Monday'
    assert not (first/'portfolio.yaml').exists()
    with pytest.raises(DataError,match='Choose'):
        save_preferences(second,'Friday',None)


def test_settings_control_persists_and_reload_uses_saved_choice(tmp_path):
    script=f'''
from pathlib import Path
import streamlit as st
from portfolio_app.calendar_ui import configure_calendar
configure_calendar(Path({str(tmp_path)!r}),st.container())
'''
    app=AppTest.from_string(script).run()
    assert by_label(app.selectbox,'Week starts on').value=='Monday'
    by_label(app.selectbox,'Week starts on').select('Sunday').run()
    assert not app.exception
    assert load_preferences(tmp_path).week_start=='Sunday'
    reopened=AppTest.from_string(script).run()
    assert by_label(reopened.selectbox,'Week starts on').value=='Sunday'


def test_invalid_preferences_are_not_overwritten(tmp_path):
    (tmp_path/FILE).write_text('broken')
    with pytest.raises(DataError,match='display preferences'):
        load_preferences(tmp_path)
    assert (tmp_path/FILE).read_text()=='broken'


def test_overview_choice_preserves_calendar_and_calendar_preserves_overview(tmp_path):
    save_preferences(tmp_path, 'Sunday', None)
    save_overview_view(tmp_path, 'Allocation')
    saved = load_preferences(tmp_path)
    assert saved.week_start == 'Sunday' and saved.overview_view == 'Allocation'
    save_preferences(tmp_path, 'Monday', saved.revision)
    assert load_preferences(tmp_path).overview_view == 'Allocation'
    before = (tmp_path/FILE).read_bytes()
    save_overview_view(tmp_path, 'Allocation')
    assert (tmp_path/FILE).read_bytes() == before
    with pytest.raises(DataError, match='Overview'):
        save_overview_view(tmp_path, 'Unsupported')
    assert (tmp_path/FILE).read_bytes() == before
