"""Strategic chart data from source positions, independent of exposure labels."""

import json
from collections import Counter

import pandas as pd

from portfolio_app.exposure.aggregation import ALLOCATION_COLUMNS, build_tree
from portfolio_app.portfolio.allocation import Allocation
from portfolio_app.interface.shared.display_names import instrument_name
from portfolio_app.performance.performance import summarize_performance


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
    return valued.loc[valued.bucket_id.isin(config.descendants(bucket))].copy()


def strategic_tree(valued: pd.DataFrame, config: Allocation, bucket: str = "", *, flat: bool = False) -> pd.DataFrame:
    """Chart areas use known reporting-currency values only; missing totals remain explicit metadata.

    IDs, rather than display names, define the hierarchy. Empty buckets get zero
    area, never a fabricated current allocation based on their target.
    """
    paths = bucket_paths(config)
    names = {item.id: item.name for item in config.buckets} | {"unassigned": "Unassigned"}
    if flat:
        valued = bucket_positions(valued, config, bucket)
    missing_positions = set()
    missing_by_category = Counter()
    records = []
    for row in valued.itertuples():
        path = paths[bucket] if flat else paths[row.bucket_id or "unassigned"]
        value = row.current_value_reporting
        if pd.isna(value):
            missing_positions.add(row.position_id)
            missing_by_category.update(path)
            missing_by_category[""] += 1
            value = 0.
        records.append(dict(asset_id=row.position_id, asset_name=instrument_name(row), path=path, value=value))
    # Materialize planned buckets even when they have no source positions.
    records.extend(dict(asset_id="", asset_name="", path=paths[key], value=0.)
                   for key in (() if flat else config.leaves() | ({"unassigned"} if valued.bucket_id.eq("").any() else set())))
    nodes = build_tree(pd.DataFrame(records, columns=ALLOCATION_COLUMNS), root=paths[bucket],
                       include_holdings=True, root_label="Portfolio")
    if nodes.empty:
        return nodes
    holding = nodes.kind.eq("holding")
    position_ids = nodes.loc[holding, "node_id"].map(lambda key: json.loads(key)[2])
    nodes = nodes.drop(index=position_ids.index[position_ids.eq("")])
    parents = set(nodes.parent_id)
    nodes["is_leaf"] = ~nodes.node_id.isin(parents)
    nodes.loc[nodes.kind.eq("category"), "label"] = nodes.loc[nodes.kind.eq("category"), "path"].map(
        lambda path: names[path[-1]] if path else "Portfolio")
    nodes["missing_prices"] = [int(position_ids.at[index] in missing_positions) if kind == "holding"
                               else missing_by_category[path[-1] if path else ""]
                               for index, kind, path in zip(nodes.index, nodes.kind, nodes.path)]
    nodes["current_value"] = nodes.value.where(nodes.missing_prices.eq(0))
    return nodes.reset_index(drop=True)


