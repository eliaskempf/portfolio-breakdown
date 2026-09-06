import pandas as pd
import pytest

from portfolio_app.aggregation import aggregate, aggregate_dimension, classify_exposures
from portfolio_app.charts import bar_chart, hierarchy_chart, pie_chart, sort_allocation_nodes
from portfolio_app.exposures import normalize_exposures
from portfolio_app.filtering import filter_holdings
from portfolio_app.valuation import portfolio_weights


@pytest.fixture
def exposures():
    return pd.DataFrame([
        {"asset_id": "a", "asset_name": "A", "value": 120.0},
        {"asset_id": "b", "asset_name": "B", "value": 80.0},
        {"asset_id": "c", "asset_name": "C", "value": 50.0},
        {"asset_id": "d", "asset_name": "D", "value": 10.0},
    ])


@pytest.fixture
def taxonomy():
    return {
        "a": {"ai": (("AI", "Compute", "Shared"), ("AI", "Infrastructure", "Shared"))},
        "b": {"ai": (("AI", "Infrastructure", "Energy", "Grid"),)},
        "c": {"ai": (("AI", "Infrastructure"),)},
    }


def assert_conserved(nodes, expected):
    assert nodes.iloc[0]["value"] == pytest.approx(expected)
    assert nodes.loc[nodes["is_leaf"], "value"].sum() == pytest.approx(expected)
    assert nodes.loc[nodes["is_leaf"], "percentage"].sum() == pytest.approx(1)
    assert nodes["node_id"].is_unique
    for node in nodes.loc[~nodes["is_leaf"]].itertuples():
        children = nodes.loc[nodes["parent_id"] == node.node_id]
        assert children["value"].sum() == pytest.approx(node.value)


@pytest.mark.parametrize("depth", [None, 0, 1, 2, 3, 4, 9])
@pytest.mark.parametrize("include_holdings", [False, True])
def test_all_depths_conserve_value(exposures, taxonomy, depth, include_holdings):
    nodes = aggregate(exposures, taxonomy, taxonomy="ai", depth=depth, include_holdings=include_holdings)
    assert_conserved(nodes, 260)


def test_multi_path_allocation_and_root_selection(exposures, taxonomy):
    allocations = classify_exposures(exposures, taxonomy, "ai")
    assert allocations.loc[allocations["asset_id"] == "a", "value"].tolist() == [60, 60]
    root = ("AI", "Infrastructure")
    nodes = aggregate(exposures, taxonomy, taxonomy="ai", root=root, depth=1)
    assert_conserved(nodes, 190)
    assert set(nodes.loc[nodes["is_leaf"], "label"]) == {"Energy", "Shared", "Assigned here"}
    assert aggregate(exposures, taxonomy, taxonomy="ai", root=("Absent",)).empty


def test_same_labels_under_different_parents_are_distinct(exposures, taxonomy):
    nodes = aggregate(exposures, taxonomy, taxonomy="ai")
    shared = nodes.loc[nodes["label"] == "Shared"]
    assert len(shared) == 2
    assert shared["node_id"].nunique() == 2
    assert shared["parent_id"].nunique() == 2


def test_missing_taxonomy_keeps_every_exposure(exposures):
    nodes = aggregate(exposures, {}, taxonomy="custom")
    assert_conserved(nodes, 260)
    assert nodes.iloc[1]["label"] == "Unclassified"


def test_deep_paths(exposures):
    path = tuple(f"level-{i}" for i in range(100))
    taxonomy = {asset: {"deep": (path,)} for asset in exposures["asset_id"]}
    nodes = aggregate(exposures, taxonomy, taxonomy="deep")
    assert len(nodes) == 101
    assert_conserved(nodes, 260)


def test_subset_relative_weights_and_branch_filters(valued, classifications):
    selected = filter_holdings(valued, classifications, metadata={"portfolio": ["AI Sleeve"]})
    assert selected["current_value_eur"].sum() == 520
    weights = portfolio_weights(selected["current_value_eur"])
    assert weights.sum() == pytest.approx(1)
    assert weights.iloc[0] == pytest.approx(160 / 520)
    branch = filter_holdings(selected, classifications, taxonomy_branches={"ai": [("AI", "AI Infrastructure")]})
    assert set(branch["id"]) == {"enr", "anet"}
    branch = filter_holdings(branch, classifications, metadata={"account": ["Demo account A"]})
    assert branch.empty
    assert filter_holdings(valued, classifications, asset_ids=[]).empty
    assert filter_holdings(valued, classifications, metadata={"portfolio": []}).empty


