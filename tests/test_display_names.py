import pandas as pd
import pytest

from portfolio_app.aggregation import aggregate_dimension
from portfolio_app.charts import hierarchy_table
from portfolio_app.display_names import display_name
from portfolio_app.instruments import Instrument, result_groups


@pytest.mark.parametrize("raw,expected", [
    ("  NVIDIA   CORPORATION ", "Nvidia"),
    ("ARISTA NETWORKS INC", "Arista Networks"),
    ("SYNTHETIC ROBOTICS CO LTD", "Synthetic Robotics"),
    ("SYNTHETIC SYSTEMS, INC. - CLASS A", "Synthetic Systems - Class A"),
    ("SYNTHETIC SYSTEMS LTD ADR", "Synthetic Systems ADR"),
    ("VANECK SEMICONDUCTOR UCITS ETF USD ACC", "VanEck Semiconductor UCITS ETF USD Acc"),
    ("ASML", "ASML"), ("TSMC ADR", "TSMC ADR"), ("AT&T INC.", "AT&T"),
    ("iShares Core MSCI World UCITS ETF", "iShares Core MSCI World UCITS ETF"),
    ("My custom investment", "My custom investment"),
    ("SYNTHETIC COMPANY RESEARCH", "Synthetic Company Research"),
    ("SYNTHETIC UCITS ETF EUR HEDGED DIST", "Synthetic UCITS ETF EUR Hedged Dist"),
    ("", ""),
    ("Taiwan Semiconductor Manufacturing Company Limited", "TSMC"),
    ("Taiwan Semiconductor Manufacturing Co L", "TSMC"),
    ("Taiwan Semiconductor Manufacturing Company ADR", "TSMC ADR"),
    ("Taiwan Synthetic Manufacturing", "Taiwan Synthetic Manufacturing"),
    ("Amazon.com, Inc.", "Amazon"),
    ("Amazon.com Inc Class A", "Amazon Class A"),
    ("Advanced Micro Devices Inc", "AMD"),
    ("Asml Holding Nv", "ASML"),
    ("Kla Corp", "KLA"),
    ("ON Semiconductor Corp", "onsemi"),
    ("STMicroelectronics NV", "STMicroelectronics"),
])
def test_readable_names_preserve_instrument_qualifiers(raw, expected):
    assert display_name(raw) == expected
    assert display_name(expected) == expected


def test_display_names_do_not_merge_distinct_instrument_identities():
    instruments = [Instrument("SYN-A", "SYNTHETIC INC"), Instrument("SYN-B", "SYNTHETIC CORP")]
    groups = result_groups(instruments)
    assert len(groups) == 2
    assert [group["name"] for group in groups] == ["Synthetic", "Synthetic"]
    assert instruments[0].name == "SYNTHETIC INC"
    exposures = pd.DataFrame([
        {"asset_id": item.ticker, "ticker": item.ticker, "asset_name": display_name(item.name), "value": 10.}
        for item in instruments
    ])
    nodes = aggregate_dimension(exposures, "holding")
    assert len(nodes) == 3
    assert len(set(nodes.node_id)) == 3
    assert nodes.iloc[0].value == 20
    assert nodes.loc[nodes.depth == 1, "label"].tolist() == ["Synthetic", "Synthetic"]
    detailed = aggregate_dimension(exposures, "holding", show_tickers=True)
    assert detailed.node_id.tolist() == nodes.node_id.tolist()
    assert detailed.loc[detailed.depth == 1, "label"].tolist() == ["Synthetic (SYN-A)", "Synthetic (SYN-B)"]


def test_allocation_total_never_participates_in_table_sorting():
    exposures = pd.DataFrame([
        {"asset_id": "a", "asset_name": "Synthetic Alpha", "ticker": "SYN-A", "value": 10.},
        {"asset_id": "b", "asset_name": "Synthetic Beta", "ticker": "SYN-B", "value": 20.},
    ])
    nodes = aggregate_dimension(exposures, "holding")
    table = hierarchy_table(nodes)
    assert len(table) == 2
    assert "Path" not in table and "Classification path" not in table
    assert table["EUR value"].sum() == nodes.iloc[0].value
    for column in table:
        for ascending in (True, False):
            assert "holding" not in table.sort_values(column, ascending=ascending)["Category"].tolist()
    assert "Classification path" in hierarchy_table(nodes, show_paths=True)
