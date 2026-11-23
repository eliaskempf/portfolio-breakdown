"""Value-conserving allocation trees shared by every chart and table."""

from collections import defaultdict
from functools import cache
import json

import pandas as pd

from portfolio_app.exposure.taxonomy import Classifications, TaxonomyPath, is_descendant, paths_for

NODE_COLUMNS = ["node_id", "parent_id", "label", "path", "depth", "value", "percentage", "is_leaf", "kind"]
ALLOCATION_COLUMNS = ["asset_id", "asset_name", "path", "value"]


def classify_exposures(exposures: pd.DataFrame, classifications: Classifications, taxonomy: str) -> pd.DataFrame:
    if exposures.empty:
        return pd.DataFrame(columns=ALLOCATION_COLUMNS)
    records = []
    for asset, name, value in zip(exposures.asset_id, exposures.asset_name, exposures.value):
        paths = paths_for(classifications, asset, taxonomy)
        share = value / len(paths)
        records.extend((asset, name, path, share) for path in paths)
    return pd.DataFrame.from_records(records, columns=ALLOCATION_COLUMNS)


def _node_id(kind: str, path: TaxonomyPath, asset_id: str = "") -> str:
    return json.dumps([kind, list(path), asset_id], ensure_ascii=False)


def build_tree(
    allocations: pd.DataFrame,
    *,
    root: TaxonomyPath = (),
    depth: int | None = None,
    include_holdings: bool = False,
    root_label: str = "All",
) -> pd.DataFrame:
    """Depth is relative to root; shorter paths remain terminal allocation buckets.

    A category can have both its own allocation and classified descendants. An
    explicit 'Assigned here' leaf makes that remainder visible in every chart.
    """
    if depth is not None and (not isinstance(depth, int) or isinstance(depth, bool) or depth < 0):
        raise ValueError("Depth must be a nonnegative integer or None.")
    nodes: dict[str, dict] = {}
    terminal_values: dict[TaxonomyPath, float] = defaultdict(float)
    asset_values: dict[tuple[TaxonomyPath, str], float] = defaultdict(float)
    asset_names: dict[str, str] = {}
    @cache
    def category_id(path):
        return _node_id("category", path)

    root_id = category_id(root)
    for row in allocations.itertuples(index=False):
        if not is_descendant(row.path, root):
            continue
        path = row.path if depth is None else row.path[: len(root) + depth]
        if include_holdings:
            asset_values[(path, row.asset_id)] += row.value
            asset_names[row.asset_id] = min(asset_names.get(row.asset_id, row.asset_name), row.asset_name)
        else:
            terminal_values[path] += row.value
        for length in range(len(root), len(path) + 1):
            prefix = path[:length]
            node_id = category_id(prefix)
            if node_id not in nodes:
                nodes[node_id] = {
                    "node_id": node_id,
                    "parent_id": "" if prefix == root else category_id(prefix[:-1]),
                    "label": prefix[-1] if prefix else root_label,
                    "path": prefix,
                    "depth": length - len(root),
                    "value": 0.0,
                    "kind": "category",
                }
            nodes[node_id]["value"] += row.value
    if not nodes:
        return pd.DataFrame(columns=NODE_COLUMNS)
    if include_holdings:
        for (path, asset_id), value in sorted(asset_values.items()):
            node_id = _node_id("asset", path, asset_id)
            nodes[node_id] = {
                "node_id": node_id, "parent_id": category_id(path),
                "label": asset_names[asset_id], "path": path, "depth": len(path) - len(root) + 1,
                "value": value, "kind": "holding",
            }
    else:
        parents = {node["parent_id"] for node in nodes.values()}
        for path, value in terminal_values.items():
            if category_id(path) in parents:
                node_id = _node_id("assigned", path)
                nodes[node_id] = {
                    "node_id": node_id, "parent_id": category_id(path),
                    "label": "Assigned here", "path": path, "depth": len(path) - len(root) + 1,
                    "value": value, "kind": "assigned",
                }
    total = nodes[root_id]["value"]
    parents = {node["parent_id"] for node in nodes.values()}
    children: dict[str, list[str]] = defaultdict(list)
    for node_id, node in nodes.items():
        node["percentage"] = node["value"] / total if total > 0 else float("nan")
        node["is_leaf"] = node_id not in parents
        children[node["parent_id"]].append(node_id)
    # Iterative traversal supports user-defined depths without a recursion limit.
    records = []
    pending = [root_id]
    while pending:
        node_id = pending.pop()
        records.append(nodes[node_id])
        pending.extend(sorted(children[node_id], key=lambda child: (nodes[child]["label"], child), reverse=True))
    return pd.DataFrame(records, columns=NODE_COLUMNS)


