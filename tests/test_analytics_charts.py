"""Compact correlation display preserves undefined cells and instrument identity."""
import numpy as np
import pandas as pd

from portfolio_app.charts import correlation_chart


def test_compact_correlations_keep_full_hover_names_and_undefined_cash():
    matrix = pd.DataFrame([[1., np.nan], [np.nan, np.nan]], index=['a', 'cash'], columns=['a', 'cash'])
    names = {'a': 'An explicitly invented and unusually long investment name', 'cash': 'Invented cash'}
    figure = correlation_chart(matrix, names, compact=True)
    assert figure.layout.height == 420
    assert figure.layout.xaxis.ticktext[0].endswith('…')
    assert figure.data[0].customdata[0][1] == [names['a'], names['cash']]
    assert len(figure.layout.annotations) == 1
    assert figure.layout.annotations[0].text == '1.00'
    assert np.isnan(figure.data[0].z[1][1])
    expanded = correlation_chart(matrix, names)
    assert expanded.layout.height >= 640
    assert expanded.layout.xaxis.ticktext[0] == names['a']
