"""Invented grouped performance with independent expected costs and gains."""

import pandas as pd
import pytest

from portfolio_app.etf import load_funds
from portfolio_app.grouping import InstrumentGroup, group_classifications
from portfolio_app.holdings import parse_holdings
from portfolio_app.label_comparison import Label, compare_labels
from portfolio_app.performance import position_performance
from portfolio_app.performance_allocation import performance_exposures, add_performance_column


def source():
    holdings = parse_holdings("id,name,ticker,shares,acquisition_price,acquisition_currency\n"
                              "a,Synthetic A,DEMO_A,2,30,EUR\nb,Synthetic B,DEMO_B,4,25,EUR\n"
                              "c,Synthetic C,DEMO_C,1,10,EUR\n")
    return position_performance(holdings.assign(current_price=[40., 20., 20.], quote_currency="EUR", current_value_reporting=[80., 80., 20.]))


CLASSES = {"a": {"labels": (("Group A", "Child"), ("Group A", "Second"), ("Group B",))},
           "b": {"labels": (("Group A", "Child"),)}, "c": {"labels": (("Group B",),)}}
LABELS = [Label("labels", ("Group A",)), Label("labels", ("Group B",))]


def compare(measures, classes=CLASSES, labels=LABELS, policy="split", percent=True):
    tables = [compare_labels(measure, classes, labels, overlap=policy).table for measure in measures.measures()]
    return add_performance_column(pd.DataFrame({"Label": [label.title for label in labels]}), [label.title for label in labels],
                                  tables, key="Label", value="Value", percent=percent)


def test_label_returns_use_matching_cost_and_overlap_splits():
    # A splits between two selected labels, once per label despite two child paths.
    measures = performance_exposures(source(), [])
    table = compare(measures)
    assert table.Performance.tolist() == pytest.approx([-10/130*100, 20/40*100])
    assert table["Performance coverage"].tolist() == ["Complete", "Complete"]
    assert compare(measures, percent=False).Performance.tolist() == [ -10, 20 ]
    assert compare(measures, policy="overlap").Performance.tolist() == pytest.approx([0, 30/70*100])


def test_incomplete_costs_foreign_currency_and_unpriced_rows_mark_partial():
    frame = source()
    frame.loc[1, "unrealized_gain_reporting"] = float("nan")
    frame.loc[1, "current_value_reporting"] = float("nan")
    table = compare(performance_exposures(frame, []))
    assert table.Performance.iloc[0] == pytest.approx(100/3)
    assert table["Performance coverage"].iloc[0] == "Partial"
    frame.loc[0, "unrealized_gain_reporting"] = float("nan")
    table = compare(performance_exposures(frame, []))
    assert pd.isna(table.Performance.iloc[0])
    assert table["Performance coverage"].iloc[0] == "Unavailable"


def test_zero_share_targets_do_not_reduce_performance_coverage():
    frame = source()
    frame.loc[1, ["shares", "current_value_reporting"]] = 0
    frame.loc[1, "unrealized_gain_reporting"] = float("nan")
    table = compare(performance_exposures(frame, []))
    assert table["Performance coverage"].iloc[0] == "Complete"
    assert table.Performance.iloc[0] == pytest.approx(100/3)


def test_etf_constituent_performance_unknown_but_grouped_whole_fund_retained(sample_data_dir):
    funds = load_funds(sample_data_dir / "etfs")
    frame = parse_holdings("id,name,ticker,shares,acquisition_price,acquisition_currency\n"
                            "fund,Synthetic Fund,VVSM.DE,1,100,EUR\n"
                            "nvda,Synthetic Stock,NVDA,1,40,EUR\n")
    frame = position_performance(frame.assign(current_price=[120., 60.], quote_currency="EUR", current_value_reporting=[120., 60.]))
    classes = {asset: {"labels": (("Group A",),)} for asset in ("fund", "nvda", "tsmc", "etf-other:smh_ucits")}
    labels = LABELS[:1]
    table = compare(performance_exposures(frame, funds), classes, labels)
    assert table.Performance.iloc[0] == pytest.approx(40/140*100)
    looked = compare(performance_exposures(frame, funds, lookthrough=True), classes, labels)
    assert looked.Performance.iloc[0] == 50  # Direct stock only; no invented constituent costs.
    assert looked["Performance coverage"].iloc[0] == "Partial"
    group = InstrumentGroup("synthetic-group", "Synthetic group", frozenset({"fund", "nvda"}), frozenset({"fund"}))
    grouped = compare(performance_exposures(frame, funds, lookthrough=True, group=group), group_classifications(classes, group), labels)
    assert grouped.Performance.iloc[0] == pytest.approx(40/140*100)
    assert grouped["Performance coverage"].iloc[0] == "Complete"


def test_child_paths_split_both_cost_and_current_and_merge_accounts():
    frame = source()
    frame = pd.concat([frame, frame.iloc[[0]].assign(position_id="synthetic-extra-account")], ignore_index=True)
    measures = performance_exposures(frame, [])
    compared = [compare_labels(measure, CLASSES, LABELS, overlap="split") for measure in measures.measures()]
    child = (LABELS[0].key, "Child")
    tables = [item.allocations.loc[item.allocations.path == child] for item in compared]
    result = add_performance_column(pd.DataFrame({"id": ["a", "b"]}), ["a", "b"], tables, key="asset_id")
    assert result.Performance.tolist() == pytest.approx([100/3, -20])
