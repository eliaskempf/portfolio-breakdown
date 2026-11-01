"""Tour lifecycle uses synthetic workspaces, never the user's working data."""
import json
from pathlib import Path

import pytest

from portfolio_app.demo import create_demo_data
from portfolio_app.settings import state_path
from portfolio_app.tour import dismissed, save_dismissal
from portfolio_app.tour_steps import STEPS
from test_startup import launch_workspaces
from test_onboarding_ui import add_category
from test_ui import activate, by_label


def click(app, label):
    button = app.button(key='tour_back') if label == 'Back' else by_label(app.button, label)
    button.click().run()
    assert not app.exception


def start(app):
    app.button(key='tour_start').click().run()
    assert not app.exception
    assert app.session_state['tour_step'] == 0


def assert_step(app, index):
    assert not app.exception
    assert app.session_state['main_tabs'] == STEPS[index].tab
    assert app.session_state['tour_step'] == index
    assert any(f'{index + 1} of {len(STEPS)}' in item.value for item in app.caption)


def tree_bytes(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}


def test_full_tour_demo_is_isolated_populated_and_returns_to_original(tmp_path):
    personal, demo = tmp_path / 'personal', create_demo_data(tmp_path / 'demo')
    before = tree_bytes(demo)
    app = launch_workspaces(personal, demo)
    click(app, 'Explore demo')
    assert app.session_state['tour_welcome']
    assert 'tour_step' not in app.session_state.to_dict()
    start(app)
    temporary = Path(app.session_state['tour_directory'].name)
    assert temporary != demo and (temporary / '.synthetic-demo').exists()
    assert app.button(key='tour_back').disabled
    click(app, 'Next')
    click(app, 'Back')
    assert_step(app, 0)
    for index in range(1, len(STEPS)):
        click(app, 'Next')
        assert_step(app, index)
        if index == 4:
            assert by_label(app.metric, 'Annualized volatility').value != '—'
        if index == 5:
            assert not by_label(app.toggle, 'Break down ETFs').value
        if index == 6:
            assert by_label(app.toggle, 'Break down ETFs').value
        if STEPS[index].calculate_plan:
            assert app.session_state['portfolio_contribution_result'][1]
    click(app, 'Finish')
    assert app.session_state['main_tabs'] == 'Overview'
    assert 'tour_step' not in app.session_state.to_dict()
    assert not temporary.exists()
    assert dismissed()
    assert tree_bytes(demo) == before
    assert not personal.exists()
    click(app, 'Take the tour')
    assert_step(app, 0)
    click(app, 'Skip tour')


@pytest.mark.parametrize('route', ['skip', 'later', 'save'])
def test_manual_invitation_waits_for_setup_and_returns_to_manual(tmp_path, route):
    personal = tmp_path / 'personal'
    app = launch_workspaces(personal, create_demo_data(tmp_path / 'demo'))
    click(app, 'Start my portfolio')
    assert 'tour_step' not in app.session_state.to_dict()
    if route == 'skip':
        click(app, 'Skip setup')
    else:
        add_category(app, 'Invented reserve')
        click(app, 'Save categories & continue')
        assert 'tour_welcome' not in app.session_state.to_dict()
        if route == 'later':
            click(app, 'Finish later')
        else:
            click(app, 'Enter manually')
            by_label(app.text_input, 'Instrument name').set_value('Invented holding')
            by_label(app.text_input, 'Quantity held (total)').set_value('1')
            click(app, 'Save position')
    assert app.session_state['tour_welcome']
    assert app.session_state['main_tabs'] == 'Overview'
    before = tree_bytes(personal)
    start(app)
    assert app.session_state['active_portfolio'] == 'Demo portfolio'
    demo_value = by_label(app.metric, 'Current value').value
    assert demo_value.startswith('€')
    assert float(demo_value.removeprefix('€').replace(',', '')) > 0
    click(app, 'Skip tour')
    assert app.session_state['active_portfolio'] == 'My portfolio'
    assert app.session_state['main_tabs'] == 'Overview'
    assert app.session_state['onboarding_started']
    assert not app.session_state.to_dict().get('tour_welcome')
    assert tree_bytes(personal) == before


