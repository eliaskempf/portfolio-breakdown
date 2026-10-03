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
            settings = st.popover('Settings', icon=':material/tune:')
            with st.popover('?', help='Help and demo guide'):
                if demo:
                    from portfolio_app.onboarding_ui import demo_guide
                    demo_guide(offline=offline)
                else:
                    st.markdown('**Your portfolio**')
                    st.write('Add or import holdings in Positions, then set category targets in Rebalance → Targets.')
                st.caption('Demo changes reset on restart. Your own portfolio stays saved locally.')
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
        st.caption('Back up the complete folder with the app stopped. Closing the browser tab keeps the app running.')
        if st.button('Stop application'):
            from portfolio_app.launcher import stop_instance
            st.info('Stopping the local application. You can close this tab.')
            timer = threading.Timer(.5, stop_instance, args=(persistent,))
            timer.daemon = True
            timer.start()
