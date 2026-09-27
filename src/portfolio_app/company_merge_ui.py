"""Source-by-source review and persistent undo for analytical company merges."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pandas as pd
import streamlit as st

from portfolio_app.company_merges import MergePlan, MergeSettings, save_settings
from portfolio_app.holdings import DataError


def render_company_merges(plan: MergePlan, settings: MergeSettings, path: Path) -> None:
    with st.expander('Company merges'):
        st.caption('* marks an estimated company match. Review the original holdings below; undo keeps their exposures separate.')
        if not plan.groups:
            st.caption('No overlapping company holdings found.')
            return
        context = sha256(str(path.resolve()).encode()).hexdigest()[:12]
        choices = {group.key: group for group in plan.groups}
        selected = st.selectbox('Review company', list(choices), key=f'merge_review_{context}',
                                format_func=lambda key: choices[key].name + (' *' if choices[key].basis == 'Estimated name match' else '')
                                + (' · Separate' if not choices[key].enabled else ''))
        group = choices[selected]
        st.caption(group.basis + (' · Merged' if group.enabled else ' · Kept separate'))
        st.dataframe(pd.DataFrame([{
            'Source': member['source'], 'Original asset': member['name'], 'ISIN': member['isin'],
            'Source ticker': member['ticker'], 'Snapshot': member['date'],
        } for member in group.members]), hide_index=True, width='stretch')
        label = 'Undo merge' if group.enabled else 'Restore merge'
        error_key = f'merge_error_{context}'
        def change_merge():
            members = {member['node'] for member in group.members}
            disabled = settings.disabled | members if group.enabled else settings.disabled - members
            try:
                save_settings(path, replace(settings, disabled=disabled))
            except (DataError, OSError) as exc:
                st.session_state[error_key] = str(exc)
        st.button(label, key=f'merge_action_{context}_{group.key}_{group.enabled}', on_click=change_merge)
        if error := st.session_state.pop(error_key, None):
            st.error(error)
