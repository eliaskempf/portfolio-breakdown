"""Exercise the actual public demo, with invented data and no network."""
import json

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from portfolio_app.demo import create_demo_data
from test_ui import by_label, launch, theme_view


def test_public_demo_sector_default_and_geography_drilldown(tmp_path):
    create_demo_data(tmp_path)
    app = launch(tmp_path)
    assert not app.exception
    assert by_label(app.selectbox, 'Asset classifications').value == 'sector'
    theme_view(app, 'selected_labels')
    assert by_label(app.selectbox, 'Label set').value == 'sector'
    assert app.get('plotly_chart')
    by_label(app.get('button_group'), 'Exposure view').set_value('Geography').run()
    assert not app.exception
    table = next(item.value for item in app.dataframe if 'Category' in item.value).set_index('Category')
    assert table.loc['Money market', 'EUR value'] == pytest.approx(20441.67)
    assert table.loc['Unknown geography', '% of selected portfolio'] < 1
    by_label(app.selectbox, 'Geography detail').set_value(('Europe', 'Germany')).run()
    assert any('Siemens' in item.value.get('Asset', []).tolist() for item in app.dataframe if 'Asset' in item.value)
    by_label(app.button, 'Back to geography overview').click().run()
    by_label(app.toggle, 'Break down ETFs').set_value(False).run()
    assert not app.exception
    table = next(item.value for item in app.dataframe if 'Category' in item.value).set_index('Category')
    assert table.loc['Money market', 'EUR value'] == pytest.approx(20441.67)
    assert table.loc['Unknown geography', 'EUR value'] == pytest.approx(60918.11)


def test_sector_default_arrives_with_metadata_without_overriding_user_choice(tmp_path):
    path = tmp_path / 'classifications.yaml'
    initial = {'invented': {'classifications': {'asset_class': [['Equity']]}}}
    path.write_text(yaml.safe_dump(initial))
    app = AppTest.from_string(
        'import pandas as pd\nfrom pathlib import Path\n'
        'from portfolio_app.label_ui import render_label_comparison\n'
        'from portfolio_app.taxonomy import load_classifications\n'
        f'render_label_comparison(pd.DataFrame([dict(asset_id="invented", asset_name="Invented company", value=100., ticker="SYN")]), load_classifications(Path({str(path)!r})))\n',
    ).run()
    assert not app.exception
    assert by_label(app.selectbox, 'Label set').value == 'asset_class'
    initial['invented']['classifications']['sector'] = [['Invented sector']]
    path.write_text(yaml.safe_dump(initial))
    app.run()
    assert not app.exception
    assert by_label(app.selectbox, 'Label set').value == 'sector'
    by_label(app.selectbox, 'Label set').set_value('asset_class').run()
    initial['invented']['classifications']['labels'] = [['Invented theme']]
    path.write_text(yaml.safe_dump(initial))
    app.run()
    assert not app.exception
    assert by_label(app.selectbox, 'Label set').value == 'asset_class'


def test_failed_first_download_has_visible_retry_direction(tmp_path):
    status = tmp_path / '.cache' / 'etf-refresh' / 'status.json'
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({'ZZ9999999999': {'status': 'unavailable', 'error': 'Invented outage'}}))
    app = AppTest.from_string(
        'from pathlib import Path\n'
        'from portfolio_app.etf_refresh_ui import render_refresh_status, refresh_revision\n'
        f'p = Path({str(tmp_path)!r})\nrender_refresh_status(p, [], refresh_revision(p))\n',
    ).run()
    assert not app.exception
    assert any('1 ETF breakdown(s) unavailable' in item.value and 'retry' in item.value for item in app.caption)
