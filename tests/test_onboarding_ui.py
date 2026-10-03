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


def test_live_demo_uses_normal_market_mode_and_preserves_workspace_isolation(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from test_ui import activate
    from portfolio_app.history import HistoryResult
    from types import SimpleNamespace
    personal = tmp_path / 'invented-personal'
    live = create_demo_data(tmp_path / 'live', live=True)
    fixture = create_demo_data(tmp_path / 'offline')
    calls = []
    monkeypatch.setattr('portfolio_app.etf_refresh.coordinator.schedule',
                        lambda *a, **kw: calls.append(kw['demo']))
    app = AppTest.from_string(
        'from pathlib import Path\n'
        'from portfolio_app.ui import render_app\n'
        'from portfolio_app.prices import PriceService, StaticProvider\n'
        f'prices = PriceService(StaticProvider(Path({str(fixture / "demo_prices.json")!r})))\n'
        f'render_app(Path({str(personal)!r}), demo_dir=Path({str(live)!r}), demo=True, price_service=prices)\n',
        default_timeout=15,
    ).run()
    assert not app.exception and app.tabs
    assert calls and not any(calls)
    assert not by_label(app.button, 'Refresh prices').disabled
    assert any('Public market data' in caption.value for caption in app.caption)
    initial = (live / 'holdings.csv').read_bytes()
    by_label(app.button, 'Refresh prices').click().run()
    assert (live / 'holdings.csv').read_bytes() == initial
    assert not (personal / 'holdings.csv').exists()
    # Opening a live demo position must use the ordinary history path.
    requested = []
    def history(directory):
        requested.append(directory)
        return SimpleNamespace(get=lambda *a, **kw: HistoryResult(note='Injected public-history path'),
                               pending=lambda *a: False)
    monkeypatch.setattr('portfolio_app.position_detail.history_for', history)
    snapshot = read_snapshot(live / 'holdings.csv')
    app.session_state['position_edit_selected'] = snapshot.holdings.position_id.iloc[0]
    app.session_state['position_edit_dialog'] = True
    app.session_state['position_edit_action'] = 'Details'
    activate(app, 'Positions')
    assert not app.exception
    assert requested == [live]
    assert any('Injected public-history path' in info.value for info in app.info)
    by_label(app.radio, 'Portfolio workspace').set_value('My portfolio').run()
    assert not app.exception and not app.tabs
