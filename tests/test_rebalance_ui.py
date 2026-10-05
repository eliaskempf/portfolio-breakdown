from list_helpers import list_frame
from test_ui import position_action
"""UI checks use deliberately invented positions and static prices in temp files."""

import shutil

import pytest

from test_ui import by_label, launch as launch_app, activate

def launch(path):
    return launch_app(path, tab="Rebalance", subtab="Plan")
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
    activate(app, "Rebalance", "Plan")
    by_label(app.button, "Calculate plan").click().run()
    assert not app.exception


def test_modes_tradeoff_selection_and_stale_plan_invalidation(rebalance_data):
    before = (rebalance_data / "holdings.csv").read_bytes()
    app = launch(rebalance_data)
    assert not app.exception
    assert [tab.label for tab in app.tabs][:4] == ["Overview", "Exposure", "Positions", "Rebalance"]
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
    by_label(app.toggle, "Only buy existing positions").set_value(True).run()
    calculate(app)
    assert any("feasible" in item.value for item in app.error)
    by_label(app.toggle, "Exclude empty positions and redistribute targets").set_value(True).run()
    assert not app.exception
    activate(app, "Exposure")
    holdings = next(item.value for item in app.dataframe if "shares" in item.value)
    activate(app, "Rebalance", "Plan")
    assert len(holdings) == 4  # Planning controls do not filter Exposure.
    assert holdings.target_allocation.sum() == pytest.approx(100)
    assert sorted(holdings.target_allocation) == [20, 20, 30, 30]
    by_label(app.checkbox, 'Hide empty positions').check().run()
    activate(app, 'Exposure')
    holdings = next(item.value for item in app.tabs[1].dataframe if 'shares' in item.value)
    assert len(holdings) == 3
    assert sorted(holdings.target_allocation) == [20, 30, 30]  # Hiding doesn't redistribute.
    position_action(app, 'Edit position', position_id='position-3')
    assert by_label(app.text_input, 'Instrument name').value == 'Synthetic D'
    by_label(app.button, 'Cancel').click().run()
    calculate(app)
    assert "Trades" in metrics(app)
    assert not app.error
    by_label(app.toggle, "Exclude empty positions and redistribute targets").set_value(False).run()
    activate(app, "Exposure")
    holdings = next(item.value for item in app.dataframe if "shares" in item.value)
    activate(app, "Rebalance", "Plan")
    assert len(holdings) == 3  # Visibility is controlled separately.
    assert "Trades" not in metrics(app)
    assert path.read_bytes() == before


def test_overview_filter_does_not_change_trade_universe(rebalance_data):
    app = launch(rebalance_data)
    activate(app, "Exposure")
    by_label(app.multiselect, "Holdings").set_value([]).run()
    activate(app, "Rebalance", "Plan")
    calculate(app)
    assert metrics(app)["Trades"] == "3"
    activate(app, "Exposure")
    assert any("No holdings match" in item.value for item in app.info)
    activate(app, "Rebalance", "Plan")
    plan = next(item.value for item in app.dataframe if "After" in item.value)
    assert len(plan) == 3
    assert plan["After"].sum() == pytest.approx(100)


def test_missing_targets_and_incomplete_redistribution_are_explained(rebalance_data):
    path = rebalance_data / "holdings.csv"
    path.write_text(path.read_text().replace("1,0.4", "1,"))
    app = launch(rebalance_data)
    assert any("target for every position" in item.value for item in app.info)
    assert not any(item.label == "Calculate plan" for item in app.button)
    with path.open("a") as file:
        file.write("d,Synthetic D,UNQUOTED,0,0.2\n")
    app = launch(rebalance_data)
    by_label(app.toggle, "Exclude empty positions and redistribute targets").set_value(True).run()
    assert not app.exception
    assert any("missing targets are unknown" in item.value for item in app.error)


