import pandas as pd
import pytest

from portfolio_app.label_comparison import (
    Label, available_labels, compare_labels, comparison_assets, comparison_tree, immediate_children,
    label_memberships, matching_labels,
)


def test_selected_labels_include_descendants_without_double_counting_paths():
    classified = {"a": {"labels": (("Group A", "First"), ("Group A", "Second"), ("Group B",))}}
    a, b = Label("labels", ("Group A",)), Label("labels", ("Group B",))
    assert matching_labels("a", classified, [a, a, b]) == (a, b)
    assert matching_labels("a", classified, [Label("labels", ("Group A", "First"))]) == (Label("labels", ("Group A", "First")),)
    assert matching_labels("missing", classified, [a, b]) == ()


def test_same_named_labels_keep_their_taxonomy_and_full_path_identity():
    first = Label("labels", ("Group A", "Shared"))
    second = Label("labels", ("Group B", "Shared"))
    third = Label("other", ("Group A", "Shared"))
    assert len({item.key for item in (first, second, third)}) == 3
    classified = {"a": {"labels": (first.path,)}, "b": {"other": (third.path,)}}
    assert matching_labels("a", classified, [first, second, third]) == (first,)
    assert matching_labels("b", classified, [first, second, third]) == (third,)


def test_direct_and_indirect_rows_share_asset_membership_and_choices_are_stable():
    classified = {"a": {"labels": (("Group A", "Child"),)}, "unpriced": {"labels": (("Group B",),)}}
    exposures = pd.DataFrame({"asset_id": ["a", "a"]})
    choices = available_labels(classified, "labels")
    assert Label("labels", ("Group B",)) in choices
    assert label_memberships(exposures, classified, choices) == {
        "a": (Label("labels", ("Group A",)), Label("labels", ("Group A", "Child"))),
    }


@pytest.fixture
def comparison_data():
    # Entirely invented allocations, including direct and indirect rows for A.
    exposures = pd.DataFrame([
        {"asset_id": "a", "asset_name": "Synthetic A", "value": 100.},
        {"asset_id": "a", "asset_name": "Synthetic A", "value": 20.},
        {"asset_id": "b", "asset_name": "Synthetic B", "value": 80.},
        {"asset_id": "c", "asset_name": "Synthetic C", "value": 40.},
        {"asset_id": "other", "asset_name": "Synthetic residual", "value": 10.},
        {"asset_id": "missing", "asset_name": "Unpriced example", "value": float("nan")},
    ])
    classified = {
        "a": {"labels": (("Group A", "First"), ("Group A", "Second"), ("Group B",))},
        "b": {"labels": (("Group B", "Child"),)},
        "c": {"labels": (("Group C",),)},
    }
    return exposures, classified


def test_union_denominator_split_overlap_and_unmatched_value(comparison_data):
    exposures, classified = comparison_data
    labels = [Label("labels", ("Group A",)), Label("labels", ("Group B",))]
    result = compare_labels(exposures, classified, labels, overlap="split")
    table = result.table.set_index("Label")
    assert result.portfolio_value == 250
    assert result.matched_value == 200
    assert result.unmatched_value == 50
    assert result.overlapping_value == 120
    assert table.loc["Group A", "Value"] == 60
    assert table.loc["Group B", "Value"] == 140
    assert table.loc["Group A", "Assets"] == 1
    assert table.loc["Group B", "Assets"] == 2
    assert table["Selected labels %"].sum() == 100
    assert table["Portfolio %"].sum() == 80
    assert result.nodes.loc[result.nodes.is_leaf, "value"].sum() == result.matched_value


def test_full_overlap_keeps_unique_denominator_instead_of_normalizing_double_counted_total(comparison_data):
    exposures, classified = comparison_data
    result = compare_labels(exposures, classified, [Label("labels", ("Group A",)), Label("labels", ("Group B",))], overlap="overlap")
    assert result.matched_value == 200
    assert result.table["Value"].sum() == 320
    assert result.table["Selected labels %"].sum() == 160
    assert result.table["Portfolio %"].sum() == 128
    assert result.nodes.loc[result.nodes.is_leaf, "percentage"].sum() == pytest.approx(1.6)


def test_selecting_only_one_label_includes_full_matching_assets_not_old_taxonomy_split(comparison_data):
    exposures, classified = comparison_data
    result = compare_labels(exposures, classified, [Label("labels", ("Group A",))], overlap="split")
    assert result.matched_value == 120  # Not 2/3 of A's value from its three paths.
    assert result.table.iloc[0]["Selected labels %"] == 100
    assert result.table.iloc[0]["Portfolio %"] == 48


def test_parent_child_selection_is_explicit_overlap_and_labels_are_not_duplicated(comparison_data):
    exposures, classified = comparison_data
    parent = Label("labels", ("Group A",))
    child = Label("labels", ("Group A", "First"))
    result = compare_labels(exposures, classified, [parent, parent, child], overlap="split")
    assert result.overlapping_value == result.matched_value == 120
    assert len(result.table) == 2
    assert result.table["Value"].tolist() == [60, 60]


