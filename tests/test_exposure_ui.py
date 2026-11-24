import pandas as pd
import pytest

from test_etf_selection import multi_fund_workspace as multi_fund_workspace, effective
from test_ui import launch, by_label, selected_value
from list_helpers import exposure_breakdown, list_data


def test_default_exposure_is_all_assets_without_chart_or_label_requirement(multi_fund_workspace):
    app = launch(multi_fund_workspace)
    assert not app.exception
    assert by_label(app.get('button_group'), 'Exposure view').value == 'Assets'
    assert by_label(app.toggle, 'Break down ETFs').value
    table = effective(app)
    assert table['Total'].sum() == 300
    assert table['Allocation %'].sum() == pytest.approx(100)
    assert table.loc[table.Asset.eq('Invented Alpha'), 'Total'].tolist() == [175]
    assert len(app.tabs[1].get('plotly_chart')) == 0
    assert by_label(app.tabs[1].metric, 'Current value').value == '€300.00'
    assert by_label(app.tabs[1].metric, 'Portfolio share').value == '100.0%'
    assert not any('source positions' in caption.value or 'use a proxy' in caption.value for caption in app.tabs[1].caption)
    assert list_data(app, 'Exposure assets')['maxHeight'] == 620
    data = list_data(app, 'Exposure assets')
    assert data['rows']['fields'] == ['asset_id', *(column['key'] for column in data['columns'])]
    assert 'Ticker' not in data['rows']['fields'] and 'Direct' not in data['rows']['fields']
    by_label(app.checkbox, 'Show tickers').check().run()
    assert 'Ticker' in list_data(app, 'Exposure assets')['rows']['fields']
    by_label(app.toggle, 'Break down ETFs').set_value(False).run()
    table = effective(app)
    assert table['Total'].sum() == 300
    assert exposure_breakdown(app)['ETF-derived'].sum() == 0
    assert list_data(app, 'Exposure assets')['maxHeight'] is None


def test_fund_components_reused_until_constituent_evidence_changes(multi_fund_workspace, monkeypatch):
    from portfolio_app.funds import etf

    original = etf._component_columns
    calls = []
    def observed(fund, resolve):
        calls.append(fund.fund_id)
        return original(fund, resolve)
    monkeypatch.setattr(etf, '_component_columns', observed)

    app = launch(multi_fund_workspace)
    assert effective(app).loc[lambda r: r.Asset.eq('Invented Alpha'), 'Total'].item() == 175.
    assert sorted(calls) == ['emerging', 'world']
    by_label(app.get('button_group'), 'Exposure view').set_value('Themes & sectors').run()
    assert not app.exception
    assert len(calls) == 2  # Primary value, targets and performance used the same weights.

    path = multi_fund_workspace / 'etfs' / 'world.csv'
    path.write_text(path.read_text().replace(',0.5,equity', ',0.6,equity'))
    by_label(app.get('button_group'), 'Exposure view').set_value('Assets').run()
    assert not app.exception
    assert effective(app).loc[lambda r: r.Asset.eq('Invented Alpha'), 'Total'].item() == 185.
    assert 'world' in calls[2:]


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
    breakdown = exposure_breakdown(app)
    assert breakdown['Direct'].tolist() == [0]
    assert breakdown['ETF-derived'].tolist() == [75]
    assert table['Allocation %'].tolist() == [37.5]
    by_label(app.button, 'Clear filters').click().run()
    assert by_label(app.selectbox, 'Source scope').value == ''
    assert by_label(app.text_input, 'Search exposure').value == ''
    assert effective(app)['Total'].sum() == 300


def test_missing_source_value_stays_visible_and_percentages_are_unavailable(multi_fund_workspace):
    path = multi_fund_workspace / 'holdings.csv'
    path.write_text(path.read_text().replace('FUND-C,ZZ4444444444,0,', 'FUND-C,ZZ4444444444,1,'))
    app = launch(multi_fund_workspace)
    assert not app.exception
    table = effective(app)
    assert table.loc[table.Asset.eq('Unsupported Fund'), 'Total'].isna().all()
    assert table['Allocation %'].isna().all()
    assert any('full portfolio percentages are blank' in warning.value for warning in app.warning)


def test_refresh_controls_open_from_a_notification(multi_fund_workspace):
    app = launch(multi_fund_workspace)
    assert not any(button.label == 'Refresh ETF holdings now' for button in app.button)
    app.session_state['exposure_notification_sources'] = True
    app.run()
    assert not app.exception
    assert by_label(app.button, 'Refresh ETF holdings now').disabled  # Offline demo.