def test_all_empty_positions_have_useful_message_and_can_allocate_cash(rebalance_data):
    path = rebalance_data / "holdings.csv"
    path.write_text("id,name,ticker,shares,target_allocation\na,Synthetic A,NVDA,0,0.4\nb,Synthetic B,TSM,0,0.6\n")
    app = launch(rebalance_data)
    by_label(app.toggle, "Exclude empty positions and redistribute targets").set_value(True).run()
    assert not app.exception
    assert any("All positions have zero shares" in item.value for item in app.info)
    by_label(app.toggle, "Exclude empty positions and redistribute targets").set_value(False).run()
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
    by_label(app.toggle, "Limit buys to selected positions").set_value(True).run()
    by_label(app.selectbox, "Distribution").set_value("Optimize rebalancing").run()
    calculate(app)
    assert any("select at least one" in item.value for item in app.error)
    by_label(app.multiselect, "Positions eligible for buying").set_value(["position-1"]).run()
    calculate(app)
    plan = list_frame(app, 'Suggested trades')
    assert plan.Investment.tolist() == ["Synthetic B"]
    assert plan["Trade"].tolist() == pytest.approx([100])
    by_label(app.multiselect, "Positions eligible for buying").set_value(["position-1", "position-2"]).run()
    assert "Trades" not in metrics(app)
    activate(app, "Exposure")
    by_label(app.multiselect, "Holdings").set_value([]).run()
    activate(app, "Rebalance", "Plan")
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
    by_label(app.toggle, "Limit buys to selected positions").set_value(True).run()
    by_label(app.selectbox, "Distribution").set_value("Optimize rebalancing").run()
    by_label(app.multiselect, "Positions eligible for buying").set_value(["position-3"]).run()
    by_label(app.toggle, "Only buy existing positions").set_value(True).run()
    calculate(app)
    assert any("No feasible buy" in item.value for item in app.error)
    by_label(app.toggle, "Exclude empty positions and redistribute targets").set_value(True).run()
    assert not app.exception
    assert by_label(app.multiselect, "Positions eligible for buying").value == []
    assert len(by_label(app.multiselect, "Positions eligible for buying").options) == 3
    calculate(app)
    assert any("select at least one" in item.value for item in app.error)


def test_target_gap_balancing_is_default_for_selected_positions(rebalance_data):
    original = (rebalance_data / "holdings.csv").read_bytes()
    app = launch(rebalance_data)
    by_label(app.selectbox, "Rebalancing mode").set_value(MODES[2]).run()
    by_label(app.number_input, "New money (EUR)").set_value(100).run()
    by_label(app.number_input, "Maximum trades").set_value(1).run()
    by_label(app.toggle, "Limit buys to selected positions").set_value(True).run()
    assert by_label(app.selectbox, "Distribution").value == "Rebalance selected positions"
    assert not any(item.label == "Maximum trades" for item in app.number_input)
    by_label(app.multiselect, "Positions eligible for buying").set_value(["position-0", "position-1", "position-2"]).run()
    calculate(app)
    assert by_label(app.selectbox, "Selection intent").value == "Buy every selected position"
    assert metrics(app)["Trades"] == "3"
    plan = list_frame(app, 'Suggested trades')
    assert plan.Investment.tolist() == ["Synthetic B", "Synthetic C", "Synthetic A"]
    assert plan["Trade"].tolist() == pytest.approx([37.5, 37.5, 25])
    assert "squared percentage-point gaps" in by_label(app.selectbox, "Distribution").proto.help
    assert not any("closest allocation" in item.value for item in app.info)
    by_label(app.selectbox, "Distribution").set_value("Spread by target weights").run()
    assert "Trades" not in metrics(app)
    calculate(app)
    plan = list_frame(app, 'Suggested trades')
    assert plan["Trade"].tolist() == pytest.approx([40, 30, 30])
    by_label(app.selectbox, "Distribution").set_value("Optimize rebalancing").run()
    assert any(item.label == "Maximum trades" for item in app.number_input)
    assert (rebalance_data / "holdings.csv").read_bytes() == original


def test_purchase_intent_minimum_and_fewer_trade_comparison(rebalance_data):
    before = (rebalance_data / "holdings.csv").read_bytes()
    app = launch(rebalance_data)
    by_label(app.selectbox, "Rebalancing mode").set_value(MODES[2]).run()
    by_label(app.number_input, "New money (EUR)").set_value(70).run()
    by_label(app.toggle, "Limit buys to selected positions").set_value(True).run()
    by_label(app.multiselect, "Positions eligible for buying").set_value(["position-0", "position-1", "position-2"]).run()
    calculate(app)
    assert any("at least 75.00" in item.value for item in app.error)
    by_label(app.number_input, "Minimum purchase (EUR)").set_value(20).run()
    calculate(app)
    assert metrics(app)["Trades"] == "3"
    by_label(app.selectbox, "Selection intent").set_value("Allow skipping positions").run()
    assert "Trades" not in metrics(app)
    assert by_label(app.toggle, "Prefer fewer trades").value is False
    assert not any(item.label == "Maximum trades" for item in app.number_input)
    calculate(app)
    assert metrics(app)["Trades"] == "2"
    assert "RMS target gap" in metrics(app)
    by_label(app.toggle, "Prefer fewer trades").set_value(True).run()
    by_label(app.number_input, "Maximum trades").set_value(2).run()
    by_label(app.number_input, "Allowed extra target error (pp)").set_value(100).run()
    calculate(app)
    assert metrics(app)["Trades"] == "1"
    comparison = next(item.value for item in app.dataframe if "RMS target gap (pp)" in item.value)
    assert comparison.Trades.tolist() == [1, 2]
    by_label(app.selectbox, "Plan to inspect").set_value(1).run()
    assert metrics(app)["Trades"] == "2"
    by_label(app.number_input, "Allowed extra target error (pp)").set_value(0).run()
    assert "Trades" not in metrics(app)
    calculate(app)
    assert metrics(app)["Trades"] == "2"
    by_label(app.number_input, "Maximum trades").set_value(1).run()
    calculate(app)
    assert metrics(app)["Trades"] == "1"
    by_label(app.toggle, "Prefer fewer trades").set_value(False).run()
    calculate(app)
    assert metrics(app)["Trades"] == "2"  # Hidden trade cap no longer applies.
    assert (rebalance_data / "holdings.csv").read_bytes() == before


