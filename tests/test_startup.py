from test_ui import position_action
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.app import main
from portfolio_app.demo import create_demo_data
from portfolio_app.holdings import load_holdings
from portfolio_app.positions import read_snapshot, save_position
from test_ui import by_label


def test_each_launch_gets_editable_fresh_demo_and_preserves_personal_data(tmp_path, monkeypatch):
    personal = tmp_path / "data" / "portfolio"
    personal.mkdir(parents=True)
    original = "id,name,shares\nexample,Synthetic saved position,3\n"
    (personal / "holdings.csv").write_text(original)
    # Earlier demo files are also never overwritten by startup.
    old_demo = tmp_path / "data" / "demo"
    old_demo.mkdir()
    (old_demo / "holdings.csv").write_text("Keep this earlier file")
    launched = []

    def run(command):
        assert Path(command[command.index("--data-dir") + 1]) == personal
        demo = Path(command[command.index("--demo-dir") + 1])
        launched.append(demo)
        snapshot = read_snapshot(demo / "holdings.csv")
        assert snapshot.holdings.iloc[0]["shares"] == 2
        save_position(demo / "holdings.csv", {"shares": "99"}, expected_revision=snapshot.revision, position_id="position-0")
        assert load_holdings(demo / "holdings.csv").iloc[0]["shares"] == 99
        return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["portfolio-app"])
    monkeypatch.setattr("portfolio_app.app.subprocess.call", run)
    for _ in range(2):
        with pytest.raises(SystemExit) as result:
            main()
        assert result.value.code == 0
        assert not launched[-1].exists()
        assert (personal / "holdings.csv").read_text() == original
    assert launched[0] != launched[1]
    assert (old_demo / "holdings.csv").read_text() == "Keep this earlier file"


def test_demo_flag_never_resets_custom_personal_directory(tmp_path, monkeypatch):
    original = tmp_path / "holdings.csv"
    original.write_text("Existing data remains untouched")

    def run(command):
        assert command[-1] == "--demo"
        assert "--server.port=8510" in command
        assert Path(command[command.index("--data-dir") + 1]) == tmp_path
        assert Path(command[command.index("--demo-dir") + 1]) != tmp_path
        return 0

    monkeypatch.setattr(sys, "argv", ["portfolio-app", "--demo", "--data-dir", str(tmp_path), "--server.port=8510"])
    monkeypatch.setattr("portfolio_app.app.subprocess.call", run)
    with pytest.raises(SystemExit):
        main()
    assert original.read_text() == "Existing data remains untouched"


def test_interrupted_start_cleans_temporary_demo(tmp_path, monkeypatch):
    launched = []

    def interrupt(command):
        launched.append(Path(command[command.index("--demo-dir") + 1]))
        raise KeyboardInterrupt

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["portfolio-app"])
    monkeypatch.setattr("portfolio_app.app.subprocess.call", interrupt)
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 130
    assert not launched[0].exists()


def launch_workspaces(personal, demo, *, start_demo=False):
    return AppTest.from_string(
        "from pathlib import Path\n"
        "from portfolio_app.ui import render_app\n"
        "from portfolio_app.prices import PriceService, StaticProvider\n"
        f"prices = PriceService(StaticProvider(Path({str(demo / 'demo_prices.json')!r})))\n"
        f"render_app(Path({str(personal)!r}), demo_dir=Path({str(demo)!r}), demo={start_demo!r}, price_service=prices)\n",
        default_timeout=15,
    ).run()


def test_demo_edits_survive_rerun_and_workspace_switch_but_not_new_start(tmp_path):
    personal = tmp_path / "personal"
    demo = create_demo_data(tmp_path / "first-start")
    app = launch_workspaces(personal, demo)
    assert not app.exception
    assert by_label(app.radio, "Portfolio workspace").value == "My portfolio"
    by_label(app.radio, "Portfolio workspace").set_value("Demo portfolio").run()
    assert app.metric[0].value == "€744.00"
    position_action(app, "Edit position")
    by_label(app.number_input, "Quantity held (total)").set_value(9.)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert load_holdings(demo / "holdings.csv").iloc[0]["shares"] == 9
    edited_total = app.metric[0].value
    app.run()
    assert app.metric[0].value == edited_total
    by_label(app.radio, "Portfolio workspace").set_value("My portfolio").run()
    assert not app.exception
    position_action(app, "Add position")
    assert by_label(app.text_input, "Instrument name").value == ""
    by_label(app.text_input, "Instrument name").set_value("Synthetic personal position")
    by_label(app.number_input, "Quantity held (total)").set_value(4.)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert load_holdings(personal / "holdings.csv").iloc[0]["shares"] == 4
    by_label(app.radio, "Portfolio workspace").set_value("Demo portfolio").run()
    assert app.metric[0].value == edited_total
    second_demo = create_demo_data(tmp_path / "second-start")
    restarted = launch_workspaces(personal, second_demo, start_demo=True)
    assert not restarted.exception
    assert restarted.metric[0].value == "€744.00"
    by_label(restarted.radio, "Portfolio workspace").set_value("My portfolio").run()
    assert next(item.value for item in restarted.tabs[1].dataframe if "shares" in item.value).iloc[0]["shares"] == 4


def test_breakdown_choice_survives_workspace_widget_cleanup(tmp_path):
    demo = create_demo_data(tmp_path / 'demo')
    app = launch_workspaces(tmp_path / 'personal', demo, start_demo=True)
    assert by_label(app.toggle, 'Break down ETFs').value
    by_label(app.toggle, 'Break down ETFs').set_value(False).run()
    by_label(app.radio, 'Portfolio workspace').set_value('My portfolio').run()
    by_label(app.radio, 'Portfolio workspace').set_value('Demo portfolio').run()
    assert not app.exception
    assert not by_label(app.toggle, 'Break down ETFs').value


def test_switching_workspaces_discards_unsubmitted_form_and_filters(tmp_path):
    demo = create_demo_data(tmp_path / "demo")
    app = launch_workspaces(tmp_path / "personal", demo, start_demo=True)
    by_label(app.multiselect, "Holdings").set_value([]).run()
    position_action(app, 'Add position')
    by_label(app.text_input, "Instrument name").set_value("Unsubmitted dummy edit")
    by_label(app.radio, "Portfolio workspace").set_value("My portfolio").run()
    assert not app.exception
    assert not app.session_state.filtered_state.get("position_edit_dialog")
    assert "position_draft" not in app.session_state.filtered_state
    by_label(app.radio, "Portfolio workspace").set_value("Demo portfolio").run()
    position_action(app, 'Add position')
    assert by_label(app.text_input, "Instrument name").value == ""
    assert len(by_label(app.multiselect, "Holdings").value) == 6


def test_performance_unit_is_remembered_per_workspace(tmp_path):
    demo = create_demo_data(tmp_path / 'demo')
    app = launch_workspaces(tmp_path / 'personal', demo, start_demo=True)
    by_label(app.get('button_group'), 'Performance display').set_value('%').run()
    by_label(app.radio, 'Portfolio workspace').set_value('My portfolio').run()
    assert by_label(app.get('button_group'), 'Performance display').value == '€'
    by_label(app.radio, 'Portfolio workspace').set_value('Demo portfolio').run()
    assert by_label(app.get('button_group'), 'Performance display').value == '%'
