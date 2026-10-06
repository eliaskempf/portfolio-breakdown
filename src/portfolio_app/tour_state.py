"""Suspend view inputs for a tour without restoring read-only widget triggers."""
from copy import deepcopy

import streamlit as st

from portfolio_app.view_state import reset_workspace


PREFIXES = ('strategic_', 'exposure_', 'filter_', 'label_compare_', 'allocation_group_',
            'rebalance_', 'planning_', 'depth_', 'geography_', 'analytics_', 'etf_lookthrough_',
            'etf_excluded_', 'onboarding_', 'position_edit_fields_', 'position_edit_history_',
            'balance_buy_in_mode_', 'bulk_bucket_positions_')
NAMES = {'active_portfolio', 'main_tabs', 'positions_workflow', 'display_tickers',
         'hide_empty_positions', 'ignore_empty_positions', 'portfolio_workspace_context',
         'targets_drafts', 'view_editor_drafts', 'view_editor_bases', 'portfolio_contribution_result'}
TRIGGERS = {'planning_calculate', 'rebalance_calculate'}


def capture_view() -> dict:
    result = {}
    for key in st.session_state:
        value = st.session_state[key]
        if key.startswith('strategic_crumb_') or key in TRIGGERS or key.endswith(('_refresh', '_risk_calculate')):
            continue
        if key in NAMES or (key.startswith('targets_') and key.endswith('_filter')):
            result[key] = deepcopy(value)
        elif key.startswith(PREFIXES) and (value is None or isinstance(value, (str, bool, int, float, list, tuple))):
            result[key] = deepcopy(value)
    return result


def clear_view() -> None:
    reset_workspace()
    for key in list(st.session_state):
        if key.startswith(PREFIXES) or key in NAMES:
            del st.session_state[key]
