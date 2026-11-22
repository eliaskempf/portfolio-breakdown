"""Invented positions exercise strategic ownership independently of labels."""

import pandas as pd
import pytest

from portfolio_app.portfolio.allocation import Allocation, Bucket
from portfolio_app.portfolio.strategic import bucket_positions, strategic_summary, strategic_tree


@pytest.fixture
def config():
    return Allocation((Bucket("long", "Long term", target=.6),
                       Bucket("funds", "Funds", "long", .75),
                       Bucket("reserve", "Reserve", "long", .25),
                       Bucket("active", "Active", target=.4),
                       Bucket("planned", "Planned", target=0.)))


@pytest.fixture
def positions():
    return pd.DataFrame([
        dict(position_id="p1", id="same-asset", name="Invented fund", bucket_id="funds", shares=1., current_value_reporting=60., within_bucket_target=1.),
        dict(position_id="p2", id="same-asset", name="Invented fund", bucket_id="active", shares=2., current_value_reporting=30., within_bucket_target=.8),
        dict(position_id="p3", id="other", name="Invented stock", bucket_id="active", shares=1., current_value_reporting=10., within_bucket_target=.2),
        dict(position_id="p4", id="unknown", name="Unassigned holding", bucket_id="", shares=0., current_value_reporting=0., within_bucket_target=float("nan")),
    ])


def test_tree_conserves_source_values_and_keeps_planned_buckets(positions, config):
    before = positions.copy(deep=True)
    tree = strategic_tree(positions, config)
    assert tree.iloc[0].value == 100
    assert tree.node_id.is_unique
    for parent in tree.itertuples():
        children = tree.loc[tree.parent_id.eq(parent.node_id)]
        if not children.empty:
            assert children.value.sum() == pytest.approx(parent.value)
    assert tree.loc[tree.kind.eq("holding"), "value"].sum() == 100
    assert set(tree.loc[tree.kind.eq("category"), "label"]) == {"Portfolio", "Long term", "Funds", "Reserve", "Active", "Planned", "Unassigned"}
    assert tree.loc[tree.label.eq("Planned"), "value"].item() == 0
    pd.testing.assert_frame_equal(positions, before)


def test_drilling_changes_denominator_and_targets_to_selected_parent(positions, config):
    root = strategic_summary(positions, config).set_index("Category")
    assert root.loc["Long term", "Current (%)"] == 60
    assert root.loc["Long term", "Target (%)"] == 60
    children = strategic_summary(positions, config, "long").set_index("Category")
    assert children.loc["Funds", "Current (%)"] == 100
    assert children.loc["Funds", "Target (%)"] == 75
    assert children.loc["Reserve", "Current (%)"] == 0
    assert children.loc["Reserve", "Target (%)"] == 25
    detail = strategic_summary(positions, config, "active")
    assert detail["Current (%)"].tolist() == [75, 25]
    assert detail["Target (%)"].tolist() == [80, 20]
    assert bucket_positions(positions, config, "active").position_id.tolist() == ["p2", "p3"]
    assert strategic_tree(positions, config, "funds").iloc[0].value == 60


def test_missing_prices_are_not_zero_and_unknown_targets_stay_blank(positions, config):
    positions.loc[0, "current_value_reporting"] = float("nan")
    positions.loc[2, "within_bucket_target"] = float("nan")
    tree = strategic_tree(positions, config)
    assert tree.iloc[0].value == 40  # Known chart area only.
    assert tree.iloc[0].missing_prices == 1
    assert pd.isna(tree.iloc[0].current_value)
    summary = strategic_summary(positions, config).set_index("Category")
    assert pd.isna(summary.loc["Long term", "Value"])
    assert summary["Current (%)"].isna().all()
    assert summary["Gap (pp)"].isna().all()
    assert summary.loc["Planned", "Value"] == 0
    detail = strategic_summary(positions, config, "active")
    assert detail["Current (%)"].sum() == 100  # Other categories don't invalidate this scope.
    assert pd.isna(detail.loc[1, "Target (%)"])
    assert pd.isna(detail.loc[1, "Gap (pp)"])


def test_all_empty_bucket_and_duplicate_names(positions):
    config = Allocation((Bucket("x", "Same name", target=.3), Bucket("y", "Same name", target=.7)))
    empty = positions.iloc[:0]
    tree = strategic_tree(empty, config)
    assert tree.value.eq(0).all()
    assert tree.node_id.is_unique
    assert tree.label.eq("Same name").sum() == 2
    summary = strategic_summary(empty, config)
    assert summary["Current (%)"].isna().all()
    assert summary["Target (%)"].sum() == 100


