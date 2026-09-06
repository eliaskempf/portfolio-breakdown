from pathlib import Path
import shutil
import sys

import pytest
from streamlit.testing.v1 import AppTest

def launch(data_dir):
    return AppTest.from_string(
        "from pathlib import Path\n"
        "from portfolio_app.ui import render_app\n"
        f"render_app(Path({str(data_dir)!r}), demo=True)\n",
        default_timeout=15,
    ).run()


def by_label(elements, label):
    return next(element for element in elements if element.label == label)


@pytest.fixture
def grouped_data(tmp_path, sample_data_dir):
    import json
    import yaml

    directory = tmp_path / "synthetic-grouping"
    shutil.copytree(sample_data_dir, directory)
    with (directory / "holdings.csv").open("a") as file:
        file.write("fund,Synthetic Fund,VVSM.DE,IE00BMC38736,2,,Core,Demo account A\n")
    prices = json.loads((directory / "demo_prices.json").read_text())
    prices["prices"]["VVSM.DE"] = {"price": 100, "currency": "EUR", "observed_at": "2026-09-04T20:00:00+00:00"}
    (directory / "demo_prices.json").write_text(json.dumps(prices))
    classes = yaml.safe_load((directory / "classifications.yaml").read_text())
    classes["fund"] = {"classifications": {"labels": [["Group A", "Funds"]]}}
    for asset in ("nvda", "tsmc"):
        classes[asset]["classifications"]["labels"] = [["Group A", "Stocks"]]
    (directory / "classifications.yaml").write_text(yaml.safe_dump(classes))
    return directory


def test_optional_smh_grouping_stock_choice_lookthrough_and_filters(grouped_data):
    before = {name: (grouped_data / name).read_bytes() for name in ("holdings.csv", "classifications.yaml")}
    app = launch(grouped_data)
    assert not app.exception
    assert by_label(app.checkbox, "Group SMH with related stocks").value is False
    by_label(app.selectbox, "Group by").set_value("holding").run()
    by_label(app.checkbox, "Group SMH with related stocks").check().run()
    assert not app.exception
    assert set(by_label(app.multiselect, "Stocks in the SMH group").value) == {"nvda", "tsmc"}
    for representation in ("Instruments", "ETF look-through"):
        by_label(app.radio, "Portfolio representation").set_value(representation).run()
        assert not app.exception
        table = app.dataframe[0].value
        assert table["EUR value"].sum() == 944
        assert table.loc[table.Category == "SMH + related stocks", "EUR value"].tolist() == [560]
        assert table["Allocation %"].sum() == pytest.approx(100)
    effective = next(item.value for item in app.dataframe if "ETF-derived (EUR)" in item.value)
    assert effective.loc[effective.Asset == "Nvidia", "Total (EUR)"].tolist() == [256]
    by_label(app.checkbox, "Show tickers").check().run()
    assert app.dataframe[0].value.Category.str.contains("view-group").sum() == 0
    by_label(app.multiselect, "Stocks in the SMH group").set_value(["nvda"]).run()
    assert not app.exception
    table = app.dataframe[0].value
    assert table.loc[table.Category == "SMH + related stocks", "EUR value"].tolist() == [440]
    by_label(app.multiselect, "Holdings").set_value(["nvda"]).run()
    assert not app.exception
    assert app.dataframe[0].value["EUR value"].tolist() == [240]
    by_label(app.checkbox, "Group SMH with related stocks").uncheck().run()
    assert not app.exception
    assert app.dataframe[0].value.Category.tolist() == ["Nvidia (NVDA)"]
    assert all((grouped_data / name).read_bytes() == content for name, content in before.items())