@pytest.mark.parametrize('view', ['overview', 'exposure', 'rebalance'])
def test_replay_restores_view_filters_drafts_and_closes_menu(tmp_path, view):
    demo = create_demo_data(tmp_path / 'demo')
    app = launch_workspaces(tmp_path / 'personal', demo, start_demo=True)
    if view == 'overview':
        by_label(app.selectbox, 'Category').set_value('equity').run()
        by_label(app.get('button_group'), 'Overview view').set_value('Performance').run()
        expected = {'main_tabs':'Overview', 'strategic_category':'equity', 'strategic_view':'Performance'}
    elif view == 'exposure':
        activate(app, 'Exposure')
        by_label(app.text_input, 'Search exposure').set_value('Nvidia').run()
        by_label(app.toggle, 'Break down ETFs').set_value(False).run()
        expected = {'main_tabs':'Exposure', 'exposure_search':'Nvidia'}
    else:
        activate(app, 'Rebalance')
        by_label(app.number_input, 'Contribution').set_value(750.).run()
        click(app, 'Calculate plan')
        expected = {'main_tabs':'Rebalance', 'planning_amount':750., 'rebalance_tabs':'Plan'}
    app.session_state['help_menu'] = True
    click(app, 'Take the tour')
    assert_step(app, 0)
    assert not app.session_state.to_dict().get('help_menu')
    click(app, 'Skip to Exposure →')
    assert_step(app, 5)
    click(app, 'Skip tour')
    for key, value in expected.items():
        assert app.session_state[key] == value
    if view == 'exposure':
        assert not by_label(app.toggle, 'Break down ETFs').value
    elif view == 'rebalance':
        assert app.session_state['portfolio_contribution_result'][1]


def test_not_now_is_remembered_but_help_still_replays(tmp_path):
    personal, demo = tmp_path / 'personal', create_demo_data(tmp_path / 'demo')
    app = launch_workspaces(personal, demo)
    click(app, 'Explore demo')
    click(app, 'Not now')
    assert dismissed()
    restarted = launch_workspaces(personal, demo)
    click(restarted, 'Explore demo')
    assert 'tour_welcome' not in restarted.session_state.to_dict()
    click(restarted, 'Take the tour')
    assert_step(restarted, 0)
    click(restarted, 'Skip tour')


@pytest.mark.parametrize('currency', ['USD', 'GBP'])
@pytest.mark.parametrize('start_demo', [False, True])
def test_tour_restores_currency_and_saved_plan(tmp_path, currency, start_demo):
    from portfolio_app.portfolio_settings import load_settings, save_settings
    personal = create_demo_data(tmp_path / 'invented-personal')
    demo = create_demo_data(tmp_path / 'demo')
    original = demo if start_demo else personal
    save_settings(original, currency, None)
    before = tree_bytes(original)
    app = launch_workspaces(personal, demo, start_demo=start_demo)
    activate(app, 'Rebalance')
    by_label(app.number_input, 'Contribution').set_value(750.).run()
    click(app, 'Calculate plan')
    plan = app.session_state['portfolio_contribution_result']
    context = app.session_state['currency_context']
    click(app, 'Take the tour')
    assert app.session_state['reporting_currency'] == 'EUR'
    click(app, 'Skip tour')
    assert app.session_state['reporting_currency'] == currency
    assert app.session_state['currency_context'] == context
    assert app.session_state['main_tabs'] == 'Rebalance'
    assert by_label(app.number_input, 'Contribution').value == 750.
    assert app.session_state['portfolio_contribution_result'][0] == plan[0]
    assert load_settings(original).reporting_currency == currency
    assert tree_bytes(original) == before


def test_unwritable_preference_still_restores_workspace(tmp_path, monkeypatch):
    app = launch_workspaces(tmp_path / 'personal', create_demo_data(tmp_path / 'demo'))
    click(app, 'Explore demo')
    start(app)
    def fail():
        raise OSError('Injected write failure')
    monkeypatch.setattr('portfolio_app.tour.save_dismissal', fail)
    click(app, 'Skip tour')
    assert 'tour_step' not in app.session_state.to_dict()
    assert any('Could not save' in item.value for item in app.caption)


def test_missing_corrupt_and_atomic_preference(tmp_path):
    assert not dismissed()
    directory = state_path()
    directory.mkdir()
    preference = directory / 'app-tour.json'
    for contents in ('broken', '[]', '{"dismissed": "true"}'):
        preference.write_text(contents)
        assert not dismissed()
    save_dismissal()
    assert dismissed()
    assert json.loads(preference.read_text()) == {'dismissed': True}
    assert list(directory.iterdir()) == [preference]


def test_demo_invitation_waits_until_overview_is_ready(tmp_path, monkeypatch):
    pending = True
    monkeypatch.setattr('portfolio_app.ui.live_demo_pending', lambda directory: pending)
    monkeypatch.setattr('portfolio_app.ui.initialize_live_demo', lambda *args: False)
    app = launch_workspaces(tmp_path / 'personal', create_demo_data(tmp_path / 'demo'))
    click(app, 'Explore demo')
    assert 'tour_welcome' not in app.session_state.to_dict()
    assert app.session_state['tour_request']['workspace'] == 'demo'
    pending = False
    app.run()
    assert app.session_state['tour_welcome']


def test_closed_first_position_ignores_delayed_valuation_callback(tmp_path, monkeypatch):
    app = launch_workspaces(tmp_path / 'personal', create_demo_data(tmp_path / 'demo'))
    click(app, 'Start my portfolio')
    add_category(app, 'Invented reserve')
    click(app, 'Save categories & continue')
    by_label(app.get('button_group'), 'Position type').set_value('Physical asset').run()
    radio = by_label(app.radio, 'Valuation method')
    # AppTest exposes a mapping facade around SafeSessionState in Streamlit 1.65.
    callback = app.session_state._state._state._new_widget_state.widget_metadata[radio.proto.id].callback
    click(app, 'Finish later')
    closed_state = {}
    monkeypatch.setattr('portfolio_app.position_ui.st.session_state', closed_state)
    callback()
    assert closed_state == {}


