"""Session transition contracts, with synthetic state and no mounted UI."""
import pytest
from datetime import date

from portfolio_app.state_lifecycle import FEATURES, capture_tour, retained_inputs, transition


def state():
    return dict(main_tabs='Overview', strategic_category='invented', exposure_market_basis='Equity only',
        planning_amount=100., planning_minimum=25., planning_calculate=False,
        rebalance_result=('invented-signature', 'invented-plan'),
        position_edit_fields_name='Invented draft', position_edit_dialog=False,
        import_files=[('invented.csv', b'id,name,shares\n')], import_uploads='widget-only',
        import_controls={'invented': 'review choice'}, futures_input_contracts=2,
        futures_funding_result_context=('invented-result', 'original assumptions'),
        analytics_context_overview_years=3, analytics_context_overview_risk_calculate=False,
        view_editor_drafts={'balance_old': 'old monetary rows', 'targets_old': 'percentage rows'},
        view_editor_bases={'balance_old': 'baseline', 'targets_old': 'baseline'},
        performance_preferences={'invented-workspace': 'money'})


def test_navigation_retains_inputs_but_not_triggers_uploads_or_editor_patches():
    saved = state()
    saved['targets_example_rows'] = {'edited_rows': {0: {'Name': 'Invented'}}}
    saved['strategic_crumb_example'] = False
    retained = set(retained_inputs(saved))
    assert {'exposure_market_basis', 'planning_amount', 'position_edit_fields_name', 'futures_input_contracts'} <= retained
    assert not {'planning_calculate', 'import_uploads', 'targets_example_rows',
                'strategic_crumb_example', 'analytics_context_overview_risk_calculate'} & retained
    saved['main_tabs'] = 'Exposure'
    assert 'exposure_market_basis' not in set(retained_inputs(saved))
    assert 'exposure_market_basis' in set(retained_inputs(saved, returning=retained))
    assert 'planning_calculate' not in set(retained_inputs(saved, returning=['planning_calculate']))


def test_remount_tracking_clears_on_workspace_and_tour_transitions():
    for event in ('workspace', 'tour'):
        saved = {'view_unmounted_inputs': ['exposure_market_basis']}
        assert capture_tour(saved) == {}
        transition(saved, event)
        assert saved == {}


def test_remount_is_restored_until_view_dispatch_completes(monkeypatch):
    from types import SimpleNamespace
    from portfolio_app import view_state
    class RecordedState(dict):
        def __setitem__(self, key, value):
            writes.append(key)
            super().__setitem__(key, value)
    writes = []
    saved = RecordedState(main_tabs='Positions', exposure_market_basis='Equity only')
    monkeypatch.setattr(view_state, 'st', SimpleNamespace(session_state=saved))
    view_state.preserve_view_inputs()
    view_state.finish_view_inputs()
    saved['main_tabs'] = 'Exposure'
    for _ in range(2):
        writes.clear()
        # Simulate a dialog returning before view dispatch on the first attempt.
        view_state.preserve_view_inputs()
        assert 'exposure_market_basis' in writes
    view_state.finish_view_inputs()
    writes.clear()
    view_state.preserve_view_inputs()
    assert 'exposure_market_basis' not in writes


def test_workspace_clears_owned_drafts_results_and_keeps_scoped_preferences():
    saved = state()
    transition(saved, 'workspace')
    assert saved == {'analytics_context_overview_years': 3,
                     'analytics_context_overview_risk_calculate': False,
                     'performance_preferences': {'invented-workspace': 'money'}}


def test_futures_date_drafts_survive_unmounts():
    saved = {'main_tabs': 'Overview', 'futures_input_start_date': date(2024, 1, 2),
             'futures_input_end_date': date(2025, 1, 2)}
    assert set(retained_inputs(saved)) == {'futures_input_start_date', 'futures_input_end_date'}
    transition(saved, 'workspace')
    assert saved == {}


def test_currency_clears_amounts_and_plans_but_keeps_scope_percentages_and_import_bytes():
    saved = state()
    transition(saved, 'currency')
    assert not {'planning_amount', 'planning_minimum', 'rebalance_result', 'position_edit_fields_name'} & saved.keys()
    assert saved['strategic_category'] == 'invented'
    assert saved['exposure_market_basis'] == 'Equity only'
    assert saved['import_files'][0][1] == b'id,name,shares\n'
    assert saved['view_editor_drafts'] == {'targets_old': 'percentage rows'}
    # This result retains its source assumptions; it is not silently recalculated.
    assert saved['futures_funding_result_context'] == ('invented-result', 'original assumptions')


def test_tour_capture_copies_editor_state_and_excludes_transient_widgets():
    saved = state()
    captured = capture_tour(saved)
    assert not {'planning_calculate', 'import_uploads', 'analytics_context_overview_risk_calculate'} & captured.keys()
    captured['view_editor_drafts']['targets_old'] = 'Changed tour copy'
    assert saved['view_editor_drafts']['targets_old'] == 'percentage rows'
    transition(saved, 'tour')
    assert saved == {'performance_preferences': {'invented-workspace': 'money'}}


def test_policy_ownership_is_unambiguous_for_declared_names_and_representative_prefixes():
    keys = {name for feature in FEATURES for name in feature.owned.names}
    keys |= {prefix + 'invented' for feature in FEATURES for prefix in feature.owned.prefixes}
    for key in keys:
        assert sum(feature.owned.matches(key) for feature in FEATURES) == 1, key
    with pytest.raises(ValueError):
        transition({}, 'unknown')
