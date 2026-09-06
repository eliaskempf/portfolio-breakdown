"""Shared colors and read-only badges for taxonomy labels."""

import streamlit as st

from portfolio_app.charts import SUNBURST_PALETTE
from portfolio_app.taxonomy import Classifications, UNCLASSIFIED, branches, paths_for


def taxonomy_colors(classifications: Classifications, taxonomy: str) -> dict[str, str]:
    """Assign colors from the complete taxonomy, independent of value or filters."""
    paths = set(branches(classifications, list(classifications), taxonomy)) | {UNCLASSIFIED}
    roots = sorted({path[0] for path in paths if path != UNCLASSIFIED})
    colors = {root: SUNBURST_PALETTE[index % len(SUNBURST_PALETTE)] for index, root in enumerate(roots)}
    colors[UNCLASSIFIED[0]] = "#7b8493"
    return {" › ".join(path): colors[path[0]] for path in sorted(paths)}


def asset_badges(classifications: Classifications, asset_id: str, taxonomy: str) -> list[str]:
    return sorted({" › ".join(path) for path in paths_for(classifications, asset_id, taxonomy)})


def badge_column(label: str, colors: dict[str, str]):
    return st.column_config.MultiselectColumn(label, options=list(colors), color=list(colors.values()), disabled=True)


def color_label_chart(figure, nodes, colors: dict[str, str], *, detail_color: str | None = None) -> None:
    """Use the table's category colors in every comparison chart type."""
    kind = figure.data[0].type
    rows = nodes if kind in {"sunburst", "treemap"} else nodes.loc[nodes["is_leaf"]]
    if kind == "bar":
        rows = rows.sort_values("value", ascending=True)
    assigned = [colors.get(row.path[0], detail_color or "#f0f2f8") if row.path else "#f0f2f8"
                for row in rows.itertuples()]
    if kind == "bar":
        figure.update_traces(marker_color=assigned)
    else:
        figure.update_traces(marker_colors=assigned)
