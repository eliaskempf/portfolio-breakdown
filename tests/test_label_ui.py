import shutil

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.label_comparison import Label
from test_ui import by_label, theme_view, missing_prices


@pytest.fixture
def label_data(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    (tmp_path / "holdings.csv").write_text(
        "id,name,ticker,shares\na,Synthetic A,NVDA,1\nb,Synthetic B,TSM,2\nc,Synthetic C,ENR.DE,2\n"
    )
    (tmp_path / "classifications.yaml").write_text(
        "a:\n  classifications:\n    labels:\n      - [Group A, Child]\n"
        "b:\n  classifications:\n    labels:\n      - [Group B, Child]\n"
        "c:\n  classifications:\n    labels:\n      - [Group C]\n"
    )
    return tmp_path


def launch(path):
    app = AppTest.from_string(
        "from pathlib import Path\nfrom portfolio_app.ui import render_app\n"
        f"render_app(Path({str(path)!r}), demo=True)\n", default_timeout=15,
    ).run()

    theme_view(app, 'selected_labels')
    return app


def test_explicit_label_comparison_and_selection_recalculate_union(label_data):
    app = launch(label_data)
    assert not app.exception
    assert by_label(app.selectbox, "Group by").value == "selected_labels"
    assert by_label(app.selectbox, "Chart").value == "Sunburst"
    table = app.tabs[1].dataframe[-1].value
    assert table["Value"].sum() == 200
    assert table["Selected labels %"].sum() == 100
    assert table["Portfolio %"].sum() == 100
    assert table["Value"].is_monotonic_decreasing
    by_label(app.multiselect, "Labels to compare").set_value([Label("labels", ("Group A",)).key, Label("labels", ("Group C",)).key]).run()
    assert not app.exception
    table = app.tabs[1].dataframe[-1].value
    assert table["Value"].sum() == 120
    assert table["Selected labels %"].sum() == pytest.approx(100)
    assert table["Portfolio %"].sum() == 60
    assert any("€80.00 falls outside" in item.value for item in app.caption)
    for kind in ("Pie", "Sunburst", "Treemap", "Bar"):
        by_label(app.selectbox, "Chart").set_value(kind).run()
        assert not app.exception
        assert len(app.tabs[1].get("plotly_chart")) == 1
    by_label(app.multiselect, "Labels to compare").set_value([]).run()
    assert not app.exception
    assert len(app.tabs[1].get("plotly_chart")) == 0
    assert any("Choose one or more labels" in item.value for item in app.info)


def test_overlapping_label_choice_is_explicit_and_full_count_uses_bar(label_data):
    app = launch(label_data)
    by_label(app.multiselect, "Labels to compare").set_value([
        Label("labels", ("Group A",)).key, Label("labels", ("Group A", "Child")).key,
    ]).run()
    assert not app.exception
    assert by_label(app.radio, "Assets matching multiple labels").value is None
    assert len(app.tabs[1].get("plotly_chart")) == 0
    by_label(app.radio, "Assets matching multiple labels").set_value("Count in each label").run()
    assert not app.exception
    table = app.tabs[1].dataframe[-1].value
    assert table["Selected labels %"].sum() == 200
    assert by_label(app.selectbox, "Chart").options == ["Bar"]
    by_label(app.radio, "Assets matching multiple labels").set_value("Split equally").run()
    assert not app.exception
    assert app.tabs[1].dataframe[-1].value["Selected labels %"].sum() == 100
    assert "Pie" in by_label(app.selectbox, "Chart").options


def test_label_choices_survive_position_filters_and_no_matches_are_clear(label_data):
    app = launch(label_data)
    a = Label("labels", ("Group A",)).key
    by_label(app.multiselect, "Labels to compare").set_value([a]).run()
    by_label(app.multiselect, "Holdings").set_value(["b"]).run()
    assert not app.exception
    assert by_label(app.multiselect, "Labels to compare").value == [a]
    assert any("No positive valued assets match" in item.value for item in app.info)
    assert app.tabs[1].dataframe[-1].value["Value"].sum() == 0
    by_label(app.multiselect, "Holdings").set_value(["a", "b"]).run()
    assert not app.exception
    assert app.tabs[1].dataframe[-1].value.iloc[0]["Portfolio %"] == 50


def test_detail_navigation_assets_tickers_and_back_to_overview(label_data):
    app = launch(label_data)
    root = (Label("labels", ("Group A",)).key,)
    by_label(app.selectbox, "Detail view").set_value(root).run()
    assert not app.exception
    table = app.tabs[1].dataframe[-1].value
    assert table["Investment"].tolist() == ["Synthetic A"]
    assert table["Value"].tolist() == [80]
    assert table["Allocation %"].tolist() == [100]
    for kind in ("Pie", "Sunburst", "Treemap", "Bar"):
        by_label(app.selectbox, "Chart").set_value(kind).run()
        assert not app.exception
    by_label(app.checkbox, "Show individual assets directly").check().run()
    assert not app.exception
    by_label(app.checkbox, "Show tickers").check().run()
    assert not app.exception
    assert app.tabs[1].dataframe[-1].value["Investment"].tolist() == ["Synthetic A (NVDA)"]
    by_label(app.selectbox, "Detail view").set_value((*root, "Child")).run()
    assert app.tabs[1].dataframe[-1].value["Value"].sum() == 80
    by_label(app.button, "Back to overview").click().run()
    assert not app.exception
    assert by_label(app.selectbox, "Detail view").value == ()
    assert app.tabs[1].dataframe[-1].value["Value"].sum() == 200


def test_detail_resets_when_selected_labels_or_filters_remove_branch(label_data):
    app = launch(label_data)
    a, b = Label("labels", ("Group A",)).key, Label("labels", ("Group B",)).key
    by_label(app.selectbox, "Detail view").set_value((a,)).run()
    by_label(app.multiselect, "Labels to compare").set_value([b]).run()
    assert not app.exception
    assert by_label(app.selectbox, "Detail view").value == ()
    by_label(app.selectbox, "Detail view").set_value((b,)).run()
    by_label(app.multiselect, "Holdings").set_value(["c"]).run()
    assert not app.exception
    assert len(app.tabs[1].get("plotly_chart")) == 0


def test_derived_targets_zero_positions_filters_and_incomplete_targets(label_data):
    (label_data / "holdings.csv").write_text(
        "id,name,ticker,shares,target_allocation\na,Synthetic A,NVDA,1,0.2\nb,Synthetic B,,0,0.3\nc,Synthetic C,ENR.DE,2,\n"
    )
    app = launch(label_data)
    assert not app.exception
    table = app.tabs[1].dataframe[-1].value
    assert sorted(table["Known target portfolio %"].tolist()) == [0, 20, 30]
    assert table["Target portfolio %"].isna().sum() == 1
    assert missing_prices(app) == "0"
    b = Label("labels", ("Group B",)).key
    by_label(app.selectbox, "Detail view").set_value((b,)).run()
    assert not app.exception
    detail = app.tabs[1].dataframe[-1].value
    assert detail["Target portfolio %"].tolist() == [30]
    assert detail["Gap (pp)"].tolist() == [-30]
    by_label(app.button, "Back to overview").click().run()
    by_label(app.multiselect, "Holdings").set_value(["a"]).run()
    assert not app.exception
    table = app.tabs[1].dataframe[-1].value
    assert table["Target portfolio %"].sum() == 20
    assert table["Current portfolio %"].sum() == pytest.approx(100 * 80 / 120)
    # Advanced taxonomy tables also derive parent/child targets.
    by_label(app.selectbox, "Group by").set_value("taxonomy:labels").run()
    assert not app.exception
    assert set(app.tabs[1].dataframe[-1].value["Target portfolio %"]) == {20}


@pytest.mark.parametrize("shares", [0, 1])
def test_targets_remain_visible_for_all_zero_or_unpriced_portfolios(label_data, shares):
    (label_data / "holdings.csv").write_text(
        f"id,name,ticker,shares,target_allocation\na,Synthetic A,,{shares},0.2\nb,Synthetic B,,0,0.3\n"
    )
    app = launch(label_data)
    assert not app.exception
    assert app.tabs[1].dataframe[-1].value["Target portfolio %"].sum() == 50
    assert app.tabs[1].dataframe[-1].value["Gap (pp)"].isna().all()
    assert len(app.tabs[1].get("plotly_chart")) == 0
    if shares == 0:
        by_label(app.selectbox, "Group by").set_value("holding").run()
        assert not app.exception
        assert app.tabs[1].dataframe[-1].value["Target portfolio %"].sum() == 50
        assert len(app.tabs[1].get("plotly_chart")) == 0