def test_empty_unknown_and_zero_label_selections(comparison_data):
    exposures, classified = comparison_data
    empty = compare_labels(exposures, classified, [], overlap="split")
    assert empty.table.empty and empty.nodes.empty
    assert empty.matched_value == 0 and empty.unmatched_value == 250
    missing = compare_labels(exposures, classified, [Label("labels", ("Absent",))], overlap="split")
    assert missing.table.iloc[0]["Value"] == 0
    assert missing.table["Selected labels %"].isna().all()
    unclassified = compare_labels(exposures, classified, [Label("labels", ("Unclassified",))], overlap="split")
    assert unclassified.matched_value == 10  # Residual stays explicit, unpriced excluded.
    exposures["value"] = 0.
    zero = compare_labels(exposures, classified, [Label("labels", ("Group A",))], overlap="split")
    assert zero.table["Selected labels %"].isna().all()
    assert zero.table["Portfolio %"].isna().all()


@pytest.mark.parametrize("value", [-1., float("inf")])
def test_invalid_values_are_rejected(comparison_data, value):
    exposures, classified = comparison_data
    exposures.loc[0, "value"] = value
    with pytest.raises(ValueError, match="nonnegative"):
        compare_labels(exposures, classified, [Label("labels", ("Group A",))], overlap="split")


@pytest.mark.parametrize("policy,assigned", [("split", 60), ("overlap", 120)])
def test_drill_preserves_assigned_label_value_and_conserves_every_branch(comparison_data, policy, assigned):
    exposures, classified = comparison_data
    # The ETF's public name differs, but its economic asset identity is the same.
    exposures.loc[1, "asset_name"] = "Synthetic A alternate name"
    labels = [Label("labels", ("Group A",)), Label("labels", ("Group B",))]
    comparison = compare_labels(exposures, classified, labels, overlap=policy)
    tree = comparison_tree(comparison, labels)
    for node in tree.loc[~tree.is_leaf].itertuples():
        assert tree.loc[tree.parent_id == node.node_id, "value"].sum() == pytest.approx(node.value)
    assert tree.node_id.is_unique
    root = (labels[0].key,)
    detail = comparison_tree(comparison, labels, root=root)
    assert detail.iloc[0]["label"] == "Group A"
    assert detail.iloc[0]["value"] == assigned
    children = immediate_children(detail)
    assert children.loc[children.is_leaf, "value"].tolist() == [assigned / 2, assigned / 2]
    assets = comparison_assets(comparison, root)
    assert assets["Investment"].tolist() == ["Synthetic A"]
    assert assets["Value"].tolist() == [assigned]
    assert assets["Allocation %"].tolist() == [100]
    subdetail = comparison_tree(comparison, labels, root=(*root, "First"))
    assert subdetail.iloc[0]["value"] == assigned / 2
    assert subdetail.loc[subdetail.is_leaf, "percentage"].sum() == 1


def test_assets_only_merges_multiple_paths_and_direct_indirect_rows(comparison_data):
    exposures, classified = comparison_data
    labels = [Label("labels", ("Group A",))]
    comparison = compare_labels(exposures, classified, labels, overlap="split")
    for root in [(), (labels[0].key,)]:
        tree = comparison_tree(comparison, labels, root=root, assets_only=True)
        leaves = tree.loc[tree.is_leaf]
        assert leaves["label"].tolist() == ["Synthetic A"]
        assert leaves["value"].tolist() == [120]
        assert leaves["percentage"].tolist() == [1]
    assert immediate_children(tree).iloc[-1]["path"] == ("Synthetic A",)


def test_unknown_and_parent_assigned_assets_remain_visible_in_drill():
    exposures = pd.DataFrame({"asset_id": ["a", "b", "other"], "asset_name": ["Synthetic A", "Synthetic B", "Synthetic residual"],
                              "value": [30., 50., 20.]})
    classified = {"a": {"labels": (("Group A",),)}, "b": {"labels": (("Group A", "Child"),)}}
    labels = [Label("labels", ("Group A",)), Label("labels", ("Unclassified",))]
    comparison = compare_labels(exposures, classified, labels, overlap="split")
    tree = comparison_tree(comparison, labels)
    assert tree.loc[tree.is_leaf, "value"].sum() == 100
    assert set(tree.loc[tree.is_leaf, "label"]) == {"Synthetic A", "Synthetic B", "Synthetic residual"}
    detail = immediate_children(comparison_tree(comparison, labels, root=(labels[0].key,)))
    assert detail.loc[detail.is_leaf, "value"].sum() == 80
    assert set(detail.loc[detail.is_leaf, "label"]) == {"Synthetic A", "Child"}
