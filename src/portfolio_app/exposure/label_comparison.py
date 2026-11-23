"""Compare chosen hierarchy labels against normalized asset exposures."""

from dataclasses import dataclass
import json
import math
from typing import Literal, Mapping

import pandas as pd

from portfolio_app.exposure.taxonomy import Classifications, TaxonomyPath, UNCLASSIFIED, branches, is_descendant, paths_for
from portfolio_app.exposure.aggregation import ALLOCATION_COLUMNS, build_tree


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
    return tuple(LabelSelection(classifications, labels).matches(asset_id))


def label_memberships(exposures: pd.DataFrame, classifications: Classifications, labels: list[Label]) -> dict[str, tuple[Label, ...]]:
    selection = LabelSelection(classifications, labels)
    return {asset: tuple(selection.matches(asset)) for asset in exposures["asset_id"].unique()}



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


class LabelSelection:
    """Classification evidence shared by all measures in one selected-label view."""

    def __init__(self, classifications: Classifications, labels: list[Label]):
        self.classifications = classifications
        self.labels = tuple(dict.fromkeys(labels))
        self._keys = {label: label.key for label in self.labels}
        self._order = {label: index for index, label in enumerate(self.labels)}
        self._by_taxonomy = {}
        for label in self.labels:
            depths = self._by_taxonomy.setdefault(label.taxonomy, {})
            depths.setdefault(len(label.path), {})[label.path] = label
        self._assets: dict[str, dict[Label, tuple[TaxonomyPath, ...]]] = {}

    def matches(self, asset_id: str) -> Mapping[Label, tuple[TaxonomyPath, ...]]:
        """Selected labels in selection order, each with its distinct matching paths."""
        if asset_id not in self._assets:
            found = {}
            for taxonomy, depths in self._by_taxonomy.items():
                for path in dict.fromkeys(paths_for(self.classifications, asset_id, taxonomy)):
                    for depth, choices in depths.items():
                        label = choices.get(path[:depth])
                        if label is not None:
                            found.setdefault(label, []).append(path)
            self._assets[asset_id] = {label: tuple(found[label]) for label in sorted(found, key=self._order.__getitem__)}
        return self._assets[asset_id]

    @staticmethod
    def _valued(exposures, overlap):
        if overlap not in {"split", "overlap"}:
            raise ValueError("Choose how to allocate assets matching multiple labels.")
        valued = exposures.loc[exposures["value"].notna(), ["asset_id", "asset_name", "value"]]
        if any(not math.isfinite(value) for value in valued["value"]):
            raise ValueError("Label comparison requires finite exposure values.")
        return valued

    def _assignments(self, valued, overlap):
        for row in valued.itertuples(index=False):
            matches = self.matches(row.asset_id)
            if matches:
                yield row, matches, row.value / len(matches) if overlap == "split" else row.value

    def measure_values(self, exposures, *, overlap, root=()):
        """Target and performance amounts at the label or drilled asset level."""
        rows = self._assignments(self._valued(exposures, overlap), overlap)
        if not root:
            totals = dict.fromkeys(self.labels, 0.)
            for _, matches, assigned in rows:
                for label in matches:
                    totals[label] += assigned
            return pd.DataFrame([(label.title, amount) for label, amount in totals.items()],
                                columns=["Label", "Value"]).sort_values(
                ["Value", "Label"], ascending=[False, True], kind="stable", ignore_index=True)
        columns = ["asset_id", "asset_name", "value"]
        label = next((label for label in self.labels if self._keys[label] == root[0]), None)
        if label is None:
            return pd.DataFrame(columns=columns)
        branch = (*label.path, *root[1:])
        details, names = [], {}
        for row, matches, assigned in rows:
            paths = matches.get(label, ())
            for path in paths:
                if is_descendant(path, branch):
                    name = names.setdefault(row.asset_id, row.asset_name)
                    details.append((row.asset_id, name, assigned / len(paths)))
        return pd.DataFrame(details, columns=columns)

    def compare(self, exposures: pd.DataFrame, *, overlap: Literal["split", "overlap"]) -> LabelComparison:
        labels = self.labels
        valued = self._valued(exposures, overlap)
        totals = dict.fromkeys(labels, 0.0)
        assets = {label: set() for label in labels}
        portfolio_value = math.fsum(valued["value"])
        matched, overlapping, details = [], [], []
        assigned_total = 0.0
        # ETF providers can name the same asset differently. A holding leaf must
        # still merge by asset identity, including when it has several source rows.
        asset_names = valued.drop_duplicates("asset_id").set_index("asset_id")["asset_name"].to_dict()
        for row, matches, assigned in self._assignments(valued, overlap):
            matched.append(row.value)
            if len(matches) > 1:
                overlapping.append(row.value)
            for label, paths in matches.items():
                key = self._keys[label]
                totals[label] += assigned
                assets[label].add(row.asset_id)
                assigned_total += assigned
                for path in paths:
                    details.append({"asset_id": row.asset_id, "asset_name": asset_names[row.asset_id],
                                    "path": (key, *path[len(label.path):]), "value": assigned / len(paths)})
        matched_value = math.fsum(matched)
        # Include explicitly chosen zero-value labels in the comparison table.
        table = pd.DataFrame([{
            "Label": label.title, "Value": totals[label], "Assets": len(assets[label]),
            "Selected labels %": 100 * totals[label] / matched_value if matched_value > 0 else float("nan"),
            "Portfolio %": 100 * totals[label] / portfolio_value if portfolio_value > 0 else float("nan"),
        } for label in labels], columns=["Label", "Value", "Assets", "Selected labels %", "Portfolio %"])
        table = table.sort_values(["Value", "Label"], ascending=[False, True], kind="stable", ignore_index=True)
        categories = [((self._keys[label],), totals[label]) for label in labels if assets[label]]
        nodes = build_tree(pd.DataFrame(categories, columns=["path", "value"]), root_label="Selected labels")
        titles = {label.key: label.title for label in labels}
        nodes.loc[nodes["depth"] == 1, "label"] = nodes.loc[nodes["depth"] == 1, "label"].map(titles)
        nodes["path"] = nodes["path"].map(lambda path: (titles[path[0]],) if path else ())
        if not nodes.empty:
            # Preserve source-order addition at the root. In overlap mode the
            # sum assigned to labels can exceed the distinct matched value.
            nodes.at[0, "value"] = assigned_total
            denominator = matched_value if matched_value > 0 else assigned_total
            nodes["percentage"] = nodes["value"] / denominator if denominator > 0 else float("nan")
        return LabelComparison(table, nodes, pd.DataFrame(details, columns=ALLOCATION_COLUMNS),
                               portfolio_value, matched_value, math.fsum(overlapping))


