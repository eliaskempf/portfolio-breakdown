"""Small application/workspace controls, separate from portfolio calculations."""
from pathlib import Path
from base64 import b64encode
import threading

import streamlit as st

from portfolio_app.settings import app_version, icon_path
from portfolio_app.view_state import reset_workspace


def app_header(data_dir: Path, demo_dir: Path | None, *, demo: bool):
    """Workspace selection and global actions; no persistent portfolio writes."""
    with st.container(key='app_header'):
        with st.container(horizontal=True, vertical_alignment='center', gap='small'):
            with st.container(width='stretch'):
                icon = icon_path('portfolio-breakdown.svg')
                image = f'<img alt="" src="data:image/svg+xml;base64,{b64encode(icon.read_bytes()).decode()}" />' if icon else ''
                st.html(f'<div class="app-brand">{image}<span>Breakdown</span></div>')
            if demo_dir is not None:
                workspace = st.selectbox('Portfolio workspace', ['My portfolio', 'Demo portfolio'],
                    index=1 if demo else 0, key='active_portfolio', label_visibility='collapsed', width=200)
                demo = workspace == 'Demo portfolio'
                if demo:
                    data_dir = demo_dir
                context = (str(data_dir.resolve()), demo)
                if st.session_state.get('portfolio_workspace_context') != context:
                    reset_workspace()
                    st.session_state['portfolio_workspace_context'] = context
            offline = demo and not (data_dir / '.live-demo').exists()
            refresh = st.button('Refresh prices', icon=':material/refresh:', type='tertiary', disabled=offline)
            settings = st.popover('Settings', icon=':material/tune:', key='workspace_settings', on_change='rerun')
            with st.popover('?', help='Help and demo guide'):
                from portfolio_app.documentation import guide_url
                st.link_button('User guide', guide_url(), icon=':material/menu_book:')
                st.link_button('Getting started', guide_url('getting-started/'))
                st.link_button('Categories and targets', guide_url('allocation/'))
                st.link_button('ETF breakdowns', guide_url('exposure/'))
                if demo:
                    st.caption('Invented holdings and buy-ins. Demo changes reset on restart.')
                    st.caption('Synthetic offline prices.' if offline else 'Public market quotes and history; availability varies.')
    return data_dir, demo, refresh, settings


def workspace_info(active: Path, persistent: Path, *, demo: bool) -> None:
    with st.expander('App & workspace'):
        st.caption(f'Portfolio Breakdown {app_version()} · GPL-3.0-only')
        st.caption('Temporary demo folder' if demo else 'Portfolio folder')
        st.code(str(active.resolve()), language=None)
        if st.button('Open data folder'):
            from portfolio_app.desktop import open_folder
            try:
                open_folder(active)
            except OSError as exc:
                st.error(f'Could not open the folder: {exc}')
        st.caption('Back up the complete folder with the app stopped. Closing the app window stops it; closing a browser tab does not.')
        if st.button('Stop application'):
            from portfolio_app.launcher import stop_instance
            st.info('Stopping the local application. You can close this tab.')
            timer = threading.Timer(.5, stop_instance, args=(persistent,))
            timer.daemon = True
            timer.start()