def test_smh_group_uses_fund_labels_and_shows_original_members(grouped_data):
    from portfolio_app.label_comparison import Label

    app = launch(grouped_data)
    by_label(app.checkbox, "Group SMH with related stocks").check().run()
    by_label(app.radio, "Portfolio representation").set_value("ETF look-through").run()
    assert not app.exception
    assert app.dataframe[0].value["EUR value"].sum() == 560
    root = (Label("labels", ("Group A",)).key,)
    by_label(app.selectbox, "Detail view").set_value(root).run()
    assert not app.exception
    assert app.dataframe[0].value.Investment.tolist() == ["SMH + related stocks"]
    detail = next(item.value for item in app.dataframe if "Within group (%)" in item.value)
    assert detail["EUR value"].sum() == 560
    assert set(detail.Investment) == {"Synthetic Fund", "Nvidia", "TSMC"}
    by_label(app.multiselect, "Stocks in the SMH group").set_value([]).run()
    assert not app.exception
    assert app.dataframe[0].value["EUR value"].sum() == 560  # Fund and stocks separate within Group A.


def test_demo_launch_and_subset_selection(sample_data_dir):
    app = launch(sample_data_dir)
    assert not app.exception
    assert app.metric[0].value == "€744.00"
    assert app.metric[2].value == "1"
    by_label(app.multiselect, "Portfolio").set_value(["AI Sleeve"]).run()
    assert not app.exception
    assert app.metric[1].value == "€520.00"
    assert app.metric[2].value == "0"
    holdings = app.dataframe[-1].value
    assert holdings["portfolio_weight"].sum() == pytest.approx(100)


def test_allocations_and_holdings_default_to_decreasing_weight(sample_data_dir):
    app = launch(sample_data_dir)
    assert not app.exception
    assert by_label(app.checkbox, "Show tickers").value is False
    assert app.dataframe[0].value["Allocation %"].is_monotonic_decreasing
    assert app.dataframe[-1].value["portfolio_weight"].dropna().is_monotonic_decreasing


def test_ticker_display_toggle_preserves_selected_instrument_ids(sample_data_dir):
    app = launch(sample_data_dir)
    by_label(app.multiselect, "Holdings").set_value(["nvda", "tsmc"]).run()
    initial = app.metric[1].value
    for enabled in (True, False, True, False):
        by_label(app.checkbox, "Show tickers").set_value(enabled).run()
        assert not app.exception
        assert by_label(app.multiselect, "Holdings").value == ["nvda", "tsmc"]
        assert app.metric[1].value == initial
    by_label(app.multiselect, "Portfolio").set_value(["AI Sleeve"]).run()
    assert app.dataframe[0].value["Allocation %"].is_monotonic_decreasing
    assert app.dataframe[-1].value["portfolio_weight"].dropna().is_monotonic_decreasing


