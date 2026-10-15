"""Workspace week-start preference for Streamlit's native calendars.

Streamlit 1.65 reads navigator.language into its calendar locale, without a
Python locale argument. Supply only the Unicode fw extension in this page;
leave the original language, region, number formats and browser settings intact.
The native calendar computes its grid and keyboard navigation from this locale.
Remove this bridge when date_input exposes a week-start option.
"""
import streamlit as st

from portfolio_app.display_preferences import WEEK_STARTS, load_preferences, save_preferences
from portfolio_app.holdings import DataError

SCRIPT = """
export default function({data, setStateValue}) {
  const state = window.__portfolioCalendarLocale ||= {original: navigator.language};
  // Some desktop/Chromium environments expose POSIX locale suffixes.
  let original;
  try { original = new Intl.Locale(state.original.replaceAll('_', '-').split('@')[0]).toString(); }
  catch { original = 'en-US'; }
  const [base, privateUse] = original.split('-x-');
  const clean = base.replace(/-fw-[a-z0-9]{3,8}(?:-[a-z0-9]{3,8})*/gi, '').replace(/-u$/, '');
  const extension = '-fw-' + data.weekStart;
  const locale = (clean.includes('-u-') ? clean.replace('-u-', '-u' + extension + '-') : clean + '-u' + extension)
    + (privateUse ? '-x-' + privateUse : '');
  if (state.applied !== locale) {
    Object.defineProperty(navigator, 'language', {configurable: true, get: () => locale});
    state.applied = locale;
    // Refresh Streamlit's locale context even when all widgets arrived in one
    // batch before this bridge mounted. One rerun per changed preference.
    setStateValue('locale', locale);
  }
}
"""


def configure_calendar(directory, settings_panel):
    try:
        preferences = load_preferences(directory)
    except DataError as exc:
        if settings_panel is not None:
            with settings_panel: st.error(str(exc))
        return
    if settings_panel is not None:
        with settings_panel:
            key = 'calendar_week_start_'+str(directory.resolve())+'_'+str(preferences.revision)
            def save():
                try:
                    save_preferences(directory, st.session_state[key], preferences.revision)
                except DataError as exc:
                    st.session_state['calendar_preference_error'] = str(exc)
            st.selectbox('Week starts on', WEEK_STARTS, index=WEEK_STARTS.index(preferences.week_start),
                key=key, on_change=save, help='First weekday in all date pickers. Saved for this portfolio.')
            if error := st.session_state.pop('calendar_preference_error', None):
                st.error(error)
    bridge = st.components.v2.component('portfolio_calendar_locale', js=SCRIPT)
    bridge(key='portfolio_calendar_locale', data={'weekStart': 'mon' if preferences.week_start=='Monday' else 'sun'},
           default={'locale': None}, on_locale_change=lambda: None)