def compare_labels(exposures: pd.DataFrame, classifications: Classifications, labels: list[Label],
                   *, overlap: Literal["split", "overlap"]) -> LabelComparison:
    """Compare whole matched exposures, after filtering and optional ETF expansion.

    Percentages use the union of matching assets, counted once, as denominator.
    In overlap mode label totals can exceed that denominator; only a bar/table
    comparison is meaningful. Tree charts must only be used in split mode (or
    when overlapping_value is zero).
    """
    return LabelSelection(classifications, labels).compare(exposures, overlap=overlap)


def comparison_tree(comparison: LabelComparison, labels: list[Label], *,
                    root: TaxonomyPath = (), assets_only: bool = False, include_holdings: bool = True) -> pd.DataFrame:
    """Decompose assigned label amounts without recalculating their membership.

    Within a label, multiple matching classification paths share its assigned
    value equally. Drilling never restores an asset's pre-split whole value.
    """
    nodes = build_tree(comparison.allocations, root=root, depth=(0 if root else 1) if assets_only else None,
                       include_holdings=include_holdings, root_label="Selected labels")
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
    assets["Allocation %"] = 100 * assets["value"] / total if total > 0 else float("nan")
    return assets.rename(columns={"asset_name": "Investment", "value": "Value"}).sort_values(
        ["Value", "Investment"], ascending=[False, True], kind="stable", ignore_index=True,
    )[[*(["asset_id"] if include_ids else []), "Investment", "Value", "Allocation %"]]
