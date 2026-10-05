import pandas as pd
import pytest

from portfolio_app.label_comparison import Label, compare_labels
from portfolio_app.targets import add_target_columns, target_exposures, target_totals
from portfolio_app.valuation import value_holdings


@pytest.fixture
def positions():
    # Invented targets, with repeated asset A, an unpriced asset and a missing target.
    return pd.DataFrame({
        "position_id": ["p1", "p2", "p3", "p4", "p5", "p6"],
        "id": ["a", "b", "c", "unpriced", "missing", "a"],
        "name": ["Synthetic A", "Synthetic B", "Synthetic C", "Unpriced example", "Missing target", "Synthetic A"],
        "ticker": ["", "", "", "", "", ""], "isin": [""] * 6,
        "shares": [1., 1., 0., 2., 1., 1.],
        "current_value_reporting": [100., 100., 0., float("nan"), 50., 50.],
        "target_allocation": [.2, .3, .1, .15, float("nan"), .05],
    })


def test_target_exposures_include_zero_and_unpriced_positions_without_renormalizing(positions):
    targets = target_exposures(positions, [])
    assert targets.known["value"].sum() == pytest.approx(.8)
    assert targets.missing["value"].sum() == 1
    assert targets.known.loc[targets.known.asset_id == "c", "value"].tolist() == [.1]
    assert targets.known.loc[targets.known.asset_id == "unpriced", "value"].tolist() == [.15]
    filtered = target_exposures(positions.loc[positions.id == "a"], [])
    assert filtered.known["value"].sum() == .25
    pd.testing.assert_series_equal(positions.target_allocation, pd.Series([.2, .3, .1, .15, float("nan"), .05], name="target_allocation"))


@pytest.mark.parametrize("policy,expected", [("split", [.125, .125]), ("overlap", [.25, .25])])
def test_targets_follow_selected_label_overlap_and_descendant_splits(positions, policy, expected):
    classes = {"a": {"labels": (("Group A", "First"), ("Group A", "Second"))}}
    parent, child = Label("labels", ("Group A",)), Label("labels", ("Group A", "First"))
    targets = target_exposures(positions, [])
    comparisons = [compare_labels(frame, classes, [parent, child], overlap=policy) for frame in (targets.known, targets.missing)]
    assert comparisons[0].table["Value"].tolist() == expected
    details = comparisons[0].allocations
    assert details.loc[details.path == (parent.key, "First"), "value"].sum() == expected[0] / 2
    assert comparisons[1].table["Value"].sum() == 0


def test_incomplete_targets_show_subtotal_without_gap_and_zero_is_explicit(positions):
    targets = target_exposures(positions, [])
    classes = {asset: {"labels": (("Group A",),)} for asset in ["a", "missing"]}
    label = Label("labels", ("Group A",))
    comparisons = [compare_labels(frame, classes, [label], overlap="split") for frame in (targets.known, targets.missing)]
    totals = target_totals(*(item.table for item in comparisons), key="Label", value="Value")
    table = add_target_columns(pd.DataFrame({"Value": [200.]}), [label.title], totals, portfolio_value=1000., valuation_complete=True)
    assert table.iloc[0]["Known target portfolio %"] == 25
    assert pd.isna(table.iloc[0]["Target portfolio %"])
    assert pd.isna(table.iloc[0]["Gap (pp)"])
    assert table.iloc[0]["Target status"] == "Incomplete"
    positions.loc[positions.id == "missing", "target_allocation"] = 0.
    targets = target_exposures(positions, [])
    totals = target_totals(targets.known, targets.missing, key="asset_id")
    table = add_target_columns(pd.DataFrame({"Value": [50., 0.]}), ["missing", "c"], totals,
                               portfolio_value=1000., valuation_complete=True)
    assert table["Target portfolio %"].tolist() == [0, 10]
    assert table["Gap (pp)"].tolist() == [5, -10]
    incomplete = add_target_columns(pd.DataFrame({"Value": [50.]}), ["missing"], totals,
                                    portfolio_value=1000., valuation_complete=False)
    assert incomplete["Gap (pp)"].isna().all()


def test_zero_shares_are_valued_at_zero_without_prices(positions):
    class NoQuotes:
        def price(self, *args, **kwargs):
            raise AssertionError("A target-only position must not need market data")

    zero = positions.loc[positions.shares == 0].copy()
    zero["ticker"] = "SYNTHETIC"
    valued = value_holdings(zero, NoQuotes())
    assert valued.current_value_reporting.tolist() == [0]
    assert valued.target_allocation.tolist() == [.1]
    assert valued.price_status.tolist() == ["not_held"]