def test_names_are_cleaned_for_display_without_changing_saved_holdings(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    path = tmp_path / "holdings.csv"
    source = "id,name,shares,ticker\nsynthetic,SYNTHETIC SYSTEMS INC,2,NVDA\n"
    path.write_text(source)
    app = launch(tmp_path)
    assert not app.exception
    assert app.dataframe[-1].value.iloc[0]["name"] == "Synthetic Systems"
    assert app.dataframe[0].value.iloc[0]["Category"] == "Synthetic Systems"
    by_label(app.checkbox, "Show tickers").check().run()
    assert app.dataframe[0].value.iloc[0]["Category"] == "Synthetic Systems (NVDA)"
    by_label(app.radio, "Position action").set_value("Edit position").run()
    assert by_label(app.text_input, "Instrument name").value == "SYNTHETIC SYSTEMS INC"
    assert path.read_text() == source


def test_hierarchy_controls_charts_and_branch_filters(sample_data_dir):
    app = launch(sample_data_dir)
    by_label(app.selectbox, "Group by").set_value("taxonomy:ai").run()
    assert not app.exception
    by_label(app.selectbox, "Hierarchy root").set_value(("AI", "AI Infrastructure")).run()
    assert not app.exception
    by_label(app.selectbox, "View depth").set_value(1).run()
    assert not app.exception
    table = app.dataframe[0].value
    assert table["EUR value"].sum() == 384
    assert set(table["Category"].str.strip()) == {"Energy", "Networking"}
    assert "Classification path" not in table
    by_label(app.checkbox, "Show classification paths").check().run()
    assert "Classification path" in app.dataframe[0].value
    by_label(app.checkbox, "Show holdings beneath labels").check().run()
    for kind in ("Sunburst", "Bar", "Treemap", "Pie"):
        by_label(app.selectbox, "Chart").set_value(kind).run()
        assert not app.exception
        assert len(app.get("plotly_chart")) == 1
    by_label(app.multiselect, "ai branches").set_value([("AI", "AI Infrastructure", "Energy")]).run()
    assert not app.exception
    assert app.metric[1].value == "€224.00"
    assert set(app.dataframe[-1].value["id"]) == {"enr", "vst"}
    # A changed taxonomy must not retain an invalid root from the previous view.
    by_label(app.selectbox, "Group by").set_value("taxonomy:sector").run()
    assert not app.exception


def test_empty_subset_and_all_missing_are_usable(sample_data_dir):
    app = launch(sample_data_dir)
    by_label(app.multiselect, "Holdings").set_value([]).run()
    assert not app.exception
    assert any("No holdings match" in item.value for item in app.info)
    by_label(app.multiselect, "Holdings").set_value(["unpriced"]).run()
    assert not app.exception
    assert app.metric[1].value == "€0.00"
    assert app.metric[2].value == "1"
    assert len(app.get("plotly_chart")) == 0
    assert len(app.dataframe[-1].value) == 1


def test_invalid_data_is_actionable(tmp_path):
    (tmp_path / "holdings.csv").write_text("id,name,shares\na,Asset,-5\n")
    app = launch(tmp_path)
    assert not app.exception
    assert "nonnegative" in app.error[0].value


def test_empty_holdings(tmp_path):
    (tmp_path / "holdings.csv").write_text("id,name,shares\n")
    (tmp_path / "classifications.yaml").write_text("")
    app = launch(tmp_path)
    assert not app.exception
    assert "Add a position" in app.info[0].value


def test_provider_failures_do_not_crash_app(sample_data_dir):
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from portfolio_app.ui import render_app\n"
        "from portfolio_app.prices import PriceService\n"
        "class OfflineProvider:\n"
        "    def price(self, ticker):\n"
        "        raise ConnectionError('test provider offline')\n"
        "    def fx(self, currency):\n"
        "        raise ConnectionError('test provider offline')\n"
        f"render_app(Path({str(sample_data_dir)!r}), price_service=PriceService(OfflineProvider()))\n",
        default_timeout=15,
    ).run()
    assert not app.exception
    assert app.metric[2].value == "7"
    assert len(app.dataframe[-1].value) == 7


