"""Small application/workspace controls, separate from portfolio calculations."""
from pathlib import Path
from base64 import b64encode
import threading

import streamlit as st

from portfolio_app.settings import app_version, icon_path
from portfolio_app.interface.shared.view_state import reset_workspace


def app_header(data_dir: Path, demo_dir: Path | None, *, demo: bool):
    """Workspace selection and global actions; no persistent portfolio writes."""
    from portfolio_app.interface.views.onboarding.tour import active, demo_directory
    default_index = int(demo)
    touring = active()
    if touring:
        data_dir, demo = demo_directory(), True
    with st.container(key='app_header'):
        with st.container(horizontal=True, vertical_alignment='center', gap='small'):
            with st.container(width='stretch'):
                icon = icon_path('portfolio-breakdown.svg')
                image = f'<img alt="" src="data:image/svg+xml;base64,{b64encode(icon.read_bytes()).decode()}" />' if icon else ''
                st.html(f'<div class="app-brand">{image}<span>Breakdown</span></div>')
            if demo_dir is not None:
                choices = ['My portfolio', 'Demo portfolio']
                # The browser's initial value follows the launcher even before a rerun.
                # Keep it independent of later workspace switches and the guided tour.
                if 'active_portfolio' in st.session_state and st.session_state.active_portfolio is None:
                    st.session_state['active_portfolio'] = choices[default_index]
                workspace = st.selectbox('Portfolio workspace', choices, help='Switch between your local portfolio and the separate demonstration workspace.',
                    index=default_index, key='active_portfolio', label_visibility='collapsed', width=200, disabled=touring)
                demo = workspace == 'Demo portfolio'
                if demo and not touring:
                    data_dir = demo_dir
                context = (str(data_dir.resolve()), demo)
                if not touring and st.session_state.get('portfolio_workspace_context') != context:
                    from portfolio_app.interface.views.onboarding.tour import workspace_changed
                    workspace_changed(demo=demo)
                    reset_workspace()
                    st.session_state['portfolio_workspace_context'] = context
            offline = demo and not (data_dir / '.live-demo').exists()
            refresh = not offline and st.button('Refresh prices', help='Request updated quotes and exchange rates.',
                icon=':material/refresh:', type='tertiary')
            notifications = st.container(key='header_notifications', width=44)
            preferences = workspace_panel = None
            if touring:
                st.button('App menu', disabled=True, help='Finish or leave the tour to use the app menu.')
            else:
                menu = st.popover('App menu', key='app_menu', on_change='rerun')
                if not menu.open:
                    st.session_state.pop('confirm_stop', None)
                with menu:
                    preferences = _menu_section('Preferences')
                    workspace_panel = _menu_section('Workspace')
                    help_section = _menu_section('Help')
                    with help_section:
                        _help(demo)
    if not touring:
        from portfolio_app.interface.shared.update_ui import watch_updates, render_update_settings
        if not offline:
            watch_updates()
        if menu.open and help_section.open:
            with help_section:
                render_update_settings(offline=offline)
    return data_dir, demo, refresh, preferences, workspace_panel, notifications


def _section_changed(selected):
    if st.session_state[selected]:
        for key in ('menu_preferences', 'menu_workspace', 'menu_help'):
            if key != selected:
                st.session_state[key] = False
    if not st.session_state.get('menu_workspace'):
        st.session_state.pop('confirm_stop', None)


def _menu_section(label):
    key = 'menu_' + label.lower()
    return st.expander(label, key=key, on_change=_section_changed, args=(key,))


def open_workspace_menu():
    st.session_state.update(app_menu=True, menu_workspace=True, menu_preferences=False, menu_help=False)


def workspace_error(error, workspace_panel):
    st.error(str(error))
    if workspace_panel is not None:
        st.button('Open workspace controls', on_click=open_workspace_menu,
                  help='Find the data folder and available backup and restore actions.')


def _help(demo):
    from portfolio_app.runtime.documentation import guide_url
    from portfolio_app.interface.views.onboarding.tour import request_tour
    if guide_url() is None:
        st.warning('The guide bundled with this app is unavailable. Reinstall the complete app to restore version-matched help.')
    else:
        for title, topic in [('User guide', ''), ('Getting started', 'getting-started/'),
                             ('Categories and targets', 'allocation/'), ('ETF breakdowns', 'exposure/'),
                             ('Monitoring and alerts', 'monitoring/')]:
            st.link_button(title, guide_url(topic))
    st.button('Take the tour', on_click=request_tour, args=('demo' if demo else 'manual',),
              kwargs={'replay': True})


def workspace_info(active: Path, persistent: Path, *, demo: bool, managed: bool, recovery: bool) -> None:
    from portfolio_app.interface.views.workspace.backup_ui import backup_controls
    st.caption('Temporary demo folder' if demo else 'Portfolio folder')
    st.code(str(active.resolve()), language=None)
    if st.button('Open data folder', help='Open this workspace in the file manager.'):
        from portfolio_app.runtime.desktop import open_folder
        try:
            open_folder(active)
        except OSError as exc:
            st.error(f'Could not open the folder: {exc}')
    backup_controls(demo=demo, recovery=recovery, managed=managed)
    st.divider()
    st.caption(f'Portfolio Breakdown {app_version()} · GPL-3.0-only')
    if not managed:
        return
    if st.session_state.get('confirm_stop'):
        st.warning('Stop the app for all open windows? Unsaved forms will close.')
        with st.container(horizontal=True):
            st.button('Keep running', on_click=st.session_state.pop, args=('confirm_stop', None))
            if st.button('Confirm stop'):
                from portfolio_app.runtime.launcher import stop_instance
                st.info('Stopping the local application. You can close this tab.')
                timer = threading.Timer(.5, stop_instance, args=(persistent,))
                timer.daemon = True
                timer.start()
    else:
        st.button('Stop application', on_click=st.session_state.update, kwargs={'confirm_stop': True})
