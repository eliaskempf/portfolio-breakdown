"""Scoped target editor shared by Overview and Rebalance."""
from uuid import uuid4
import math

import streamlit as st

from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.target_edit import prepare_target_edit, save_target_edit

STATE = 'overview_target_dialog'


def target_split_status(percentages):
    missing = int(percentages.isna().sum())
    total = float(percentages.sum())
    complete = not missing and math.isclose(total,100.,abs_tol=1e-7)
    suffix = f' · {missing} missing' if missing else '' if complete else ' · needs 100%'
    return complete, f'{total:g}% assigned{suffix}'


def request_target_edit(directory, config, scope, *, market=False, exposure_result=None, displayed_positions=None,
                        policy_settings=False, switch_to_members=False, whole_leaf_split=False):
    try:
        draft = prepare_target_edit(directory,config,scope,market=market,
            exposure_result=exposure_result,displayed_positions=displayed_positions,policy_settings=policy_settings,
            switch_to_members=switch_to_members,whole_leaf_split=whole_leaf_split)
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
    can_manage = manage_capital is not None or manage_exposure is not None
    def dismiss():
        st.session_state.pop(STATE,None)
    def manage():
        callback = manage_exposure if market and (draft is None or draft.policy is not None) else manage_capital
        dismiss()
        if callback:
            callback()
            if draft:
                scope_key = 'exposure_control_target_hierarchy' if market and draft.policy else 'targets_allocation_scope'
                st.session_state[scope_key] = draft.requested_scope
    exposure_policy = market and (draft is None or draft.policy is not None)
    @st.dialog('Edit exposure targets' if exposure_policy else 'Edit allocation targets',width='large',on_dismiss=dismiss)
    def editor():
        if STATE not in st.session_state:
            st.rerun()
        complete = True
        if request['error']:
            st.info(request['error'])
        else:
            st.markdown(f'**{draft.label}**')
            if draft.config is None:
                st.caption('Whole-portfolio position targets')
            elif draft.switch_to_members:
                st.caption('Member targets · 100% required')
            elif draft.whole_leaf_split:
                st.caption('Whole-portfolio leaf shares')
            elif draft.scope != draft.requested_scope:
                st.caption('Shared group target', help='Edit its share within the displayed parent.')
            elif market and draft.policy is None:
                st.caption('Capital targets', help='Market exposure uses these percentages until you save a separate exposure policy.')
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
                            help=f'Share of {draft.label}. A complete split totals 100%.')})
                complete, status = target_split_status(edited.Target)
                st.caption(status)
            if draft.edits_multiple and multiple is None:
                st.caption('Total exposure required')
            if edited.empty and draft.policy and draft.policy.policy == 'overlay_only':
                st.caption('Funded weights', help='The policy retains the funded holdings and assigns the overlay to its saved category.')
            elif edited.empty:
                st.caption('No members in this category.')
        def save(*, allow_partial=False):
            try:
                save_target_edit(draft, edited, multiple=multiple, allow_partial=allow_partial)
            except (DataError, OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.session_state['portfolio_save_notice'] = 'Targets saved.'
                dismiss()
                st.rerun()

        with st.container(horizontal=True):
            if draft is None or (draft.rows.empty and not draft.edits_multiple):
                if can_manage:
                    st.button('Manage targets', type='primary', key='overview_target_manage', on_click=manage)
                if st.button('Close', key='overview_target_cancel'):
                    dismiss()
                    st.rerun()
                return
            if st.button('Save', type='primary', key='overview_target_save'):
                save()
            if st.button('Cancel',key='overview_target_cancel'):
                dismiss()
                st.rerun()
            dirty = draft is not None and (not edited.equals(draft.rows) or
                (draft.edits_multiple and multiple != draft.policy.multiple))
            if not complete and draft.policy is None and not draft.switch_to_members:
                if st.button('Save incomplete split', type='tertiary', key='target_partial',
                             help='Preserve blank and entered targets. Planning needs a complete split.'):
                    save(allow_partial=True)
            if can_manage:
                st.button('Discard edits & manage targets' if dirty else 'Manage targets',type='tertiary',key='overview_target_manage',on_click=manage)
    editor()
