"""Rendering adapters; all portfolio math is already present in the node table."""

from html import escape
from textwrap import wrap
import colorsys

import plotly.graph_objects as go
import pandas as pd
from portfolio_app.settings import PRIMARY_COLOR


def sort_allocation_nodes(nodes: pd.DataFrame) -> pd.DataFrame:
    """Largest allocations first among siblings, retaining the parent/child tree."""
    if nodes.empty:
        return nodes.copy()
    ordered = nodes.sort_values(["percentage", "label", "node_id"], ascending=[False, True, True], kind="stable", na_position="last")
    children = {parent: rows.node_id.tolist() for parent, rows in ordered.groupby("parent_id", sort=False)}
    pending = list(reversed(children.get("", [])))
    traversal = []
    while pending:
        node = pending.pop()
        traversal.append(node)
        pending.extend(reversed(children.get(node, [])))
    return nodes.set_index("node_id", drop=False).loc[traversal].reset_index(drop=True)

PALETTE = ["#377f66", "#6da995", "#a9cabc", "#d5b982", "#617f99", "#9cb3c5", "#bb8c7a", "#7a8d71"]
SUNBURST_PALETTE = [PRIMARY_COLOR, "#9a6dd7", "#e5a04b", "#4aa9b3", "#d76e91", "#75a565", "#c57b55", "#7b91b3"]


def style_figure(figure: go.Figure) -> go.Figure:
    return figure.update_layout(colorway=PALETTE, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                font=dict(family="Inter, system-ui, sans-serif", size=12),
                                hoverlabel=dict(bgcolor="#202632", font_color="white"))


def correlation_chart(correlations: pd.DataFrame, names: dict[str, str]) -> go.Figure:
    """A full-width matrix with display names and distinct cells for equal names."""
    rows = [escape(names.get(key, key)) for key in correlations.index]
    columns = [escape(names.get(key, key)) for key in correlations.columns]
    figure = style_figure(go.Figure(go.Heatmap(
        z=correlations.to_numpy(), x=list(range(len(columns))), y=list(range(len(rows))),
        zmin=-1, zmax=1, zmid=0,
        colorscale=[[0, '#b8a0dd'], [.25, '#76628f'], [.5, '#303744'],
                    [.75, '#398b87'], [1, '#80d3c5']],
        customdata=[[[row, column] for column in columns] for row in rows],
        hovertemplate='%{customdata[0]}<br>%{customdata[1]}<br>Correlation: %{z:.2f}<extra></extra>',
        hoverongaps=False,
        colorbar=dict(x=1.01, xanchor='left', xpad=0, thickness=14, len=.9,
                      outlinewidth=0, tickvals=[-1, -.5, 0, .5, 1], title='Correlation'),
    )))
    figure.update_xaxes(tickmode='array', tickvals=list(range(len(columns))), ticktext=columns,
                        tickangle=-40, automargin=True, showgrid=False, zeroline=False)
    figure.update_yaxes(tickmode='array', tickvals=list(range(len(rows))), ticktext=rows,
                        autorange='reversed', automargin=True, showgrid=False, zeroline=False)
    figure.update_layout(height=max(640, min(1000, 36 * len(rows) + 220)),
                         margin=dict(l=12, r=20, t=24, b=12))
    return figure


def hierarchy_chart(nodes: pd.DataFrame, chart_type: str) -> go.Figure:
    trace_class = go.Treemap if chart_type == "Treemap" else go.Sunburst
    figure = style_figure(go.Figure(trace_class(
        ids=nodes["node_id"], parents=nodes["parent_id"], labels=nodes["label"],
        values=nodes["value"], branchvalues="total", sort=False,
        customdata=nodes[["percentage"]].to_numpy(),
        hovertemplate="%{label}<br>€%{value:,.2f}<br>%{customdata[0]:.2%} of displayed root<extra></extra>",
        marker=dict(line=dict(color="rgba(127,127,127,0.7)", width=2)),
    )).update_layout(margin=dict(t=24, l=24, r=24, b=24), height=480, treemapcolorway=PALETTE))
    if chart_type == "Sunburst":
        # Keep the actual areas and hover data; suppress only tiny labels.
        text = ["<br>".join(escape(line) for line in wrap(row.label, width=18, max_lines=3, placeholder="…"))
                if row.percentage >= .01 or row.parent_id == '' else ''
                for row in nodes.itertuples()]
        figure.update_traces(text=text, textinfo="text", insidetextorientation="radial", root=dict(color="rgba(127,127,127,0.12)"))
        figure.update_layout(height=640, sunburstcolorway=SUNBURST_PALETTE,
                             uniformtext=dict(minsize=11, mode="hide"))
    return figure


