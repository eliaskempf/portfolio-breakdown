import pandas as pd
import pytest

from test_etf_selection import multi_fund_workspace as multi_fund_workspace, effective
from test_ui import launch, by_label, selected_value
from list_helpers import list_data


def test_default_exposure_is_all_assets_without_chart_or_label_requirement(multi_fund_workspace):
    app = launch(multi_fund_workspace)
    assert not app.exception
    assert by_label(app.get('button_group'), 'Exposure view').value == 'Assets'
    assert by_label(app.toggle, 'Break down ETFs').value
    table = effective(app)
    assert table['Total (EUR)'].sum() == 300
    assert table['Allocation %'].sum() == pytest.approx(100)
    assert table.loc[table.Asset.eq('Invented Alpha'), 'Total (EUR)'].tolist() == [175]
    assert len(app.tabs[1].get('plotly_chart')) == 0
    assert len(app.tabs[1].metric) == 0
    assert list_data(app, 'Exposure assets')['maxHeight'] == 620
    assert 'Direct (EUR)' not in {column['key'] for column in list_data(app, 'Exposure assets')['columns']}
    by_label(app.toggle, 'Break down ETFs').set_value(False).run()
    table = effective(app)
    assert table['Total (EUR)'].sum() == 300
    assert table['ETF-derived (EUR)'].sum() == 0
    assert list_data(app, 'Exposure assets')['maxHeight'] is None


def test_source_scope_filters_before_expansion_search_retains_denominator(multi_fund_workspace):
    path = multi_fund_workspace / 'holdings.csv'
    rows = pd.read_csv(path).fillna('')
    rows['portfolio'] = ['Satellite', 'Core', 'Core', 'Core']
    rows.to_csv(path, index=False)
    app = launch(multi_fund_workspace)
    by_label(app.selectbox, 'Source scope').set_value('Core').run()
    assert not app.exception
    assert selected_value(app) == '€200.00'
    by_label(app.text_input, 'Search exposure').set_value('Alpha').run()
    table = effective(app)
    assert table.Asset.tolist() == ['Invented Alpha']
    assert table['Direct (EUR)'].tolist() == [0]
    assert table['ETF-derived (EUR)'].tolist() == [75]
    assert table['Allocation %'].tolist() == [37.5]
    by_label(app.button, 'Clear filters').click().run()
    assert by_label(app.selectbox, 'Source scope').value == ''
    assert by_label(app.text_input, 'Search exposure').value == ''
    assert effective(app)['Total (EUR)'].sum() == 300


def test_missing_source_value_stays_visible_and_percentages_are_unavailable(multi_fund_workspace):
    path = multi_fund_workspace / 'holdings.csv'
    path.write_text(path.read_text().replace('FUND-C,ZZ4444444444,0,', 'FUND-C,ZZ4444444444,1,'))
    app = launch(multi_fund_workspace)
    assert not app.exception
    table = effective(app)
    assert table.loc[table.Asset.eq('Unsupported Fund'), 'Total (EUR)'].isna().all()
    assert table['Allocation %'].isna().all()
    assert any('full portfolio percentages are blank' in warning.value for warning in app.warning)