def strategic_summary(valued: pd.DataFrame, config: Allocation, bucket: str = "", *, known_exposure=None, flat=False, hierarchy=False, identities=False) -> pd.DataFrame:
    """Scope-relative allocation rows, optionally including expandable descendants.

    Hierarchical rows overlap: only rows with no parent form the scope total.
    Every descendant keeps the selected scope's percentage denominator.
    """
    selected = bucket_positions(valued, config, bucket)
    total = float(selected.current_value_reporting.sum())
    complete = selected.current_value_reporting.notna().all()
    choices = []
    paths = bucket_paths(config)
    members = {}
    if hierarchy and not flat:
        # Index membership once; avoid rescanning all positions per category.
        for index, owner in enumerate(selected.bucket_id):
            for ancestor in paths[owner or 'unassigned']:
                members.setdefault(ancestor, []).append(index)
    if not flat:
        categories = [item for item in config.buckets if item.id != bucket and
                      (not bucket or bucket in paths[item.id])] if hierarchy else config.children(bucket)
        choices.extend((item.id, item.name, config.relative_target(item.id, bucket),
                        selected.iloc[members.get(item.id, [])] if hierarchy else bucket_positions(selected, config, item.id))
                       for item in categories if bucket != 'unassigned')
        if not bucket and valued.loc[valued.bucket_id.eq(''), 'current_value_reporting'].ne(0).any():
            choices.append(('unassigned', 'Unassigned', None, bucket_positions(selected, config, 'unassigned')))
    records = []
    def add_row(key, name, target, value, empty, parent, *, owner='', account='', exposure=None):
        known = pd.notna(value)
        current = 100 * value / total if complete and known and total > 0 else float('nan')
        target = float(target) * 100 if pd.notna(target) else float('nan')
        record = {'Category': name, 'Value': value, 'Current (%)': current, 'Target (%)': target,
                  'Gap (pp)': current - target, 'Status': 'Missing price' if not known else 'Empty' if empty else ''}
        if flat:
            record['Assigned category'] = owner
        if flat or hierarchy:
            record['Account'] = account
        if hierarchy or identities:
            record.update(row_id=('position:' if owner else 'category:') + key,
                          parent_id='' if flat or parent in {'', bucket} else 'category:' + parent,
                          position_id=key if owner else '', category_id='' if owner else key)
        if known_exposure is not None:
            record['Known exposure'] = exposure
        records.append(record)

    parents = {item.id: item.parent for item in config.buckets}
    for key, name, target, rows in choices:
        value = float(rows.current_value_reporting.sum()) if rows.current_value_reporting.notna().all() else float('nan')
        exposure = None
        if known_exposure is not None:
            exposure = rows.position_id.map(known_exposure).sum(min_count=1) if len(rows) else 0.
        add_row(key, name, target, value, rows.shares.eq(0).all(), parents.get(key, ''), exposure=exposure)

    direct = selected if flat or hierarchy or bucket == 'unassigned' else selected.loc[selected.bucket_id.eq(bucket)] if bucket else selected.iloc[:0]
    labels = category_labels(config)
    exposures = direct.position_id.map(known_exposure).tolist() if known_exposure is not None else [None] * len(direct)
    empty = direct.shares.eq(0).fillna(True)
    for row, exposure, is_empty in zip(direct.itertuples(), exposures, empty):
        target = row.within_bucket_target if row.bucket_id and not config.flexible(row.bucket_id) else float('nan')
        if (flat or hierarchy) and row.bucket_id:
            target *= config.relative_target(row.bucket_id, bucket)
        owner = labels.get(row.bucket_id, 'Unassigned')
        if bucket and row.bucket_id != bucket:
            owner = owner.removeprefix(labels.get(bucket, '') + ' › ')
        value = float(row.current_value_reporting) if pd.notna(row.current_value_reporting) else float('nan')
        add_row(row.position_id, instrument_name(row), target, value, is_empty, row.bucket_id or 'unassigned',
                owner=owner, account=getattr(row, 'account', ''), exposure=exposure)
    return pd.DataFrame(records, columns=["Category", *(["Assigned category", "Account"] if flat else ['Account'] if hierarchy else []), "Value", *(['Known exposure'] if known_exposure is not None else []), "Current (%)", "Target (%)", "Gap (pp)", "Status", *(['row_id', 'parent_id', 'position_id', 'category_id'] if hierarchy or identities else [])]).sort_values(
        ["Value", "Category"], ascending=[False, True], na_position="last", ignore_index=True)


def strategic_performance(valued, config, bucket="", *, evaluate=None):
    """Disjoint child totals from owned positions, never look-through holdings."""
    selected = bucket_positions(valued, config, bucket)
    children = config.children(bucket) if bucket != "unassigned" else []
    choices = [(b.name, bucket_positions(selected, config, b.id), b.id) for b in children]
    if not bucket and selected.bucket_id.eq("").any():
        choices.append(("Unassigned", bucket_positions(selected, config, "unassigned"), 'unassigned'))
    direct = selected if bucket == 'unassigned' else selected.loc[selected.bucket_id.eq(bucket)] if bucket else selected.iloc[:0]
    if not direct.empty:
        labels = [instrument_name(row) for row in direct.itertuples()]
        counts = Counter(labels)
        choices += [(label + (' · ' + (getattr(row, 'account', '') or getattr(row, 'portfolio', '') or f'Position {index + 1}')
                            if counts[label] > 1 else ''), selected.loc[selected.position_id.eq(row.position_id)], None)
                   for index, (label, row) in enumerate(zip(labels, direct.itertuples()))]
    # Plotly uses category labels as coordinates. Never place distinct positions
    # or same-named sibling categories on top of each other.
    counts = Counter(name for name, _, _ in choices)
    choices = [(name + (f' ({index + 1})' if counts[name] > 1 else ''), positions, category)
               for index, (name, positions, category) in enumerate(choices)]
    rows, freshness, freshness_members = [], {}, {}
    for name, positions, category in choices:
        from portfolio_app.derivatives.futures_valuation import summarize_freshness
        freshness[name] = summarize_freshness(positions)
        freshness_members[name] = tuple(positions.position_id)
        if evaluate is not None:
            gain, twr = evaluate(positions, category)
            rows.append({'Category':name, 'Cost':gain.capital, 'Gain':gain.amount,
                         'Return (%)':100*twr if twr is not None else None,
                         'Coverage':gain.coverage, 'Status':gain.status})
            continue
        summary = summarize_performance(positions)
        rows.append({"Category": name, "Cost": summary.cost_reporting,
                     "Gain": summary.gain_reporting, "Return (%)": summary.return_pct,
                     "Coverage": summary.coverage,
                     "Status": ("N/A · Cash balance" if summary.cash_only else "Complete" if summary.covered_count == summary.eligible_count and summary.eligible_count else
                               "Partial" if summary.covered_count else "Unavailable") + (" · Estimated" if summary.estimated_count else "")})
    result = pd.DataFrame(rows, columns=["Category", "Cost", "Gain", "Return (%)", "Coverage", "Status"])
    for column in ("Cost", "Gain", "Return (%)"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result.attrs['valuation_freshness'] = freshness
    result.attrs['valuation_members'] = freshness_members
    return result