def strategic_colors(nodes, config):
    """Stable identity-based colours, with distinct shades for siblings."""
    top = sorted(b.id for b in config.children())
    palettes = {key: SUNBURST_PALETTE[i % len(SUNBURST_PALETTE)] for i, key in enumerate(top)}
    sibling_indices = {node: (i, len(group)) for _, group in nodes.groupby('parent_id')
                       for i, node in enumerate(sorted(group.node_id))}
    colors = []
    for row in nodes.itertuples():
        if not row.path:
            colors.append('rgba(127,127,127,0.12)')
            continue
        base = palettes.get(row.path[0], '#7b8493')
        rgb = tuple(int(base[i:i+2], 16) / 255 for i in (1, 3, 5))
        hue, light, saturation = colorsys.rgb_to_hls(*rgb)
        if len(row.path) > 1 or row.kind == 'holding':
            index, count = sibling_indices[row.node_id]
            fraction = index / max(1, count - 1)
            hue = (hue + (fraction - .5) * .09) % 1
            light = .36 + fraction * .34
        rgb = colorsys.hls_to_rgb(hue, light, saturation)
        colors.append('#' + ''.join(f'{round(v * 255):02x}' for v in rgb))
    return colors


def bar_chart(nodes: pd.DataFrame) -> go.Figure:
    leaves = nodes.loc[nodes["is_leaf"]].sort_values("value", ascending=True)
    labels = [" > ".join(row.path) + (" > Assigned here" if row.kind == "assigned" else "") for row in leaves.itertuples()]
    return style_figure(go.Figure(go.Bar(
        x=leaves["value"], y=labels, orientation="h",
        customdata=leaves[["percentage"]].to_numpy(),
        hovertemplate="%{y}<br>€%{x:,.2f}<br>%{customdata[0]:.2%}<extra></extra>",
        text=[f"{value:.1%}" for value in leaves["percentage"]], textposition="auto",
    )).update_layout(xaxis_title="EUR", margin=dict(t=15, b=0), height=max(350, 32 * len(leaves))))


def pie_chart(nodes: pd.DataFrame) -> go.Figure:
    # Only disjoint leaf buckets belong in a pie; parent totals would double-count.
    leaves = nodes.loc[nodes["is_leaf"]]
    labels = [" > ".join(row.path) + (" > Assigned here" if row.kind == "assigned" else "") for row in leaves.itertuples()]
    return style_figure(go.Figure(go.Pie(
        ids=leaves["node_id"], labels=labels, values=leaves["value"],
        customdata=leaves[["percentage"]].to_numpy(),
        textinfo="percent", hole=.52, marker=dict(colors=PALETTE, line=dict(color="rgba(127,127,127,0.25)", width=1)),
        hovertemplate="%{label}<br>€%{value:,.2f}<br>%{customdata[0]:.2%} of displayed root<extra></extra>",
    )).update_layout(margin=dict(t=20, l=15, r=15, b=20), height=480, legend=dict(orientation="h", y=-.08)))


def hierarchy_table(nodes: pd.DataFrame, *, show_paths: bool = False) -> pd.DataFrame:
    # The root is a total, not another allocation. Render it separately so table
    # sorting can never mix it into holdings or category rows.
    ordered = sort_allocation_nodes(nodes)
    rows = ordered.loc[ordered["parent_id"] != ""]
    table = pd.DataFrame({
        "Category": ["　" * max(0, row.depth - 1) + row.label for row in rows.itertuples()],
        "EUR value": rows["value"].to_numpy(),
        "Allocation %": (100 * rows["percentage"]).to_numpy(),
    })
    if show_paths:
        table.insert(1, "Classification path", [" › ".join(path) for path in rows["path"]])
    return table
