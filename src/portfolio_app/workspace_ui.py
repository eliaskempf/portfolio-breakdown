"""Small application/workspace controls, separate from portfolio calculations."""
from pathlib import Path
import threading

import streamlit as st

from portfolio_app.settings import app_version


def workspace_info(active: Path, persistent: Path, *, demo: bool) -> None:
    with st.sidebar.expander('App & workspace'):
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