def test_branch_selection_keeps_whole_multi_label_holding(exposures, taxonomy):
    holdings = exposures.rename(columns={"asset_id": "id"})
    selected = filter_holdings(holdings, taxonomy, taxonomy_branches={"ai": [("AI", "Compute")]})
    assert selected["value"].sum() == 120
    # Drilling into Compute instead displays its allocated half.
    nodes = aggregate(exposures, taxonomy, taxonomy="ai", root=("AI", "Compute"))
    assert_conserved(nodes, 60)


def test_exposure_boundary_and_flat_dimensions(valued):
    exposures = normalize_exposures(valued)
    assert len(exposures) == 6
    assert exposures["direct_or_indirect"].eq("direct").all()
    assert exposures["source_position_id"].is_unique
    for dimension in ("holding", "portfolio", "account"):
        nodes = aggregate_dimension(exposures, dimension)
        assert_conserved(nodes, 744)
    holdings = aggregate_dimension(exposures, "holding", show_tickers=True)
    assert holdings.loc[holdings["label"] == "Nvidia (NVDA)", "value"].iloc[0] == 240


def test_empty_and_zero_allocations(exposures, taxonomy):
    assert aggregate(exposures.iloc[:0], taxonomy, taxonomy="ai").empty
    exposures["value"] = 0.0
    nodes = aggregate(exposures, taxonomy, taxonomy="ai")
    assert nodes["percentage"].isna().all()
    assert nodes["value"].sum() == 0


def test_charts_use_unique_node_ids_and_valid_parent_totals(exposures, taxonomy):
    nodes = aggregate(exposures, taxonomy, taxonomy="ai")
    for chart_type in ("Treemap", "Sunburst"):
        trace = hierarchy_chart(nodes, chart_type).data[0]
        assert trace.branchvalues == "total"
        assert list(trace.ids) == nodes["node_id"].tolist()
    trace = bar_chart(nodes).data[0]
    assert sum(trace.x) == 260
    pie = pie_chart(nodes).data[0]
    assert sum(pie.values) == 260
    assert len(pie.values) == nodes["is_leaf"].sum()
    assert len(set(pie.ids)) == len(pie.ids)


def test_sunburst_has_distinct_palette_and_room_for_complete_circle(exposures, taxonomy):
    nodes = aggregate(exposures, taxonomy, taxonomy="ai", include_holdings=True)
    sunburst = hierarchy_chart(nodes, "Sunburst")
    treemap = hierarchy_chart(nodes, "Treemap")
    assert sunburst.layout.height >= 600
    assert min(sunburst.layout.margin[key] for key in ("l", "r", "t", "b")) >= 20
    assert sunburst.layout.sunburstcolorway != treemap.layout.treemapcolorway
    assert sunburst.data[0].insidetextorientation == "radial"
    assert sunburst.layout.uniformtext.mode == "hide"


def test_default_sort_orders_siblings_without_detaching_descendants(exposures, taxonomy):
    original = aggregate(exposures, taxonomy, taxonomy="ai", include_holdings=True)
    ordered = sort_allocation_nodes(original)
    assert ordered.iloc[0].parent_id == ""
    assert set(ordered.node_id) == set(original.node_id)
    for parent, children in ordered.groupby("parent_id", sort=False):
        assert children.percentage.is_monotonic_decreasing
        if parent:
            assert ordered.index[ordered.node_id == parent][0] < children.index.min()
    # Every subtree occupies one contiguous block in the displayed hierarchy.
    for start, row in ordered.iterrows():
        descendants = {row.node_id}
        for item in ordered.iloc[start + 1:].itertuples():
            if item.parent_id in descendants:
                descendants.add(item.node_id)
        positions = ordered.index[ordered.node_id.isin(descendants)].tolist()
        assert positions == list(range(start, start + len(descendants)))
    assert_conserved(ordered, original.iloc[0].value)


@pytest.mark.parametrize("depth", [-1, 1.5, True])
def test_invalid_depth(exposures, taxonomy, depth):
    with pytest.raises(ValueError):
        aggregate(exposures, taxonomy, taxonomy="ai", depth=depth)
