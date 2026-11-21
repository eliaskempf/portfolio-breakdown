"""Change presentation retains numeric values, signs, zero and unknowns."""
import pandas as pd
import pytest
from portfolio_app.interface.shared.number_format import change_number, return_percent, change_columns


@pytest.mark.parametrize('value,expected', [(12.5,'▲ 12.50%'),(-12.5,'▼ 12.50%'),(0,'0.00%'),(-0.,'0.00%'),(None,'—'),(float('nan'),'—'),(float('inf'),'—')])
def test_return_direction(value,expected):
    assert return_percent(value) == expected


def test_grid_formatting_keeps_numeric_sorting_and_ordinary_percentages():
    frame=pd.DataFrame({'Performance':[10.,-2.,0.], 'Allocation %':[10.,20.,70.], 'Gap (pp)':[-1.,0.,1.]})
    styled=change_columns(frame,percent=['Performance'],points=['Gap (pp)'])
    pd.testing.assert_frame_equal(styled.data,frame)
    assert styled._display_funcs[(1,0)](-2.) == '▼ 2.00%'
    assert styled._display_funcs[(0,2)](-1.) == '▼ 1.00'
    assert change_number(2,suffix=' pp') == '▲ 2.00 pp'