def test_zero_positions_and_missing_classifications(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    (tmp_path / "holdings.csv").write_text("id,name,shares,ticker\na,Asset,0,NVDA\n")
    (tmp_path / "classifications.yaml").write_text("")
    app = launch(tmp_path)
    assert not app.exception
    assert app.metric[1].value == "€0.00"
    assert len(app.get("plotly_chart")) == 0


def test_actual_streamlit_entrypoint(monkeypatch, sample_data_dir):
    import portfolio_app.ui

    script = Path(portfolio_app.ui.__file__)
    monkeypatch.setattr(sys, "argv", [str(script), "--data-dir", str(sample_data_dir), "--demo"])
    app = AppTest.from_file(script, default_timeout=15).run()
    assert not app.exception
    assert app.metric[0].value == "€744.00"


def test_target_column_is_optional_and_not_a_filter(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    (tmp_path / "holdings.csv").write_text(
        "id,name,shares,ticker,target_allocation,portfolio\n"
        "a,Asset,1,nvda,15%,AI\nb,Other,2,tsm,,Core\n"
    )
    (tmp_path / "classifications.yaml").write_text("")
    app = launch(tmp_path)
    assert not app.exception
    assert all(item.label != "Target Allocation" for item in app.multiselect)
    table = app.dataframe[-1].value
    assert table["ticker"].tolist() == ["NVDA", "TSM"]
    assert table["target_allocation"].iloc[0] == 15
    assert table["target_allocation"].isna().sum() == 1
    by_label(app.multiselect, "Portfolio").set_value(["AI"]).run()
    assert app.dataframe[-1].value["target_allocation"].iloc[0] == 15
    by_label(app.multiselect, "Portfolio").set_value(["Core"]).run()
    assert "target_allocation" not in app.dataframe[-1].value


def test_etf_breakdown_and_look_through(tmp_path, sample_data_dir):
    import json
    from portfolio_app.etf import load_funds

    shutil.copytree(sample_data_dir / "etfs", tmp_path / "etfs")
    constituents = load_funds(tmp_path / "etfs")[0].constituents
    nvidia_indirect = 1000 * constituents.loc[constituents["ticker"] == "NVDA", "weight"].iloc[0]
    shutil.copy(sample_data_dir / "classifications.yaml", tmp_path)
    prices = json.loads((sample_data_dir / "demo_prices.json").read_text())
    prices["prices"]["VVSM.DE"] = {"price": 100, "currency": "EUR", "observed_at": "2026-09-04T15:30:00+00:00"}
    (tmp_path / "demo_prices.json").write_text(json.dumps(prices))
    (tmp_path / "holdings.csv").write_text(
        "id,name,shares,ticker,isin,portfolio,account\n"
        "nvda,Nvidia,10,nvda,US67066G1040,AI,Main\n"
        "semis,Semiconductor UCITS,10,vvsm.de,IE00BMC38736,AI,Main\n"
    )
    app = launch(tmp_path)
    assert not app.exception
    assert app.metric[0].value == "€1,800.00"
    assert any("ETF breakdown" in item.label for item in app.expander)
    by_label(app.radio, "Portfolio representation").set_value("ETF look-through").run()
    assert not app.exception
    effective = next(table.value for table in app.dataframe if "ETF-derived (EUR)" in table.value.columns)
    nvidia = effective.loc[effective["Ticker"] == "NVDA"].iloc[0]
    assert nvidia["Direct (EUR)"] == 800
    assert nvidia["ETF-derived (EUR)"] == pytest.approx(nvidia_indirect)
    assert effective["Total (EUR)"].sum() == pytest.approx(1800)
    assert app.metric[1].value == "€1,800.00"
    by_label(app.selectbox, "Group by").set_value("taxonomy:ai").run()
    by_label(app.selectbox, "Hierarchy root").set_value(("AI", "Compute", "GPUs")).run()
    assert not app.exception
    assert app.dataframe[0].value.empty  # The selected leaf is shown as the separate total.
    by_label(app.checkbox, "Show holdings beneath labels").check().run()
    assert app.dataframe[0].value.iloc[0]["EUR value"] == pytest.approx(800 + nvidia_indirect)
    by_label(app.selectbox, "Chart").set_value("Pie").run()
    assert not app.exception
    by_label(app.radio, "Portfolio representation").set_value("Instruments").run()
    assert not app.exception
    assert not any("ETF-derived (EUR)" in table.value.columns for table in app.dataframe)


def test_plain_smh_with_ucits_isin_is_actionable(tmp_path, sample_data_dir):
    shutil.copytree(sample_data_dir / "etfs", tmp_path / "etfs")
    (tmp_path / "holdings.csv").write_text("id,name,shares,ticker,isin\nsmh,UCITS,1,SMH,IE00BMC38736\n")
    (tmp_path / "classifications.yaml").write_text("")
    app = launch(tmp_path)
    assert not app.exception
    assert "SMH.L" in app.error[0].value


def test_show_tickers_preserves_merged_hierarchy_leaf_across_exchanges(tmp_path, sample_data_dir):
    import json
    from portfolio_app.etf import load_funds

    shutil.copytree(sample_data_dir / "etfs", tmp_path / "etfs")
    weight = load_funds(tmp_path / "etfs")[0].constituents.set_index("ticker").loc["NVDA", "weight"]
    prices = json.loads((sample_data_dir / "demo_prices.json").read_text())
    prices["prices"]["NVD.DE"] = prices["prices"]["NVDA"]
    prices["prices"]["VVSM.DE"] = {"price": 100, "currency": "EUR", "observed_at": "2026-09-04T15:30:00+00:00"}
    (tmp_path / "demo_prices.json").write_text(json.dumps(prices))
    (tmp_path / "holdings.csv").write_text(
        "id,name,shares,ticker,isin\n"
        "supplier,Synthetic Supplier,1,NVD.DE,US67066G1040\n"
        "fund,Synthetic Fund Position,10,VVSM.DE,IE00BMC38736\n"
    )
    (tmp_path / "classifications.yaml").write_text("supplier:\n  classifications:\n    test:\n      - [Synthetic, Branch]\n")
    app = launch(tmp_path)
    by_label(app.radio, "Portfolio representation").set_value("ETF look-through").run()
    by_label(app.selectbox, "Group by").set_value("taxonomy:test").run()
    by_label(app.checkbox, "Show holdings beneath labels").check().run()
    by_label(app.checkbox, "Show tickers").check().run()
    assert not app.exception
    table = app.dataframe[0].value
    leaf = table.loc[table.Category.str.strip() == "Synthetic Supplier (NVD.DE)"]
    assert len(leaf) == 1
    assert leaf.iloc[0]["EUR value"] == pytest.approx(80 + 1000 * weight)


def test_stale_snapshot_warning_and_update_failure(monkeypatch, sample_data_dir):
    import portfolio_app.etf_ui as etf_ui

    monkeypatch.setattr(etf_ui, "snapshot_age_days", lambda fund: 10)

    def failed_update(fund):
        raise ConnectionError("provider offline")

    monkeypatch.setattr(etf_ui, "refresh_snapshot", failed_update)
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from portfolio_app.etf import load_funds\n"
        "from portfolio_app.etf_ui import render_snapshot_controls\n"
        f"render_snapshot_controls(load_funds(Path({str(sample_data_dir / 'etfs')!r})))\n"
    ).run()
    assert not app.exception
    assert any("10 days old" in warning.value for warning in app.warning)
    by_label(app.button, "Update from VanEck").click().run()
    assert not app.exception
    assert any("keeping the snapshot" in warning.value for warning in app.warning)


def test_snapshot_update_button_success(monkeypatch, sample_data_dir):
    import portfolio_app.etf_ui as etf_ui

    monkeypatch.setattr(etf_ui, "refresh_snapshot", lambda fund: fund)
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from portfolio_app.etf import load_funds\n"
        "from portfolio_app.etf_ui import render_snapshot_controls\n"
        f"render_snapshot_controls(load_funds(Path({str(sample_data_dir / 'etfs')!r})))\n"
    ).run()
    by_label(app.button, "Update from VanEck").click().run()
    assert not app.exception
    assert "holdings checked" in app.success[0].value


