"""Strategic chart data from source positions, independent of exposure labels."""

import json
from collections import Counter

import pandas as pd

from portfolio_app.aggregation import ALLOCATION_COLUMNS, build_tree
from portfolio_app.allocation import Allocation
from portfolio_app.display_names import instrument_name
from portfolio_app.performance import summarize_performance


def bucket_paths(config: Allocation) -> dict[str, tuple[str, ...]]:
    by_id = {bucket.id: bucket for bucket in config.buckets}
    paths = {"": (), "unassigned": ("unassigned",)}
    for bucket in config.buckets:
        path = (bucket.id,)
        parent = bucket.parent
        while parent:
            path = (parent, *path)
            parent = by_id[parent].parent
        paths[bucket.id] = path
    return paths


def category_labels(config):
    paths = bucket_paths(config)
    names = {b.id: b.name for b in config.buckets}
    labels = {b.id: ' › '.join(names[key] for key in paths[b.id]) for b in config.buckets}
    used = {'Portfolio', 'Unassigned'}
    result = {}
    for key, label in labels.items():
        candidate, suffix = label, 1
        while candidate in used:
            candidate = f'{label} (category {suffix})'
            suffix += 1
        result[key] = candidate
        used.add(candidate)
    return result



def bucket_positions(valued: pd.DataFrame, config: Allocation, bucket: str = "") -> pd.DataFrame:
    if not bucket:
        return valued.copy()
    if bucket == "unassigned":
        return valued.loc[valued.bucket_id.eq("")].copy()
    return valued.loc[valued.bucket_id.isin(config.leaves(bucket))].copy()


def strategic_tree(valued: pd.DataFrame, config: Allocation, bucket: str = "") -> pd.DataFrame:
    """Chart areas use known EUR only; missing totals remain explicit metadata.

    IDs, rather than display names, define the hierarchy. Empty buckets get zero
    area, never a fabricated current allocation based on their target.
    """
    paths = bucket_paths(config)
    names = {item.id: item.name for item in config.buckets} | {"unassigned": "Unassigned"}
    records = [dict(asset_id=row.position_id, asset_name=instrument_name(row),
                    path=paths[row.bucket_id or "unassigned"],
                    value=0. if pd.isna(row.current_value_reporting) else row.current_value_reporting)
               for row in valued.itertuples()]
    # Materialize planned buckets even when they have no source positions.
    records.extend(dict(asset_id="", asset_name="", path=paths[key], value=0.)
                   for key in config.leaves() | ({"unassigned"} if valued.bucket_id.eq("").any() else set()))
    nodes = build_tree(pd.DataFrame(records, columns=ALLOCATION_COLUMNS), root=paths[bucket],
                       include_holdings=True, root_label="Portfolio")
    if nodes.empty:
        return nodes
    nodes = nodes.loc[~nodes.node_id.map(lambda key: json.loads(key)[0] == "asset" and json.loads(key)[2] == "")].copy()
    parents = set(nodes.parent_id)
    nodes["is_leaf"] = ~nodes.node_id.isin(parents)
    nodes.loc[nodes.kind.eq("category"), "label"] = nodes.loc[nodes.kind.eq("category"), "path"].map(
        lambda path: names[path[-1]] if path else "Portfolio")
    by_position = valued.set_index("position_id")
    missing = []
    for row in nodes.itertuples():
        if row.kind == "holding":
            missing.append(int(pd.isna(by_position.loc[json.loads(row.node_id)[2], "current_value_reporting"])))
        else:
            missing.append(int(bucket_positions(valued, config, row.path[-1] if row.path else "").current_value_reporting.isna().sum()))
    nodes["missing_prices"] = missing
    nodes["current_value"] = nodes.value.where(nodes.missing_prices.eq(0))
    return nodes.reset_index(drop=True)


def strategic_summary(valued: pd.DataFrame, config: Allocation, bucket: str = "") -> pd.DataFrame:
    """Disjoint immediate children; current/target columns share the view scope."""
    selected = bucket_positions(valued, config, bucket)
    total = float(selected.current_value_reporting.sum())
    complete = selected.current_value_reporting.notna().all()
    children = config.children(bucket) if bucket != "unassigned" else []
    records = []
    choices = [(item.id, item.name, item.target) for item in children]
    if not bucket and valued.bucket_id.eq("").any():
        choices.append(("unassigned", "Unassigned", None))
    if not choices:
        choices = [(row.position_id, instrument_name(row), row.within_bucket_target)
                   for row in selected.itertuples()]
        positions = True
    else:
        positions = False
    for key, name, target in choices:
        rows = selected.loc[selected.position_id.eq(key)] if positions else bucket_positions(valued, config, key)
        known = rows.current_value_reporting.notna().all()
        subtotal = float(rows.current_value_reporting.sum())
        current = 100 * subtotal / total if complete and known and total > 0 else float("nan")
        target = float(target) * 100 if pd.notna(target) else float("nan")
        records.append({"Category": name, "Value": subtotal if known else float("nan"),
                        "Current (%)": current, "Target (%)": target,
                        "Gap (pp)": current - target,
                        "Status": "Missing price" if not known else "Empty" if not len(rows) or rows.shares.eq(0).all() else ""})
    return pd.DataFrame(records, columns=["Category", "Value", "Current (%)", "Target (%)", "Gap (pp)", "Status"]).sort_values(
        ["Value", "Category"], ascending=[False, True], na_position="last", ignore_index=True)


def strategic_performance(valued, config, bucket=""):
    """Disjoint child totals from owned positions, never look-through holdings."""
    selected = bucket_positions(valued, config, bucket)
    children = config.children(bucket) if bucket != "unassigned" else []
    choices = [(b.name, bucket_positions(selected, config, b.id)) for b in children]
    if not bucket and selected.bucket_id.eq("").any():
        choices.append(("Unassigned", bucket_positions(selected, config, "unassigned")))
    if not choices:
        labels = [instrument_name(row) for row in selected.itertuples()]
        counts = Counter(labels)
        choices = [(label + (' · ' + (getattr(row, 'account', '') or getattr(row, 'portfolio', '') or f'Position {index + 1}')
                            if counts[label] > 1 else ''), selected.loc[selected.position_id.eq(row.position_id)])
                   for index, (label, row) in enumerate(zip(labels, selected.itertuples()))]
    # Plotly uses category labels as coordinates. Never place distinct positions
    # or same-named sibling categories on top of each other.
    counts = Counter(name for name, _ in choices)
    choices = [(name + (f' ({index + 1})' if counts[name] > 1 else ''), positions)
               for index, (name, positions) in enumerate(choices)]
    rows = []
    for name, positions in choices:
        summary = summarize_performance(positions)
        rows.append({"Category": name, "Cost": summary.cost_reporting,
                     "Gain": summary.gain_reporting, "Return (%)": summary.return_pct,
                     "Coverage": f"{summary.covered_count} of {summary.held_count}",
                     "Status": ("Complete" if summary.covered_count == summary.held_count and summary.held_count else
                               "Partial" if summary.covered_count else "Unavailable") + (" · Estimated" if summary.estimated_count else "")})
    result = pd.DataFrame(rows, columns=["Category", "Cost", "Gain", "Return (%)", "Coverage", "Status"])
    for column in ("Cost", "Gain", "Return (%)"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result