def test_monitoring_tour_saves_acknowledges_and_restores_original_draft(tmp_path, monkeypatch):
    from portfolio_app.monitoring import Rule, SeriesSpec, Evaluation
    from portfolio_app.monitoring_store import load_monitoring, save_rule, record_evaluations
    from portfolio_app.monitoring_service import monitoring_for
    original = create_demo_data(tmp_path / 'invented-original')
    rule = Rule('original-example', 'Invented original rule', series=SeriesSpec('INVENTED'))
    save_rule(original, rule)
    record_evaluations(original, rule, [Evaluation('2026-10-13', -3., condition='triggered')])
    # Keep original observations fixed; this test concerns the tour's isolation.
    monkeypatch.setattr(monitoring_for(original, demo=True), 'schedule', lambda **kw: None)
    app = launch_workspaces(tmp_path / 'personal', original, start_demo=True)
    activate(app, 'Monitoring')
    by_label(app.number_input, 'Distance from SMA trigger (%)').set_value(-7.).run()
    before = tree_bytes(original)
    click(app, 'Take the tour')
    temporary = Path(app.session_state['tour_directory'].name)
    first = next(i for i, s in enumerate(STEPS) if s.tab == 'Monitoring')
    for _ in range(first):
        click(app, 'Next')
    assert by_label(app.number_input, 'Distance from SMA trigger (%)').value is None
    assert load_monitoring(temporary)[0]['rules'] == []
    click(app, 'Next')
    assert by_label(app.number_input, 'Distance from SMA trigger (%)').value == 10.
    assert load_monitoring(temporary)[0]['rules'] == []
    click(app, 'Next')
    doc, _ = load_monitoring(temporary)
    assert len(doc['rules']) == len(doc['events']) == 1
    assert doc['events'][0]['condition'] == 'triggered'
    click(app, 'Next')
    click(app, 'Acknowledge alert')
    doc, _ = load_monitoring(temporary)
    assert doc['events'][0]['acknowledged'] and doc['rules'][0]['enabled']
    click(app, 'Back')
    click(app, 'Next')
    assert len(load_monitoring(temporary)[0]['events']) == 1
    assert any('Alert acknowledged' in x.value for x in app.success)
    click(app, 'Finish')
    assert not temporary.exists()
    assert app.session_state['main_tabs'] == 'Monitoring'
    assert by_label(app.number_input, 'Distance from SMA trigger (%)').value == -7.
    assert tree_bytes(original) == before


def test_monitoring_tour_rejects_non_tour_workspace(tmp_path):
    from portfolio_app.tour_monitoring import prepare_step
    directory = create_demo_data(tmp_path / 'invented-non-tour')
    before = tree_bytes(directory)
    with pytest.raises(ValueError, match='own synthetic workspace'):
        prepare_step(directory, 'Tracked rules')
    assert tree_bytes(directory) == before


def test_tour_monitoring_completion_uses_canonical_workspace(tmp_path, monkeypatch):
    from threading import Event
    from types import SimpleNamespace
    from portfolio_app import monitoring_service, tour
    from portfolio_app.market_data import RequestCoordinator
    from portfolio_app.monitoring import SeriesSpec

    # A noncanonical temporary root exercises the same identity mismatch as
    # Windows short names and symlinked temp roots, without requiring symlinks.
    (tmp_path / 'parent').mkdir()
    actual = tmp_path / 'invented-tour'
    actual.mkdir()
    alias = tmp_path / 'parent' / '..' / actual.name
    owner = SimpleNamespace(name=str(alias))
    monkeypatch.setattr(tour, 'st', SimpleNamespace(session_state={
        'tour_step': 15, 'tour_directory': owner}))
    directory = tour.demo_directory()
    coordinator = RequestCoordinator(workers=1)
    monkeypatch.setattr(monitoring_service, 'coordinator', coordinator)
    service = monitoring_service.MonitoringService(directory, demo=True)
    started = Event()
    get_series = service.market.get
    def fetch(*args, **kwargs):
        started.set()
        return get_series(*args, **kwargs)
    monkeypatch.setattr(service.market, 'get', fetch)
    spec = SeriesSpec('MSCI:990100:NETR:EUR', currency='EUR')
    try:
        assert service.browse(spec) is None
        assert started.wait(timeout=5), 'Synthetic monitoring worker did not start'
    finally:
        coordinator.close()
    # The UI polls the resolved identity; completion must reach that same key.
    assert coordinator.revision(directory.resolve()) == 1
    assert service.browse(spec).dates
    assert owner.name == str(alias)