def test_create_position_from_empty_app_and_reopen(tmp_path, sample_data_dir):
    from portfolio_app.holdings import load_holdings

    # No holdings or classifications file is needed to create the first position.
    app = launch(tmp_path)
    assert not app.exception
    by_label(app.text_input, "Instrument name").set_value("New asset")
    by_label(app.number_input, "Shares held (total)").set_value(1.23456789)
    by_label(app.text_input, "Portfolio / sleeve").set_value("AI")
    by_label(app.text_input, "Account / broker").set_value("Broker")
    by_label(app.number_input, "Average buy-in per share (optional)").set_value(42.50)
    by_label(app.number_input, "Target allocation % (optional)").set_value(10.0)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert any("Saved New asset" in item.value for item in app.success)
    frame = load_holdings(tmp_path / "holdings.csv")
    assert len(frame) == 1
    assert frame.iloc[0]["shares"] == pytest.approx(1.23456789)
    assert frame.iloc[0]["acquisition_currency"] == "EUR"
    assert frame.iloc[0]["target_allocation"] == 0.1
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    reopened = launch(tmp_path)
    assert not reopened.exception
    assert reopened.dataframe[-1].value.iloc[0]["name"] == "New asset"
    assert reopened.dataframe[-1].value.iloc[0]["acquisition_price"] == 42.5


