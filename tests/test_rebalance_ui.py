"""UI checks use deliberately invented positions and static prices in temp files."""

import shutil

import pytest

from test_ui import by_label, launch
from portfolio_app.rebalance_ui import MODES


@pytest.fixture
def rebalance_data(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    (tmp_path / "holdings.csv").write_text(
        "id,name,ticker,shares,target_allocation\n"
        "a,Synthetic A,NVDA,1,0.4\nb,Synthetic B,TSM,0.25,0.3\n"
        "c,Synthetic C,ENR.DE,0.5,0.3\n"
    )  # Values 80, 10, 10 EUR.
    return tmp_path


def metrics(app):
    return {item.label: item.value for item in app.metric}


def calculate(app):
    by_label(app.button, "Calculate rebalance").click().run()
    assert not app.exception


def test_modes_tradeoff_selection_and_stale_plan_invalidation(rebalance_data):
    before = (rebalance_data / "holdings.csv").read_bytes()
    app = launch(rebalance_data)
    assert not app.exception
    assert [tab.label for tab in app.tabs][:3] == ["Overview", "Rebalance", "Manage positions"]
    by_label(app.number_input, "Allowed deviation (pp)").set_value(0).run()
    calculate(app)
    assert metrics(app)["Trades"] == "3"
    assert metrics(app)["New money"] == "€0.00"
    by_label(app.selectbox, "Rebalancing mode").set_value(MODES[1]).run()
    assert "Trades" not in metrics(app)
    calculate(app)
    assert metrics(app)["Minimum new money"] == "€100.00"
    assert metrics(app)["Trades"] == "2"
    by_label(app.selectbox, "Rebalancing mode").set_value(MODES[2]).run()
    by_label(app.number_input, "New money (EUR)").set_value(100).run()
    by_label(app.number_input, "Maximum trades").set_value(2).run()
    calculate(app)
    assert metrics(app)["Trades"] == "2"
    assert metrics(app)["Deviation outside ranges"] == "0.000 pp"
    by_label(app.selectbox, "Plan to inspect").set_value(0).run()
    assert metrics(app)["Trades"] == "1"
    assert metrics(app)["Deviation outside ranges"] == "50.000 pp"
    by_label(app.number_input, "New money (EUR)").set_value(110).run()
    assert "Trades" not in metrics(app)
    assert (rebalance_data / "holdings.csv").read_bytes() == before


def test_empty_positions_hidden_targets_redistributed_editor_retains_original(rebalance_data):
    path = rebalance_data / "holdings.csv"
    with path.open("a") as file:
        file.write("d,Synthetic D,UNQUOTED,0,0.2\n")
    path.write_text(path.read_text().replace("1,0.4", "1,0.2"))
    before = path.read_bytes()
    app = launch(rebalance_data)
    by_label(app.checkbox, "No new positions").check().run()
    calculate(app)
    assert any("feasible" in item.value for item in app.error)
    by_label(app.checkbox, "Ignore empty positions").check().run()
    assert not app.exception
    holdings = next(item.value for item in app.dataframe if "shares" in item.value)
    assert len(holdings) == 3
    assert holdings.target_allocation.sum() == pytest.approx(100)
    assert sorted(holdings.target_allocation) == pytest.approx([100 * (.2 + .2/3), 100 * (.3 + .2/3), 100 * (.3 + .2/3)])
    assert any("Synthetic D" in str(item.options) for item in app.selectbox)
    calculate(app)
    assert "Trades" in metrics(app)
    assert not app.error
    by_label(app.checkbox, "Ignore empty positions").uncheck().run()
    holdings = next(item.value for item in app.dataframe if "shares" in item.value)
    assert len(holdings) == 4
    assert "Trades" not in metrics(app)
    assert path.read_bytes() == before


def test_overview_filter_does_not_change_trade_universe(rebalance_data):
    app = launch(rebalance_data)
    by_label(app.multiselect, "Holdings").set_value([]).run()
    calculate(app)
    assert metrics(app)["Trades"] == "3"
    assert any("No holdings match" in item.value for item in app.info)
    plan = next(item.value for item in app.dataframe if "After (EUR)" in item.value)
    assert len(plan) == 3
    assert plan["After (EUR)"].sum() == pytest.approx(100)


def test_missing_targets_and_incomplete_redistribution_are_explained(rebalance_data):
    path = rebalance_data / "holdings.csv"
    path.write_text(path.read_text().replace("1,0.4", "1,"))
    app = launch(rebalance_data)
    assert any("target for every position" in item.value for item in app.info)
    assert not any(item.label == "Calculate rebalance" for item in app.button)
    with path.open("a") as file:
        file.write("d,Synthetic D,UNQUOTED,0,0.2\n")
    app = launch(rebalance_data)
    by_label(app.checkbox, "Ignore empty positions").check().run()
    assert not app.exception
    assert any("missing targets are unknown" in item.value for item in app.error)


def test_all_empty_positions_have_useful_message_and_can_allocate_cash(rebalance_data):
    path = rebalance_data / "holdings.csv"
    path.write_text("id,name,ticker,shares,target_allocation\na,Synthetic A,NVDA,0,0.4\nb,Synthetic B,TSM,0,0.6\n")
    app = launch(rebalance_data)
    by_label(app.checkbox, "Ignore empty positions").check().run()
    assert not app.exception
    assert any("All positions have zero shares" in item.value for item in app.info)
    by_label(app.checkbox, "Ignore empty positions").uncheck().run()
    by_label(app.selectbox, "Rebalancing mode").set_value(MODES[2]).run()
    by_label(app.number_input, "New money (EUR)").set_value(100).run()
    calculate(app)
    assert metrics(app)["Trades"] == "2"
    assert metrics(app)["New money"] == "€100.00"


def test_buy_selection_constrains_cash_and_invalidates_previous_plan(rebalance_data):
    path = rebalance_data / "holdings.csv"
    before = path.read_bytes()
    app = launch(rebalance_data)
    by_label(app.selectbox, "Rebalancing mode").set_value(MODES[2]).run()
    by_label(app.number_input, "New money (EUR)").set_value(100).run()
    by_label(app.checkbox, "Limit buys to selected positions").check().run()
    calculate(app)
    assert any("select at least one" in item.value for item in app.error)
    by_label(app.multiselect, "Positions eligible for buying").set_value(["position-1"]).run()
    calculate(app)
    plan = next(item.value for item in app.dataframe if "Action" in item.value)
    assert plan.Investment.tolist() == ["Synthetic B"]
    assert plan["Trade (EUR)"].tolist() == pytest.approx([100])
    by_label(app.multiselect, "Positions eligible for buying").set_value(["position-1", "position-2"]).run()
    assert "Trades" not in metrics(app)
    by_label(app.multiselect, "Holdings").set_value([]).run()
    calculate(app)
    assert metrics(app)["Trades"] == "2"
    assert metrics(app)["Deviation outside ranges"] == "0.000 pp"
    assert path.read_bytes() == before
    by_label(app.selectbox, "Rebalancing mode").set_value(MODES[0]).run()
    calculate(app)
    assert metrics(app)["Trades"] == "3"  # Buy-only subset does not leak into the sell/buy mode.


def test_buy_selection_empty_positions_and_changed_universe_fail_closed(rebalance_data):
    path = rebalance_data / "holdings.csv"
    with path.open("a") as handle:
        handle.write("d,Synthetic D,UNKNOWN,0,0\n")
    app = launch(rebalance_data)
    by_label(app.selectbox, "Rebalancing mode").set_value(MODES[2]).run()
    by_label(app.checkbox, "Limit buys to selected positions").check().run()
    by_label(app.multiselect, "Positions eligible for buying").set_value(["position-3"]).run()
    by_label(app.checkbox, "No new positions").check().run()
    calculate(app)
    assert any("No feasible buy" in item.value for item in app.error)
    by_label(app.checkbox, "Ignore empty positions").check().run()
    assert not app.exception
    assert by_label(app.multiselect, "Positions eligible for buying").value == []
    assert len(by_label(app.multiselect, "Positions eligible for buying").options) == 3
    calculate(app)
    assert any("select at least one" in item.value for item in app.error)
