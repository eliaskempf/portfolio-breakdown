"""Compare chosen hierarchy labels against normalized asset exposures."""

from dataclasses import dataclass
import json
import math
from typing import Literal

import pandas as pd

from portfolio_app.taxonomy import Classifications, TaxonomyPath, UNCLASSIFIED, branches, is_descendant, paths_for
from portfolio_app.aggregation import ALLOCATION_COLUMNS, build_tree


@dataclass(frozen=True)
class Label:
    taxonomy: str
    path: TaxonomyPath

    @property
    def key(self) -> str:
        return json.dumps([self.taxonomy, self.path], ensure_ascii=False)

    @property
    def title(self) -> str:
        return " › ".join(self.path)


def available_labels(classifications: Classifications, taxonomy: str) -> list[Label]:
    """Stable choices include unvalued assets, independent of current filters."""
    paths = set(branches(classifications, list(classifications), taxonomy)) | {UNCLASSIFIED}
    return [Label(taxonomy, path) for path in sorted(paths)]


def matching_labels(asset_id: str, classifications: Classifications, labels: list[Label]) -> tuple[Label, ...]:
    """An asset matches a label or any descendant, once per selected label.

    Matching two paths beneath a selected parent does not count the asset twice.
    This tests membership, without applying the taxonomy's allocation splits.
    """
    return tuple(label for label in dict.fromkeys(labels) if any(
        is_descendant(path, label.path)
        for path in paths_for(classifications, asset_id, label.taxonomy)
    ))


def label_memberships(exposures: pd.DataFrame, classifications: Classifications, labels: list[Label]) -> dict[str, tuple[Label, ...]]:
    return {asset: matching_labels(asset, classifications, labels) for asset in exposures["asset_id"].unique()}


@dataclass
class LabelComparison:
    table: pd.DataFrame
    nodes: pd.DataFrame
    allocations: pd.DataFrame
    portfolio_value: float
    matched_value: float
    overlapping_value: float

    @property
    def unmatched_value(self) -> float:
        return max(0.0, self.portfolio_value - self.matched_value)


def compare_labels(exposures: pd.DataFrame, classifications: Classifications, labels: list[Label],
                   *, overlap: Literal["split", "overlap"]) -> LabelComparison:
    """Compare whole matched exposures, after filtering and optional ETF expansion.

    Percentages use the union of matching assets, counted once, as denominator.
    In overlap mode label totals can exceed that denominator; only a bar/table
    comparison is meaningful. Tree charts must only be used in split mode (or
    when overlapping_value is zero).
    """
    if overlap not in {"split", "overlap"}:
        raise ValueError("Choose how to allocate assets matching multiple labels.")
    labels = list(dict.fromkeys(labels))
    valued = exposures.loc[exposures["value"].notna()]
    if any(not math.isfinite(value) or value < 0 for value in valued["value"]):
        raise ValueError("Label comparison requires finite, nonnegative exposure values.")
    memberships = label_memberships(valued, classifications, labels)
    totals = dict.fromkeys(labels, 0.0)
    assets = {label: set() for label in labels}
    portfolio_value = math.fsum(valued["value"])
    matched, overlapping, allocations, details = [], [], [], []
    # ETF providers can name the same asset differently. A holding leaf must
    # still merge by asset identity, including when it has several source rows.
    asset_names = valued.drop_duplicates("asset_id").set_index("asset_id")["asset_name"].to_dict()
    for row in valued.itertuples(index=False):
        matches = memberships[row.asset_id]
        if not matches:
            continue
        matched.append(row.value)
        if len(matches) > 1:
            overlapping.append(row.value)
        for label in matches:
            assigned = row.value / len(matches) if overlap == "split" else row.value
            totals[label] += assigned
            assets[label].add(row.asset_id)
            allocations.append({"asset_id": row.asset_id, "asset_name": row.asset_name,
                                "path": (label.key,), "value": assigned})
            paths = tuple(dict.fromkeys(path for path in paths_for(classifications, row.asset_id, label.taxonomy)
                                        if is_descendant(path, label.path)))
            for path in paths:
                details.append({"asset_id": row.asset_id, "asset_name": asset_names[row.asset_id],
                                "path": (label.key, *path[len(label.path):]), "value": assigned / len(paths)})
    matched_value = math.fsum(matched)
    # Include explicitly chosen zero-value labels in the comparison table.
    table = pd.DataFrame([{
        "Label": label.title, "EUR value": totals[label], "Assets": len(assets[label]),
        "Selected labels %": 100 * totals[label] / matched_value if matched_value else float("nan"),
        "Portfolio %": 100 * totals[label] / portfolio_value if portfolio_value else float("nan"),
    } for label in labels], columns=["Label", "EUR value", "Assets", "Selected labels %", "Portfolio %"])
    table = table.sort_values(["EUR value", "Label"], ascending=[False, True], kind="stable", ignore_index=True)
    nodes = build_tree(pd.DataFrame(allocations, columns=ALLOCATION_COLUMNS), root_label="Selected labels")
    titles = {label.key: label.title for label in labels}
    nodes.loc[nodes["depth"] == 1, "label"] = nodes.loc[nodes["depth"] == 1, "label"].map(titles)
    nodes["path"] = nodes["path"].map(lambda path: (titles[path[0]],) if path else ())
    if matched_value:
        nodes["percentage"] = nodes["value"] / matched_value
    return LabelComparison(table, nodes, pd.DataFrame(details, columns=ALLOCATION_COLUMNS),
                           portfolio_value, matched_value, math.fsum(overlapping))


def comparison_tree(comparison: LabelComparison, labels: list[Label], *,
                    root: TaxonomyPath = (), assets_only: bool = False) -> pd.DataFrame:
    """Decompose assigned label amounts without recalculating their membership.

    Within a label, multiple matching classification paths share its assigned
    value equally. Drilling never restores an asset's pre-split whole value.
    """
    nodes = build_tree(comparison.allocations, root=root, depth=(0 if root else 1) if assets_only else None,
                       include_holdings=True, root_label="Selected labels")
    titles = {label.key: label.title for label in labels}
    nodes["label"] = nodes["label"].map(lambda label: titles.get(label, label))
    nodes["path"] = nodes["path"].map(lambda path: (titles[path[0]], *path[1:]) if path else ())
    return nodes


def immediate_children(nodes: pd.DataFrame) -> pd.DataFrame:
    """Disjoint next-level buckets for a flat chart of the current branch."""
    children = nodes.loc[nodes["depth"] <= 1].copy()
    children["is_leaf"] = children["depth"] == 1
    children.loc[children["kind"] == "holding", "path"] = children.loc[children["kind"] == "holding", "label"].map(lambda name: (name,))
    return children


def comparison_assets(comparison: LabelComparison, root: TaxonomyPath, *, include_ids: bool = False) -> pd.DataFrame:
    """Unique assets assigned beneath a branch, combining all source rows/paths."""
    rows = comparison.allocations.loc[comparison.allocations["path"].map(lambda path: is_descendant(path, root))]
    assets = rows.groupby(["asset_id", "asset_name"], sort=False, as_index=False)["value"].sum()
    total = assets["value"].sum()
    assets["Allocation %"] = 100 * assets["value"] / total if total else float("nan")
    return assets.rename(columns={"asset_name": "Investment", "value": "EUR value"}).sort_values(
        ["EUR value", "Investment"], ascending=[False, True], kind="stable", ignore_index=True,
    )[[*(["asset_id"] if include_ids else []), "Investment", "EUR value", "Allocation %"]]