def aggregate(
    exposures: pd.DataFrame,
    classifications: Classifications,
    *,
    taxonomy: str,
    root: TaxonomyPath = (),
    depth: int | None = None,
    include_holdings: bool = False,
) -> pd.DataFrame:
    return build_tree(
        classify_exposures(exposures, classifications, taxonomy),
        root=root, depth=depth, include_holdings=include_holdings, root_label=taxonomy,
    )


def flat_category_tree(totals, *, root_label, total, denominator):
    """Build one leaf per total while retaining the root's source-order sum."""
    categories = [((key,), value) for key, value in totals.items()]
    nodes = build_tree(pd.DataFrame(categories, columns=["path", "value"]), root_label=root_label)
    if not nodes.empty:
        nodes.at[0, "value"] = total
        nodes["percentage"] = nodes["value"] / denominator if denominator > 0 else float("nan")
    return nodes


def _dimension_totals(exposures, dimension):
    if dimension == "holding":
        keys = exposures["asset_id"]
    else:
        keys = (str(value) if value is not pd.NA and value else "Unspecified" for value in exposures[dimension])
    totals = defaultdict(float)
    total = 0.0
    amounts = exposures["value"].to_numpy(dtype="float64", na_value=float("nan"))
    for key, value in zip(keys, amounts):
        totals[key] += value
        total += value
    return totals, total


def dimension_measure(exposures: pd.DataFrame, dimension: str) -> pd.DataFrame:
    """Secondary amounts keyed to existing chart leaves. Absent amounts align to zero."""
    columns = ["node_id", "value"]
    if exposures.empty:
        return pd.DataFrame(columns=columns)
    key = "asset_id" if dimension == "holding" else dimension
    # Unknown amounts still propagate to their category. Zero is additive and
    # cannot introduce a new row in the primary allocation table.
    rows = exposures.loc[exposures.value.ne(0).fillna(True), list(dict.fromkeys([key, "value"]))]
    totals, _ = _dimension_totals(rows, dimension)
    return pd.DataFrame([(_node_id("category", (key,)), amount) for key, amount in totals.items()], columns=columns)


def aggregate_dimension(exposures: pd.DataFrame, dimension: str, *, show_tickers: bool = False) -> pd.DataFrame:
    if exposures.empty:
        return pd.DataFrame(columns=NODE_COLUMNS)
    totals, total = _dimension_totals(exposures, dimension)
    nodes = flat_category_tree(totals, root_label=dimension, total=total, denominator=total)
    if dimension == "holding":
        # The last source supplies the display name, independently of the stable node ID.
        columns = [name for name in ("asset_id", "asset_name", "ticker", "source_type") if name in exposures]
        last = exposures.loc[:, columns].drop_duplicates("asset_id", keep="last")
        labels = last.set_index("asset_id")["asset_name"].to_dict()
        if show_tickers:
            for row in last.to_dict("records"):
                if row.get("source_type") != "instrument_group":
                    symbol = str(row.get("ticker") or row["asset_id"]).upper()
                    labels[row["asset_id"]] = f"{row['asset_name']} ({symbol})"
        leaves = nodes["depth"] == 1
        nodes.loc[leaves, "label"] = nodes.loc[leaves, "label"].map(labels)
        nodes["path"] = nodes["path"].map(lambda path: (labels[path[0]],) if path else ())
    return nodes
