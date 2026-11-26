"""Compact correlation display preserves undefined cells and instrument identity."""
import numpy as np
import pandas as pd

from portfolio_app.interface.shared.charts import correlation_chart


def test_compact_correlations_keep_full_hover_names_and_undefined_cash():
    matrix = pd.DataFrame([[1., np.nan], [np.nan, np.nan]], index=['a', 'cash'], columns=['a', 'cash'])
    names = {'a': 'An explicitly invented and unusually long investment name', 'cash': 'Invented cash'}
    figure = correlation_chart(matrix, names, compact=True)
    assert figure.layout.height == 420
    assert figure.layout.xaxis.ticktext[0].endswith('…')
    assert figure.data[0].customdata is None
    assert figure.layout.yaxis.labelalias[figure.data[0].y[0]] == names['a']
    assert figure.layout.xaxis.labelalias[figure.data[0].x[1]] == names['cash']
    assert len(figure.layout.annotations) == 1
    assert figure.layout.annotations[0].text == '1.00'
    assert np.isnan(figure.data[0].z[1][1])
    expanded = correlation_chart(matrix, names)
    assert expanded.layout.height >= 640
    assert expanded.layout.xaxis.ticktext[0] == names['a']


def test_correlation_axis_labels_preserve_duplicate_names_and_matrix_order():
    ids = ['one', 'two', 'three']
    names = {'one': 'Invented & shared', 'two': 'Invented & shared', 'three': 'Third'}
    matrix = pd.DataFrame(np.eye(3), index=ids, columns=ids[::-1])
    figure = correlation_chart(matrix, names)
    trace = figure.data[0]
    assert figure.layout.xaxis.type == figure.layout.yaxis.type == 'category'
    np.testing.assert_array_equal(trace.z, matrix.to_numpy())
    assert len(set(trace.x)) == len(set(trace.y)) == 3
    assert [figure.layout.xaxis.labelalias[key] for key in trace.x] == [
        'Third', 'Invented &amp; shared', 'Invented &amp; shared']
    assert [figure.layout.yaxis.labelalias[key] for key in trace.y] == [
        'Invented &amp; shared', 'Invented &amp; shared', 'Third']