def test_edit_position_updates_total_shares_buy_in_and_currency(tmp_path, sample_data_dir):
    from portfolio_app.holdings import load_holdings

    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    (tmp_path / "holdings.csv").write_text(
        "id,name,ticker,shares,portfolio,account,acquisition_price,acquisition_currency\n"
        "nvda,Nvidia,NVDA,10,AI,Broker,100,EUR\n"
    )
    app = launch(tmp_path)
    by_label(app.radio, "Position action").set_value("Edit position").run()
    by_label(app.number_input, "Shares held (total)").set_value(15.0)
    by_label(app.number_input, "Average buy-in per share (optional)").set_value(106.666667)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    frame = load_holdings(tmp_path / "holdings.csv")
    assert len(frame) == 1
    assert frame.iloc[0]["shares"] == 15
    assert frame.iloc[0]["acquisition_price"] == pytest.approx(106.666667)
    assert app.metric[1].value == "€1,200.00"
    assert (tmp_path / ".backups").exists()


def test_create_position_validation_error_does_not_save(tmp_path):
    app = launch(tmp_path)
    by_label(app.number_input, "Shares held (total)").set_value(1.0)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert any("nonempty name" in item.value for item in app.error)
    assert not (tmp_path / "holdings.csv").exists()


def test_edit_stale_file_prompts_reload_without_overwrite(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,ticker,shares\nnvda,Nvidia,NVDA,10\n")
    app = launch(tmp_path)
    by_label(app.radio, "Position action").set_value("Edit position").run()
    changed = "id,name,ticker,shares\nnvda,Nvidia,NVDA,20\n"
    path.write_text(changed)
    by_label(app.number_input, "Shares held (total)").set_value(15.0)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert any("Holdings changed" in item.value for item in app.warning)
    assert path.read_text() == changed
    by_label(app.button, "Reload position form").click().run()
    assert not app.exception


def test_new_ucits_position_rejects_bare_smh_before_save(tmp_path, sample_data_dir):
    shutil.copytree(sample_data_dir / "etfs", tmp_path / "etfs")
    app = launch(tmp_path)
    by_label(app.text_input, "Instrument name").set_value("VanEck UCITS")
    by_label(app.text_input, "Ticker").set_value("smh")
    by_label(app.text_input, "ISIN (optional)").set_value("IE00BMC38736")
    by_label(app.number_input, "Shares held (total)").set_value(1.0)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert any("SMH.L" in item.value for item in app.error)
    assert not (tmp_path / "holdings.csv").exists()


def test_new_position_is_visible_after_filters_were_cleared(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    (tmp_path / "holdings.csv").write_text("id,name,ticker,shares,portfolio\nnvda,Nvidia,NVDA,10,AI\n")
    app = launch(tmp_path)
    by_label(app.multiselect, "Holdings").set_value([]).run()
    by_label(app.text_input, "Instrument name").set_value("Arista")
    by_label(app.text_input, "Ticker").set_value("anet")
    by_label(app.number_input, "Shares held (total)").set_value(1.0)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert len(app.dataframe[-1].value) == 2
    assert "ANET" in app.dataframe[-1].value["ticker"].tolist()


def test_new_buy_in_requires_currency_but_legacy_amount_is_not_guessed(tmp_path, sample_data_dir):
    from portfolio_app.holdings import load_holdings

    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,ticker,shares,acquisition_price\nnvda,Nvidia,NVDA,10,100\n")
    app = launch(tmp_path)
    by_label(app.radio, "Position action").set_value("Edit position").run()
    assert by_label(app.text_input, "Buy-in currency").value == ""
    by_label(app.number_input, "Average buy-in per share (optional)").set_value(110.)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert any("Enter the currency" in item.value for item in app.error)
    assert load_holdings(path)["acquisition_price"].iloc[0] == 100
    by_label(app.number_input, "Average buy-in per share (optional)").set_value(100.)
    by_label(app.number_input, "Shares held (total)").set_value(11.)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert load_holdings(path)["shares"].iloc[0] == 11
    assert load_holdings(path)["acquisition_currency"].iloc[0] == ""