def test_unassigned_and_account_rows_remain_independent(positions, config):
    positions.loc[3, ["shares", "current_value_reporting"]] = [1., 20.]
    tree = strategic_tree(positions, config, "unassigned")
    assert tree.iloc[0].value == 20
    assert len(tree.loc[tree.kind.eq("holding")]) == 1
    root = strategic_summary(positions, config).set_index("Category")
    assert root.loc["Unassigned", "Current (%)"] == pytest.approx(100 / 6)
    assert pd.isna(root.loc["Unassigned", "Target (%)"])


def test_icon_palette_follows_actual_allocation_not_category_ids(positions, config):
    from portfolio_app.interface.shared.charts import strategic_colors
    tree = strategic_tree(positions, config)
    colors = dict(zip(tree.label, strategic_colors(tree, config)))
    assert colors['Long term'] == '#5470c6'
    assert colors['Active'] == '#4aa9b3'
    shuffled = tree.sample(frac=1, random_state=4)
    assert dict(zip(shuffled.node_id, strategic_colors(shuffled, config))) == dict(zip(tree.node_id, strategic_colors(tree, config)))
    positions.loc[0, 'current_value_reporting'] = 5
    changed = strategic_tree(positions, config)
    colors = dict(zip(changed.label, strategic_colors(changed, config)))
    assert colors['Active'] == '#5470c6'
    assert colors['Long term'] == '#4aa9b3'


def test_flat_summary_maps_partial_exposure_once_and_preserves_source_rows(positions, config, monkeypatch):
    positions['account'] = ['A', 'B', 'A', 'C']
    positions.loc[1, 'current_value_reporting'] = float('nan')
    exposure = {'p1': -2., 'p3': 0.}
    batches = []
    original_map = pd.Series.map
    def counted_map(series, arg, *args, **kwargs):
        if arg is exposure:
            batches.append(len(series))
        return original_map(series, arg, *args, **kwargs)
    monkeypatch.setattr(pd.Series, 'map', counted_map)
    table = strategic_summary(positions, config, flat=True, identities=True, known_exposure=exposure).set_index('position_id')
    assert batches == [len(positions)]
    assert table.loc['p1', 'Known exposure'] == -2.
    assert table.loc['p3', 'Known exposure'] == 0.
    assert pd.isna(table.loc['p2', 'Known exposure']) and table.loc['p2', 'Status'] == 'Missing price'
    assert table.loc['p2', 'Account'] == 'B' and table.loc['p4', 'Status'] == 'Empty'
    assert table['Current (%)'].isna().all()


def test_tree_propagates_missing_counts_without_rescanning_categories(positions, config, monkeypatch):
    from portfolio_app.portfolio import strategic
    scans = []
    def select(frame, allocation, bucket):
        scans.append(bucket)
        return bucket_positions(frame, allocation, bucket)
    monkeypatch.setattr(strategic, 'bucket_positions', select)
    positions.loc[[0, 2, 3], 'current_value_reporting'] = float('nan')
    tree = strategic_tree(positions, config)
    categories = tree[tree.kind.eq('category')].set_index('label')
    assert categories.missing_prices.to_dict() == {
        'Portfolio': 3, 'Long term': 1, 'Funds': 1, 'Reserve': 0,
        'Active': 1, 'Planned': 0, 'Unassigned': 1}
    assert categories.loc['Planned', 'current_value'] == 0.
    holdings = tree[tree.kind.eq('holding')]
    assert holdings.loc[holdings.value.eq(30.), 'missing_prices'].item() == 0
    assert scans == []
    flat = strategic_tree(positions, config, 'long', flat=True)
    assert flat.missing_prices.tolist() == [1, 1]
    assert scans == ['long']


def test_root_allocation_uses_one_tree_for_chart_and_colors(positions, config):
    from streamlit.testing.v1 import AppTest
    def draw():
        import streamlit as st
        from unittest.mock import patch
        from portfolio_app.interface.views.portfolio import strategic_ui
        with patch.object(strategic_ui, 'strategic_tree', wraps=strategic_ui.strategic_tree) as tree:
            strategic_ui.render_strategic_overview(st.session_state.positions, st.session_state.config)
        st.session_state.tree_calls = tree.call_count
    app = AppTest.from_function(draw)
    app.session_state.positions = positions
    app.session_state.config = config
    app.run()
    assert not app.exception
    assert app.session_state.tree_calls == 1
