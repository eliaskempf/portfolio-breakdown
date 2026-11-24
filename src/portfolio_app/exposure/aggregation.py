"""Value-conserving allocation trees shared by every chart and table."""

from collections import defaultdict
from functools import cache
from itertools import repeat
import json

import pandas as pd

from portfolio_app.exposure.taxonomy import Classifications, TaxonomyPath, is_descendant, paths_for

NODE_COLUMNS = ["node_id", "parent_id", "label", "path", "depth", "value", "percentage", "is_leaf", "kind"]
ALLOCATION_COLUMNS = ["asset_id", "asset_name", "path", "value"]


def _classified_rows(exposures, classifications, taxonomy, *, include_names):
    if exposures.empty:
        return
    names = exposures.asset_name if include_names else repeat(None)
    for asset, name, value in zip(exposures.asset_id, names, exposures.value):
        paths = paths_for(classifications, asset, taxonomy)
        share = value / len(paths)
        for path in paths:
            yield asset, name, path, share


def classify_exposures(exposures: pd.DataFrame, classifications: Classifications, taxonomy: str) -> pd.DataFrame:
    rows = _classified_rows(exposures, classifications, taxonomy, include_names=True)
    return pd.DataFrame.from_records(rows, columns=ALLOCATION_COLUMNS)


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
    rows = ()
    if not allocations.empty:
        assets = allocations.asset_id if include_holdings else repeat(None)
        names = allocations.asset_name if include_holdings else repeat(None)
        rows = zip(assets, names, allocations.path, allocations.value)
    return _tree_from_rows(rows, root=root, depth=depth, include_holdings=include_holdings, root_label=root_label)


def _tree_from_rows(rows, *, root, depth, include_holdings, root_label):
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
    for asset, name, source_path, value in rows:
        if not is_descendant(source_path, root):
            continue
        path = source_path if depth is None else source_path[: len(root) + depth]
        if include_holdings:
            asset_values[(path, asset)] += value
            asset_names[asset] = min(asset_names.get(asset, name), name)
        else:
            terminal_values[path] += value
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
            nodes[node_id]["value"] += value
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
    rows = _classified_rows(exposures, classifications, taxonomy, include_names=include_holdings)
    return _tree_from_rows(rows, root=root, depth=depth, include_holdings=include_holdings, root_label=taxonomy)


def taxonomy_measure(exposures: pd.DataFrame, classifications: Classifications, *, taxonomy: str,
                     root: TaxonomyPath = (), depth: int | None = None, include_holdings: bool = False) -> pd.DataFrame:
    """Return secondary amounts for alignment to the displayed allocation nodes."""
    if not exposures.empty:
        active = exposures.value.ne(0).fillna(True)
        if not active.all():
            if not include_holdings and active.any():
                # Even a zero-only descendant can make its parent's direct amount an
                # 'Assigned here' leaf. One occurrence per asset retains every path.
                active |= ~exposures.asset_id.duplicated()
            columns = ["asset_id", "asset_name", "value"] if include_holdings else ["asset_id", "value"]
            exposures = exposures.loc[active, columns]
    nodes = aggregate(exposures, classifications, taxonomy=taxonomy, root=root, depth=depth,
                      include_holdings=include_holdings)
    return nodes.loc[:, ["node_id", "value"]]


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
