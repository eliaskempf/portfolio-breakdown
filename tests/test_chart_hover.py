"""Shared inspection formats synthetic values without changing financial data."""
import plotly.graph_objects as go
from portfolio_app.chart_hover import chart_hover


def test_lines_share_date_cursor_without_interpolation_or_rounding_data():
    figure = go.Figure()
    figure.add_scatter(x=['2024-01-01','2024-01-02','2024-01-03'], y=[1234.56789,None,1500], name='Value', mode='lines', connectgaps=False)
    figure.add_scatter(x=['2024-01-01','2024-01-02','2024-01-03'], y=[1000,1000,1000], name='Capital', mode='lines', line=dict(dash='dash'))
    figure.update_layout(yaxis_title='EUR')
    chart_hover(figure)
    assert figure.layout.hovermode == 'x unified' and figure.layout.hoverdistance == -1
    assert figure.layout.xaxis.spikemode == 'across'
    assert figure.layout.xaxis.unifiedhovertitle.text == '%{x|%d %b %Y}'
    assert figure.data[0].y == (1234.56789,None,1500)
    assert figure.data[0].connectgaps is False
    assert 'Value: %{y:,.2f} EUR' in figure.data[0].hovertemplate
    assert figure.data[1].line.dash == 'dash'
    assert '%{_xother}' in figure.data[0].hovertemplate
    template = figure.data[0].hovertemplate
    assert chart_hover(figure).data[0].hovertemplate == template


def test_category_tooltips_and_hidden_bands_preserve_their_meaning():
    figure=go.Figure(go.Bar(x=[1234.567],y=['Invented'],orientation='h'))
    figure.update_layout(xaxis_title='EUR')
    chart_hover(figure)
    assert figure.layout.hovermode is None
    assert figure.data[0].hovertemplate == '%{y}<br>%{x:,.2f} EUR<extra></extra>'
    custom=go.Figure(go.Heatmap(z=[[1]],hovertemplate='Correlation: %{z:.2f}<extra></extra>'))
    assert chart_hover(custom).data[0].hovertemplate == custom.data[0].hovertemplate
    band=go.Figure()
    band.add_scatter(x=[1,2],y=[10,12],mode='lines',line=dict(width=0),showlegend=False)
    band.add_scatter(x=[1,2],y=[11,13],mode='lines',hovertemplate='Known: %{y:.1f}%<extra></extra>')
    chart_hover(band)
    assert band.data[0].hoverinfo == 'skip'
    assert band.data[1].hovertemplate == 'Known: %{y:.1f}%<extra></extra>'


def test_numeric_scatter_keeps_point_inspection():
    figure=chart_hover(go.Figure(go.Scatter(x=[.1,.2],y=[1,2],mode='markers',name='Risk')))
    assert figure.layout.hovermode is None
    assert figure.data[0].hovertemplate == '%{x}<br>Risk: %{y:,.2f}<extra></extra>'
