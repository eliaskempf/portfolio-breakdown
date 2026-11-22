"""Contextual Overview target editing; full policy setup stays in Rebalance."""
from uuid import uuid4

import streamlit as st

from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.target_edit import prepare_target_edit, save_target_edit

STATE = 'overview_target_dialog'


def request_target_edit(directory, config, scope, *, market, exposure_result, displayed_positions):
    try:
        draft = prepare_target_edit(directory,config,scope,market=market,
            exposure_result=exposure_result,displayed_positions=displayed_positions)
        error = None
    except (DataError,OSError) as exc:
        draft, error = None, str(exc)
    st.session_state[STATE] = dict(directory=str(directory),market=market,draft=draft,error=error,token=uuid4().hex)


def render_target_dialog(directory, *, manage_capital=None, manage_exposure=None):
    request = st.session_state.get(STATE)
    if request is None:
        return
    if request['directory'] != str(directory):
        st.session_state.pop(STATE,None)
        return
    draft = request['draft']
    market = request['market']
    def dismiss():
        st.session_state.pop(STATE,None)
    def manage():
        callback = manage_exposure if market and (draft is None or draft.policy is not None) else manage_capital
        dismiss()
        if callback:
            callback()
    @st.dialog('Edit exposure targets' if market else 'Edit allocation targets',width='large',on_dismiss=dismiss)
    def editor():
        if STATE not in st.session_state:
            st.rerun()
        if request['error']:
            st.info(request['error'])
        else:
            st.markdown(f'**{draft.label}**')
            if draft.scope != draft.requested_scope:
                st.caption('Shared group target · edit its share alongside its siblings.')
            elif market and draft.policy is None:
                st.caption('Using capital allocation target percentages.')
            multiple = draft.policy.multiple if draft.policy else None
            if draft.edits_multiple:
                multiple = st.number_input('Total exposure (× net equity)',min_value=0.,value=multiple,step=.05,
                    key='overview_target_multiple_'+request['token'])
            edited = draft.rows
            if len(edited):
                edited = st.data_editor(edited,hide_index=True,width='stretch',num_rows='fixed',
                    key='overview_target_editor_'+request['token'],
                    disabled=['identity','kind','Investment','Account'],
                    column_config={'identity':None,'kind':None,
                        'Account': 'Account' if edited.Account.ne('').any() else None,
                        'Target':st.column_config.NumberColumn('Target (%)',min_value=0.,max_value=100.,
                            help=f'Percentage of {draft.label}. These targets must total 100%.')})
                missing = int(edited.Target.isna().sum())
                total = float(edited.Target.sum())
                st.caption(f'{total:g}% assigned' + (f' · {missing} missing' if missing else ' · needs 100%' if abs(total-100)>1e-7 else ''))
            else:
                st.caption('Target shares follow funded holdings.')
        with st.container(horizontal=True):
            if draft is not None and st.button('Save',type='primary',key='overview_target_save',disabled=not len(draft.rows) and not draft.edits_multiple):
                try:
                    save_target_edit(draft,edited,multiple=multiple)
                except (DataError,OSError,ValueError) as exc:
                    st.error(str(exc))
                else:
                    st.session_state['portfolio_save_notice'] = 'Targets saved.'
                    dismiss()
                    st.rerun()
            if st.button('Cancel',key='overview_target_cancel'):
                dismiss()
                st.rerun()
            dirty = draft is not None and (not edited.equals(draft.rows) or
                (draft.edits_multiple and multiple != draft.policy.multiple))
            st.button('Discard edits & manage targets' if dirty else 'Manage targets',type='tertiary',key='overview_target_manage',on_click=manage)
    editor()
