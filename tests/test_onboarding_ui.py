"""First-use choices use isolated, invented workspaces and existing save flows."""
from portfolio_app.demo import create_demo_data
from portfolio_app.positions import read_snapshot
from test_startup import launch_workspaces
from test_ui import by_label


def test_welcome_demo_and_manual_save_are_isolated(tmp_path):
    personal = tmp_path / 'invented-personal'
    demo = create_demo_data(tmp_path / 'demo')
    app = launch_workspaces(personal, demo)
    assert not app.exception and not app.tabs
    assert {button.label for button in app.button} >= {'Explore demo', 'Start manually', 'Import holdings'}
    by_label(app.button, 'Explore demo').click().run()
    assert not app.exception
    assert by_label(app.radio, 'Portfolio workspace').value == 'Demo portfolio'
    assert app.metric[0].value == '€100,000.00'
    assert not (personal / 'holdings.csv').exists()
    by_label(app.radio, 'Portfolio workspace').set_value('My portfolio').run()
    by_label(app.button, 'Start manually').click().run()
    assert not app.exception
    by_label(app.text_input, 'Instrument name').set_value('Invented first position')
    by_label(app.number_input, 'Quantity held (total)').set_value(2.)
    by_label(app.button, 'Save position').click().run()
    assert not app.exception
    assert read_snapshot(personal / 'holdings.csv').holdings.shares.tolist() == [2.]
    assert read_snapshot(demo / 'holdings.csv').holdings.shares.iloc[0] == 450
    restarted = launch_workspaces(personal, demo)
    assert not restarted.exception and restarted.tabs
    assert 'Explore demo' not in {button.label for button in restarted.button}


def test_import_choice_and_workspace_switch_clear_first_use_state(tmp_path):
    personal = tmp_path / 'invented-personal'
    demo = create_demo_data(tmp_path / 'demo')
    app = launch_workspaces(personal, demo)
    by_label(app.button, 'Import holdings').click().run()
    assert not app.exception
    assert app.session_state['main_tabs'] == 'Positions'
    assert by_label(app.get('button_group'), 'Position tools').value == 'Import portfolio'
    assert app.get('file_uploader')
    assert not (personal / 'holdings.csv').exists()
    by_label(app.radio, 'Portfolio workspace').set_value('Demo portfolio').run()
    by_label(app.radio, 'Portfolio workspace').set_value('My portfolio').run()
    assert not app.exception and not app.tabs
    assert 'onboarding_started' not in app.session_state.filtered_state
    assert not any(key.startswith('import_') for key in app.session_state.filtered_state)
