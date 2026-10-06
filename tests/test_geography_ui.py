"""Offline Streamlit smoke tests using invented data only."""
import json

import pandas as pd
import pytest
import yaml

from test_etf_selection import multi_fund_workspace as multi_fund_workspace, effective
from test_ui import by_label, launch, theme_view
from portfolio_app.label_comparison import Label


@pytest.fixture
def geography_workspace(multi_fund_workspace):
    directory = multi_fund_workspace
    for name in ['world', 'emerging']:
        path = directory / 'etfs' / f'{name}.csv'
        rows = pd.read_csv(path).fillna('')
        rows['country'] = 'DE'
        rows['sector'] = 'Invented sector'
        rows.to_csv(path, index=False)
    path = directory / 'holdings.csv'
    rows = pd.read_csv(path).fillna('')
    for asset, kind in [('crypto', 'crypto'), ('gold', 'physical'), ('cash', 'cash'), ('british', 'equity'), ('american', 'equity')]:
        rows = pd.concat([rows, pd.DataFrame([dict(id=asset, name=f'Synthetic {asset.title()}', ticker=f'SYN-{asset.upper()}',
                        shares=1, instrument_type=kind, target_allocation=0.)])], ignore_index=True)
    rows.fillna('').to_csv(path, index=False)
    path = directory / 'demo_prices.json'
    prices = json.loads(path.read_text())
    for asset in ['crypto', 'gold', 'cash', 'british', 'american']:
        prices['prices'][f'SYN-{asset.upper()}'] = dict(price=100, currency='EUR', observed_at='2026-01-01T12:00:00+00:00')
    path.write_text(json.dumps(prices))
    (directory / 'classifications.yaml').write_text(yaml.safe_dump({
        'company': {'classifications': {'labels': [['Invented AI', 'Compute']]}},
        'gold': {'classifications': {'asset_class': [['Commodities', 'Gold']]}},
        'british': {'classifications': {'geography': [['UK']]}},
        'american': {'classifications': {'geography': [['US']]}},
    }))
    return directory


def open_geography(app):
    by_label(app.get('button_group'), 'Exposure view').set_value('Geography').run()
    assert not app.exception


def breakdown(app):
    return next(item.value for item in app.tabs[1].dataframe if '% of selected portfolio' in item.value)


def test_assets_expose_sector_geography_and_all_detail_classifications(geography_workspace):
    app = launch(geography_workspace)
    assert not app.exception
    assert by_label(app.selectbox, 'Asset classifications').value == 'labels'
    by_label(app.selectbox, 'Asset classifications').set_value('sector').run()
    table = effective(app).set_index('asset_id')
    assert table.loc['company', 'Labels'] == ['Invented sector']
    by_label(app.selectbox, 'Asset classifications').set_value('geography').run()
    table = effective(app).set_index('asset_id')
    assert table.loc['company', 'Labels'] == ['Europe › Germany']
    assert table.loc['crypto', 'Labels'] == ['Crypto']
    app.session_state['exposure_asset_detail'] = 'company'
    app.run()
    assert not app.exception
    assert any('Themes: Invented AI > Compute' in item.value for item in app.markdown)
    assert any('Geography: Europe > Germany' in item.value for item in app.markdown)
    assert any('Geography source: Synthetic' in item.value for item in app.caption)


def test_region_country_drilldown_search_and_etf_toggle(geography_workspace):
    app = launch(geography_workspace)
    open_geography(app)
    table = breakdown(app).set_index('Category')
    assert table['Value'].sum() == 800
    assert table['% of selected portfolio'].sum() == pytest.approx(100)
    assert table.loc['Europe', 'Value'] == 275
    assert table.loc['United States', 'Value'] == 100
    for name in ['Gold', 'Crypto', 'Cash']:
        assert table.loc[name, 'Value'] == 100
    assert table.loc['Unknown geography', 'Value'] == 125
    by_label(app.get('button_group'), 'Geography granularity').set_value('Countries').run()
    table = breakdown(app).set_index('Category')
    assert table.loc['Germany', 'Value'] == 175
    assert table.loc['United Kingdom', 'Value'] == 100
    by_label(app.selectbox, 'Geography detail').set_value(('Europe',)).run()
    assert set(breakdown(app).Category) == {'Germany', 'United Kingdom'}
    by_label(app.selectbox, 'Geography detail').set_value(('Europe', 'Germany')).run()
    assert breakdown(app).Asset.tolist() == ['Invented Alpha']
    assert breakdown(app)['Value'].tolist() == [175]
    by_label(app.button, 'Back to geography overview').click().run()
    by_label(app.text_input, 'Search exposure').set_value('Alpha').run()
    assert breakdown(app)['% of selected portfolio'].tolist() == [100 * 175 / 800]
    by_label(app.text_input, 'Search exposure').set_value('').run()
    by_label(app.toggle, 'Break down ETFs').set_value(False).run()
    table = breakdown(app).set_index('Category')
    assert table.loc['Unknown geography', 'Value'] == 200
    assert table['Value'].sum() == 800
    by_label(app.multiselect, 'Holdings').set_value(['british']).run()
    assert breakdown(app)['Value'].tolist() == [100]
    assert breakdown(app)['% of selected portfolio'].tolist() == [100]


def test_missing_prices_and_unknown_assets_stay_visible(geography_workspace):
    path = geography_workspace / 'holdings.csv'
    rows = pd.read_csv(path).fillna('')
    rows.loc[rows.id.eq('unsupported'), 'shares'] = 1
    rows.to_csv(path, index=False)
    app = launch(geography_workspace)
    open_geography(app)
    assert breakdown(app)['% of selected portfolio'].isna().all()
    assert breakdown(app)['Missing valuations'].sum() == 1
    assert any('of priced value' in item.value for item in app.caption)
    by_label(app.selectbox, 'Geography detail').set_value(('Unknown geography',)).run()
    assert breakdown(app).Asset.str.contains('Unsupported Fund').any()
    assert breakdown(app)['Classification note'].str.contains('Unresolved ETF residual').any()
    by_label(app.text_input, 'Search exposure').set_value('nonexistent').run()
    assert not app.exception
    assert any('No assets match this search' in item.value for item in app.info)


def test_label_sets_remember_selections_and_ai_analysis_is_unchanged(geography_workspace):
    app = launch(geography_workspace)
    theme_view(app, 'selected_labels')
    ai = Label('labels', ('Invented AI',)).key
    by_label(app.multiselect, 'Labels to compare').set_value([ai]).run()
    before = app.tabs[1].dataframe[-1].value.copy()
    by_label(app.selectbox, 'Label set').set_value('sector').run()
    sector = Label('sector', ('Invented sector',)).key
    by_label(app.multiselect, 'Labels to compare').set_value([sector]).run()
    by_label(app.selectbox, 'Label set').set_value('labels').run()
    assert by_label(app.multiselect, 'Labels to compare').value == [ai]
    pd.testing.assert_frame_equal(before, app.tabs[1].dataframe[-1].value)
    by_label(app.selectbox, 'Label set').set_value('sector').run()
    assert by_label(app.multiselect, 'Labels to compare').value == [sector]
    open_geography(app)
    theme_view(app, 'selected_labels')
    by_label(app.selectbox, 'Label set').set_value('labels').run()
    pd.testing.assert_frame_equal(before, app.tabs[1].dataframe[-1].value)
