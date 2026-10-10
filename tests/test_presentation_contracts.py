"""Currency and chart presentation contracts using invented amounts only."""
from types import SimpleNamespace

import pandas as pd
import pytest

from portfolio_app import currency_display
from portfolio_app.charts import bar_chart, chart_config, hierarchy_chart, pie_chart
from portfolio_app.money import euros, format_money


@pytest.mark.parametrize('currency,symbol', [('EUR', '€'), ('USD', '$'), ('GBP', '£')])
def test_reporting_currency_reaches_text_native_grids_and_chart_hovers(monkeypatch, currency, symbol):
    monkeypatch.setattr(currency_display, 'st', SimpleNamespace(session_state={'reporting_currency': currency}))
    assert currency_display.money(1234.5) == f'{symbol}1,234.50'
    assert currency_display.money(-1234.5, signed=True) == f'-{symbol}1,234.50'
    assert currency_display.money(12, signed=True) == f'+{symbol}12.00'
    assert currency_display.money_format() == f'{symbol} %.2f'
    assert currency_display.money_format(signed=True) == f'{symbol} %+.2f'
    assert euros(-1234.5) == '€-1,234.50'
    assert currency_display.money(12, currency='EUR') == '€12.00'
    nodes = pd.DataFrame([
        dict(node_id='root', parent_id='', label='Invented total', value=1234.5, percentage=1.,
             depth=0, is_leaf=False, path=(), kind='root'),
        dict(node_id='leaf', parent_id='root', label='Invented leaf', value=1234.5, percentage=1.,
             depth=1, is_leaf=True, path=('Invented leaf',), kind='category'),
    ])
    for figure in (hierarchy_chart(nodes, 'Sunburst'), bar_chart(nodes), pie_chart(nodes)):
        assert symbol + '%{' in figure.data[0].hovertemplate
        assert figure.data[0].customdata[0][0] == 1.


@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), -float('inf')])
def test_missing_amounts_never_render_as_zero(value):
    assert format_money(value, 'USD') == '—'
    assert format_money(value, 'GBP', missing='Unavailable') == 'Unavailable'
    assert euros(value) == '—'
    assert format_money(0, 'EUR') == '€0.00'


def test_chart_modes_keep_allocation_compact_and_studies_interactive():
    assert chart_config()['displayModeBar'] is False
    assert chart_config(toolbar='hover')['displayModeBar'] == 'hover'
    config = chart_config(zoom=True)
    assert config['displayModeBar'] is True
    assert config['modeBarButtons'] == [['zoom2d', 'pan2d', 'resetScale2d']]
    assert config['doubleClick'] == 'reset'
    for mode in (chart_config(), config):
        assert mode['showSendToCloud'] is False and mode['displaylogo'] is False
    config['modeBarButtons'][0].clear()
    assert chart_config(zoom=True)['modeBarButtons'][0] == ['zoom2d', 'pan2d', 'resetScale2d']
