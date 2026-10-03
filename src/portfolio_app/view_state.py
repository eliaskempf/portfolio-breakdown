"""Retain user inputs when lazy views unmount their widgets."""
import streamlit as st


def preserve_view_inputs():
    # Only input widgets, never buttons, component triggers, or editor patches.
    prefixes = ('filter_', 'label_compare_', 'allocation_group_', 'rebalance_',
                'planning_', 'bulk_bucket_positions_', 'balance_buy_in_mode_',
                'depth_', 'position_edit_fields_', 'position_edit_history_', 'exposure_control_')
    names = {'strategic_category', 'strategic_view', 'strategic_performance_measure',
             'exposure_scope', 'exposure_search', 'exposure_view', 'exposure_group',
             'exposure_show_chart', 'display_tickers', 'positions_workflow',
             'ignore_empty_positions', 'exposure_sources_open', 'exposure_stock_open', 'exposure_merges_open',
             'exposure_show_price_details', 'exposure_stock_enabled', 'exposure_stock_excluded',
             'rebalance_tabs'}
    active = st.session_state.get('main_tabs', 'Overview')
    for key in list(st.session_state):
        value = st.session_state[key]
        if key.startswith('targets_') and key.endswith('_filter'):
            if active != 'Rebalance' or st.session_state.get('rebalance_tabs') != 'Targets':
                st.session_state[key] = value
            continue
        # Analytics presets and options have their own mount boundaries inside
        # the lazy tabs. Never preserve button/refresh trigger state.
        if key.startswith('analytics_') and (key.endswith(('_view', '_benchmark', '_years', '_accounts')) or '_columns_' in key):
            if '_positions_' in key:
                prefix, control = key.split('_positions_', 1)
                view = st.session_state.get(prefix + '_positions_view', 'Holdings')
                mounted = active == 'Positions' and st.session_state.get('positions_workflow', 'Positions') == 'Positions'
                if control.startswith('columns_'):
                    mounted = mounted and view == control.removeprefix('columns_')
                elif control in {'benchmark', 'years'}:
                    mounted = mounted and view == 'Risk'
            else:
                mounted = active == 'Overview' and st.session_state.get('strategic_view') == 'Analytics'
            if not mounted:
                st.session_state[key] = value
            continue
        if (key in names or key.startswith(prefixes) or key.startswith('exposure_root_')) and key not in {'rebalance_calculate', 'planning_calculate'}:
            if key.startswith('strategic_'):
                mounted = active == 'Overview'
            elif key.startswith(('rebalance_', 'planning_')) or key == 'ignore_empty_positions':
                mounted = active == 'Rebalance' and (key == 'rebalance_tabs' or st.session_state.get('rebalance_tabs', 'Plan') == 'Plan')
            elif key.startswith(('position_edit_fields_', 'position_edit_history_')):
                mounted = st.session_state.get('position_edit_dialog', False)
            elif key == 'positions_workflow' or key.startswith('balance_'):
                mounted = active == 'Positions'
            elif key.startswith('bulk_bucket_positions_'):
                mounted = active == 'Rebalance' and st.session_state.get('rebalance_tabs') == 'Targets'
            elif key in {'exposure_stock_enabled', 'exposure_stock_excluded'}:
                mounted = active == 'Exposure' and st.session_state.get('exposure_stock_open', False)
            elif key == 'exposure_show_price_details':
                mounted = active == 'Exposure' and st.session_state.get('exposure_sources_open', False)
            else:
                mounted = active == 'Exposure'
            if mounted:
                continue
            if isinstance(value, (str, bool, int, float, list, tuple)) or value is None:
                st.session_state[key] = value


def persistent_editor(data, *, key, **kwargs):
    """Keep edited rows across unmounts without assigning read-only widget state.

    Callers include source revisions in keys, so a successful save or external
    edit starts a fresh draft. The editor's baseline stays fixed while mounted.
    """
    drafts = st.session_state.setdefault('view_editor_drafts', {})
    bases = st.session_state.setdefault('view_editor_bases', {})
    if key not in st.session_state or key not in bases:
        bases[key] = drafts.get(key, data).copy(deep=True)
    edited = st.data_editor(bases[key], key=key, **kwargs)
    drafts[key] = edited.copy(deep=True)
    # Bound obsolete revisions retained during a long editing session.
    while len(drafts) > 32:
        oldest = next(iter(drafts))
        drafts.pop(oldest)
        bases.pop(oldest, None)
    return edited


def reset_workspace():
    """Discard workspace-specific forms and filters before switching portfolios."""
    for key in list(st.session_state):
        if key.startswith(("onboarding_", "import_", "etf_setup_", "position_edit_", "filter_", "label_compare_", "allocation_group_", "rebalance_", "strategic_", "exposure_", "position_draft", "positions_", "planning_", "bulk_bucket_", "targets_")) or key in {"position_saved_notice", "ignore_empty_positions", "hide_empty_positions", "view_editor_drafts", "view_editor_bases", "main_tabs", "rebalance_tabs", "portfolio_contribution_result"}:
            del st.session_state[key]
