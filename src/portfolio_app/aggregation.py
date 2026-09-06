"""Value-conserving allocation trees shared by every chart and table."""

from collections import defaultdict
import json

import pandas as pd

from portfolio_app.taxonomy import Classifications, TaxonomyPath, is_descendant, paths_for

NODE_COLUMNS = ["node_id", "parent_id", "label", "path", "depth", "value", "percentage", "is_leaf", "kind"]
ALLOCATION_COLUMNS = ["asset_id", "asset_name", "path", "value"]


def classify_exposures(exposures: pd.DataFrame, classifications: Classifications, taxonomy: str) -> pd.DataFrame:
    records = []
    for row in exposures.itertuples(index=False):
        paths = paths_for(classifications, row.asset_id, taxonomy)
        for path in paths:
            records.append({"asset_id": row.asset_id, "asset_name": row.asset_name, "path": path, "value": row.value / len(paths)})
    return pd.DataFrame(records, columns=ALLOCATION_COLUMNS)


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
    asset_values: dict[tuple[TaxonomyPath, str, str], float] = defaultdict(float)
    root_id = _node_id("category", root)
    for row in allocations.itertuples(index=False):
        if not is_descendant(row.path, root):
            continue
        path = row.path if depth is None else row.path[: len(root) + depth]
        terminal_values[path] += row.value
        asset_values[(path, row.asset_id, row.asset_name)] += row.value
        for length in range(len(root), len(path) + 1):
            prefix = path[:length]
            node_id = _node_id("category", prefix)
            if node_id not in nodes:
                nodes[node_id] = {
                    "node_id": node_id,
                    "parent_id": "" if prefix == root else _node_id("category", prefix[:-1]),
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
        for (path, asset_id, name), value in sorted(asset_values.items()):
            node_id = _node_id("asset", path, asset_id)
            nodes[node_id] = {
                "node_id": node_id, "parent_id": _node_id("category", path),
                "label": name, "path": path, "depth": len(path) - len(root) + 1,
                "value": value, "kind": "holding",
            }
    else:
        parents = {node["parent_id"] for node in nodes.values()}
        for path, value in terminal_values.items():
            if _node_id("category", path) in parents:
                node_id = _node_id("assigned", path)
                nodes[node_id] = {
                    "node_id": node_id, "parent_id": _node_id("category", path),
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


def aggregate_dimension(exposures: pd.DataFrame, dimension: str, *, show_tickers: bool = False) -> pd.DataFrame:
    records = []
    labels = {}
    for row in exposures.to_dict("records"):
        key = row["asset_id"] if dimension == "holding" else str(row[dimension] or "Unspecified")
        symbol = str(row.get("ticker") or row["asset_id"]).upper()
        labels[key] = (f"{row['asset_name']} ({symbol})" if show_tickers else row['asset_name']) if dimension == "holding" else key
        records.append({"asset_id": row["asset_id"], "asset_name": row["asset_name"], "path": (key,), "value": row["value"]})
    nodes = build_tree(pd.DataFrame(records, columns=ALLOCATION_COLUMNS), root_label=dimension)
    # Keep node identities based on stable IDs, independently of display tickers.
    leaves = nodes["depth"] == 1
    nodes.loc[leaves, "label"] = nodes.loc[leaves, "label"].map(labels)
    nodes["path"] = nodes["path"].map(lambda path: (labels[path[0]],) if path else ())
    return nodes
