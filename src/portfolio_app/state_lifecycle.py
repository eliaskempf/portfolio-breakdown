"""Feature-owned session lifetimes, independent of Streamlit.

Policies describe ordinary inputs separately from owned drafts/results/triggers.
Only unmounted ordinary inputs may be reassigned. Uploads and editor patches keep
specialized adapters; source fingerprints remain owned by each calculator.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Callable


@dataclass(frozen=True)
class Keys:
    names: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    predicate: Callable[[str], bool] | None = None

    def matches(self, key):
        return key in self.names or key.startswith(self.prefixes) or bool(self.predicate and self.predicate(key))


def scalar(value):
    return value is None or isinstance(value, (str, bool, int, float, list, tuple))


def trigger(key):
    return key in {'planning_calculate', 'rebalance_calculate'} or key.startswith('strategic_crumb_') or key.endswith(('_refresh', '_risk_calculate'))


def tab(name, subkey=None, subview=None):
    return lambda key, state: state.get('main_tabs', 'Overview') == name and (
        subkey is None or state.get(subkey, 'Plan' if subkey == 'rebalance_tabs' else 'Positions') == subview)


@dataclass(frozen=True)
class FeatureState:
    owner: str
    owned: Keys
    inputs: Keys = field(default_factory=Keys)
    mounted: Callable = lambda key, state: False
    workspace: bool = True
    currency: Keys = field(default_factory=Keys)
    tour: Keys = field(default_factory=Keys)
    tour_objects: Keys = field(default_factory=Keys)


def analytics_mounted(key, state):
    if '_positions_' not in key:
        return tab('Overview')(key, state) and state.get('strategic_view') == 'Analytics'
    prefix, control = key.split('_positions_', 1)
    view = state.get(prefix + '_positions_view', 'Holdings')
    mounted = tab('Positions', 'positions_workflow', 'Positions')(key, state)
    if control.startswith('columns_'):
        return mounted and view == control.removeprefix('columns_')
    return mounted and (control not in {'benchmark', 'years'} or view == 'Risk')


def exposure_mounted(key, state):
    if not tab('Exposure')(key, state):
        return False
    if key in {'exposure_stock_enabled', 'exposure_stock_excluded'}:
        return state.get('exposure_stock_open', False)
    if key == 'exposure_show_price_details':
        return state.get('exposure_sources_open', False)
    return True


# Each declaration owns all its transitions. New widget keys belong beside their
# feature here, rather than in separate navigation/workspace/currency/tour lists.
FEATURES = (
    FeatureState('overview', Keys(prefixes=('strategic_',)),
        inputs=Keys(names=('strategic_category', 'strategic_view', 'strategic_performance_measure', 'strategic_allocation_measure')),
        mounted=tab('Overview'), tour=Keys(prefixes=('strategic_',))),
    FeatureState('exposure', Keys(prefixes=('exposure_', 'filter_', 'label_compare_', 'allocation_group_')),
        inputs=Keys(names=('exposure_measure', 'exposure_market_basis', 'exposure_market_scope', 'exposure_market_view',
            'exposure_market_taxonomy', 'exposure_market_depth', 'exposure_scope', 'exposure_search', 'exposure_view',
            'exposure_group', 'exposure_show_chart', 'exposure_sources_open', 'exposure_stock_open', 'exposure_merges_open',
            'exposure_show_price_details', 'exposure_stock_enabled', 'exposure_stock_excluded'),
            prefixes=('filter_', 'label_compare_', 'allocation_group_', 'exposure_control_', 'exposure_root_')),
        mounted=exposure_mounted, tour=Keys(prefixes=('exposure_', 'filter_', 'label_compare_', 'allocation_group_'))),
    FeatureState('planning', Keys(prefixes=('rebalance_', 'planning_'), names=('ignore_empty_positions', 'portfolio_contribution_result')),
        inputs=Keys(prefixes=('rebalance_', 'planning_'), names=('ignore_empty_positions',)),
        mounted=lambda key, state: tab('Rebalance')(key, state) and (key == 'rebalance_tabs' or state.get('rebalance_tabs', 'Plan') == 'Plan'),
        currency=Keys(names=('planning_amount', 'planning_minimum', 'rebalance_cash', 'rebalance_minimum_purchase',
                            'rebalance_result', 'portfolio_contribution_result')),
        tour=Keys(prefixes=('rebalance_', 'planning_'), names=('ignore_empty_positions',)),
        tour_objects=Keys(names=('portfolio_contribution_result',))),
    FeatureState('targets', Keys(prefixes=('targets_', 'bulk_bucket_')),
        inputs=Keys(prefixes=('bulk_bucket_positions_',), predicate=lambda key: key.startswith('targets_') and key.endswith('_filter')),
        mounted=tab('Rebalance', 'rebalance_tabs', 'Targets'),
        tour=Keys(prefixes=('bulk_bucket_positions_',), predicate=lambda key: key.startswith('targets_') and key.endswith('_filter')),
        tour_objects=Keys(names=('targets_drafts',))),
    FeatureState('position editor', Keys(prefixes=('position_edit_', 'position_draft'), names=('position_saved_notice',)),
        inputs=Keys(prefixes=('position_edit_fields_', 'position_edit_history_')),
        mounted=lambda key, state: state.get('position_edit_dialog', False),
        currency=Keys(prefixes=('position_edit_',), names=('position_draft',)),
        tour=Keys(prefixes=('position_edit_fields_', 'position_edit_history_'))),
    FeatureState('positions', Keys(prefixes=('positions_',)), inputs=Keys(names=('positions_workflow',)),
        mounted=tab('Positions'), tour=Keys(names=('positions_workflow',))),
    FeatureState('balances', Keys(prefixes=('balance_buy_in_mode_',)),
        inputs=Keys(prefixes=('balance_buy_in_mode_',)), mounted=tab('Positions'), workspace=False,
        tour=Keys(prefixes=('balance_buy_in_mode_',))),
    FeatureState('analytics', Keys(prefixes=('analytics_',)), workspace=False,
        inputs=Keys(predicate=lambda key: key.endswith(('_view', '_benchmark', '_years', '_accounts')) or '_columns_' in key),
        mounted=analytics_mounted, tour=Keys(prefixes=('analytics_',))),
    FeatureState('futures', Keys(prefixes=('futures_', 'future_', 'leverage_', 'funding_')),
        inputs=Keys(prefixes=('futures_input_',))),
    FeatureState('currency', Keys(prefixes=('currency_',)), currency=Keys(prefixes=('currency_estimate_',))),
    FeatureState('onboarding', Keys(prefixes=('onboarding_',)), tour=Keys(prefixes=('onboarding_',))),
    FeatureState('imports', Keys(prefixes=('import_',))),
    FeatureState('fund setup', Keys(prefixes=('etf_setup_',))),
    FeatureState('editors', Keys(names=('view_editor_drafts', 'view_editor_bases')),
        tour_objects=Keys(names=('view_editor_drafts', 'view_editor_bases'))),
    FeatureState('workspace', Keys(names=('main_tabs', 'parsed_inputs', 'hide_empty_positions')),
        tour_objects=Keys(names=('main_tabs', 'hide_empty_positions'))),
    FeatureState('view preferences', Keys(prefixes=('depth_', 'geography_', 'etf_lookthrough_', 'etf_excluded_'),
        names=('display_tickers', 'active_portfolio', 'portfolio_workspace_context')), workspace=False,
        inputs=Keys(prefixes=('depth_',), names=('display_tickers',)), mounted=tab('Exposure'),
        tour=Keys(prefixes=('depth_', 'geography_', 'etf_lookthrough_', 'etf_excluded_')),
        tour_objects=Keys(names=('display_tickers', 'active_portfolio', 'portfolio_workspace_context'))),
)


def policy_for(key):
    return next((feature for feature in FEATURES if feature.owned.matches(key)), None)


def retained_inputs(state):
    """Keys safe to reassign before rendering to survive Streamlit unmounts."""
    for key in list(state):
        policy = policy_for(key)
        safe_input = scalar(state[key]) or isinstance(state[key], date)
        if policy and not trigger(key) and policy.inputs.matches(key) and safe_input and not policy.mounted(key, state):
            yield key


def transition(state, event):
    if event not in {'workspace', 'currency', 'tour'}:
        raise ValueError('Unknown session transition')
    for key in list(state):
        policy = policy_for(key)
        if policy is None:
            continue
        clear = policy.workspace if event == 'workspace' else policy.currency.matches(key) if event == 'currency' else (
            policy.workspace or policy.tour.matches(key) or policy.tour_objects.matches(key))
        if clear:
            del state[key]
    if event == 'currency':
        for bucket in ('view_editor_drafts', 'view_editor_bases'):
            drafts = state.get(bucket, {})
            for key in list(drafts):
                if key.startswith('balance_'):
                    del drafts[key]


def capture_tour(state):
    result = {}
    for key in state:
        policy = policy_for(key)
        if policy and not trigger(key) and (policy.tour_objects.matches(key) or
                policy.tour.matches(key) and scalar(state[key])):
            result[key] = deepcopy(state[key])
    return result