def test_buy_every_selection_explains_no_new_conflict(rebalance_data):
    path = rebalance_data / "holdings.csv"
    with path.open("a") as handle:
        handle.write("d,Synthetic D,UNKNOWN,0,0\n")
    app = launch(rebalance_data)
    by_label(app.selectbox, "Rebalancing mode").set_value(MODES[2]).run()
    by_label(app.toggle, "Limit buys to selected positions").set_value(True).run()
    by_label(app.multiselect, "Positions eligible for buying").set_value(["position-1", "position-3"]).run()
    by_label(app.toggle, "Only buy existing positions").set_value(True).run()
    calculate(app)
    assert any("conflicts with No new positions" in item.value for item in app.error)
    by_label(app.selectbox, "Selection intent").set_value("Allow skipping positions").run()
    calculate(app)
    assert metrics(app)["Trades"] == "1"


def edit_caps(app, changes):
    key = next(key for key in app.session_state.filtered_state if key.startswith('rebalance_caps_'))
    app.session_state[key] = {'edited_rows': {i: {'Max allocation %': value} for i, value in changes.items()},
                              'added_rows': [], 'deleted_rows': []}


def capped_app(rebalance_data):
    app = launch(rebalance_data)
    by_label(app.selectbox, 'Rebalancing mode').set_value(MODES[2]).run()
    by_label(app.number_input, 'New money (EUR)').set_value(100).run()
    by_label(app.toggle, 'Limit buys to selected positions').set_value(True).run()
    by_label(app.multiselect, 'Positions eligible for buying').set_value(['position-0', 'position-1', 'position-2']).run()
    by_label(app.number_input, 'Minimum purchase (EUR)').set_value(5).run()
    by_label(app.toggle, 'Limit allocations for this rebalance').set_value(True).run()
    return app


def test_temporary_caps_redirect_buys_show_cash_and_never_save_targets(rebalance_data):
    before = (rebalance_data / 'holdings.csv').read_bytes()
    app = capped_app(rebalance_data)
    edit_caps(app, {1: 20})
    calculate(app)
    plan = list_frame(app, 'Suggested trades')
    assert plan.set_index('Investment')['Trade'].to_dict() == {'Synthetic A': 10, 'Synthetic B': 30, 'Synthetic C': 60}
    edit_caps(app, {0: 45, 1: 15, 2: 15})
    app.run()
    assert 'Trades' not in metrics(app)
    edit_caps(app, {0: 45, 1: 15, 2: 15})
    calculate(app)
    assert metrics(app)['Unallocated cash'] == '€50.00'
    assert metrics(app)['New money'] == '€100.00'
    assert any('included in the final planning value' in item.value for item in app.info)
    by_label(app.toggle, 'Limit allocations for this rebalance').set_value(False).run()
    assert 'Trades' not in metrics(app)
    calculate(app)
    assert metrics(app)['Unallocated cash'] == '€0.00'
    assert (rebalance_data / 'holdings.csv').read_bytes() == before


def test_cap_conflicts_and_selection_changes_clear_limits(rebalance_data):
    app = capped_app(rebalance_data)
    edit_caps(app, {0: 0})
    calculate(app)
    assert any('less room than the minimum' in item.value for item in app.error)
    by_label(app.selectbox, 'Selection intent').set_value('Allow skipping positions').run()
    edit_caps(app, {0: 0})
    calculate(app)
    plan = list_frame(app, 'Suggested trades')
    assert set(plan.Investment) == {'Synthetic B', 'Synthetic C'}
    by_label(app.multiselect, 'Positions eligible for buying').set_value(['position-0']).run()
    calculate(app)
    plan = list_frame(app, 'Suggested trades')
    assert plan.Investment.tolist() == ['Synthetic A']
    assert plan['Trade'].tolist() == [100]
