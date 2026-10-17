"""Consistent Plotly inspection, without changing plotted values or navigation."""
from datetime import date, datetime
from html import escape
import re


def _dated(values):
    return any(isinstance(value, (date, datetime)) or
               bool(re.match(r'^\d{4}-\d\d-\d\d', str(value))) for value in list(values)[:3])


def _suffix(title):
    if '%' in title:
        return '%'
    if '€' in title:
        return ' EUR'
    for currency in ('EUR', 'USD', 'GBP', 'CHF', 'JPY'):
        if currency in title:
            return ' ' + currency
    return ''


def chart_hover(figure):
    """Inspect curves anywhere along x; retain purpose-built categorical tooltips.

    Shared hover snaps to observed points, never interpolates financial values.
    Explicit templates and intentionally hidden uncertainty bands are preserved.
    """
    scatter = [t for t in figure.data if t.type in ('scatter', 'scattergl')]
    unified = any('lines' in (t.mode or '') or _dated(t.x if t.x is not None else []) for t in scatter)
    if unified:
        figure.update_layout(hovermode='x unified', hoverdistance=-1, spikedistance=-1)
        figure.update_xaxes(showspikes=True, spikemode='across', spikesnap='data',
                            spikedash='dot', spikethickness=1, spikecolor='#87919f')
        figure.update_yaxes(showspikes=False)
    for trace in figure.data:
        if trace.type not in ('scatter', 'scattergl', 'bar'):
            continue
        xaxis = figure.layout[getattr(trace, 'xaxis', None).replace('x', 'xaxis', 1) if getattr(trace, 'xaxis', None) else 'xaxis']
        yaxis = figure.layout[getattr(trace, 'yaxis', None).replace('y', 'yaxis', 1) if getattr(trace, 'yaxis', None) else 'yaxis']
        dates = _dated(trace.x if trace.x is not None else [])
        if dates:
            xaxis.update(hoverformat='%d %b %Y', unifiedhovertitle=dict(text='%{x|%d %b %Y}'))
            trace.xhoverformat = '%d %b %Y'
        elif unified:
            xaxis.hoverformat = ',.2f'
        if trace.hovertemplate or trace.hoverinfo in ('skip', 'none'):
            if unified and dates and trace.hovertemplate and '%{x' not in trace.hovertemplate and '%{_xother}' not in trace.hovertemplate:
                trace.hovertemplate = trace.hovertemplate.replace('<extra>', '%{_xother}<extra>')
            continue
        if trace.type in ('scatter', 'scattergl') and trace.showlegend is False and trace.line.width == 0:
            trace.hoverinfo = 'skip'
            continue
        if trace.type == 'bar' and trace.orientation == 'h':
            trace.hovertemplate = '%{y}<br>%{x:,.2f}' + _suffix(xaxis.title.text or '') + '<extra></extra>'
        else:
            label = escape(trace.name or yaxis.title.text or 'Value')
            value = '%{y:,.2f}' + _suffix(yaxis.title.text or '')
            trace.hovertemplate = (label + ': ' + value + '%{_xother}' if unified else '%{x}<br>' + label + ': ' + value) + '<extra></extra>'
    return figure
