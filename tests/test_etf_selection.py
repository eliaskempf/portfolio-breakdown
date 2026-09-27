"""Synthetic multi-fund portfolios for master and per-fund look-through."""

import json

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from test_ui import by_label, launch


@pytest.fixture
def multi_fund_workspace(tmp_path):
    (tmp_path / 'holdings.csv').write_text(
        'id,name,ticker,isin,shares,acquisition_price,acquisition_currency,target_allocation,instrument_type,exposure_kind\n'
        'company,Invented Alpha,COMPANY,ZZ1111111111,1,20,EUR,0.2,equity,equity\n'
        'world,Synthetic World,FUND-A,ZZ2222222222,1,20,EUR,0.4,etf,equity\n'
        'emerging,Synthetic Emerging,FUND-B,ZZ3333333333,1,20,EUR,0.4,etf,equity\n'
        'unsupported,Unsupported Fund,FUND-C,ZZ4444444444,0,,,0,etf,unknown\n'
    )
    (tmp_path / 'demo_prices.json').write_text(json.dumps({'prices': {
        ticker: {'price': 100, 'currency': 'EUR', 'observed_at': '2026-01-01T12:00:00+00:00'}
        for ticker in ['COMPANY', 'FUND-A', 'FUND-B']}, 'fx': {}}))
    directory = tmp_path / 'etfs'
    directory.mkdir()
    for fund_id, name, isin, ticker, weight in [
        ('world', 'Synthetic World', 'ZZ2222222222', 'FUND-A', .5),
        ('emerging', 'Synthetic Emerging', 'ZZ3333333333', 'FUND-B', .25),
    ]:
        (directory / f'{fund_id}.csv').write_text(
            'constituent_id,name,ticker,isin,weight,instrument_type\n'
            f'company,Invented Alpha,COMPANY,ZZ1111111111,{weight},equity\n'
        )
        (directory / f'{fund_id}.yaml').write_text(yaml.safe_dump({
            'fund_id': fund_id, 'name': name, 'isin': isin, 'tickers': [ticker],
            'as_of': '2026-01-01', 'source': 'https://example.invalid/synthetic-holdings',
            'holdings_file': f'{fund_id}.csv', 'equity_fund': True,
        }))
    return tmp_path


def effective(app):
    return next(item.value for item in app.dataframe if 'ETF-derived (EUR)' in item.value)


def test_master_expands_all_and_per_fund_switch_preserves_totals_and_choices(multi_fund_workspace):
    path = multi_fund_workspace / 'holdings.csv'
    before = path.read_bytes()
    app = launch(multi_fund_workspace)
    assert not app.exception
    assert not by_label(app.toggle, 'Break down ETFs').value
    by_label(app.toggle, 'Break down ETFs').set_value(True).run()
    assert not app.exception and not app.error
    assert by_label(app.toggle, 'Synthetic World').value
    assert by_label(app.toggle, 'Synthetic Emerging').value
    assert any('No breakdown available: Unsupported Fund' in info.value for info in app.info)
    table = effective(app)
    assert table.loc[table.Asset == 'Invented Alpha', 'Total (EUR)'].iloc[0] == 175.
    assert table['Total (EUR)'].sum() == 300.
    by_label(app.toggle, 'Synthetic World').set_value(False).run()
    assert not app.exception and not app.error
    table = effective(app)
    assert table.loc[table.Asset == 'Invented Alpha', 'Total (EUR)'].iloc[0] == 125.
    assert table.loc[table.Asset == 'Synthetic World', 'Total (EUR)'].iloc[0] == 100.
    assert table['Total (EUR)'].sum() == 300.
    # Targets follow the same selective expansion, and the intact fund retains
    # its own performance instead of inheriting missing constituent history.
    chart = next(item.value for item in app.dataframe if 'Category' in item.value and 'Target portfolio %' in item.value)
    world = chart.loc[chart.Category == 'Synthetic World'].iloc[0]
    assert world['Target portfolio %'] == 40.
    assert world['Performance coverage'] == 'Complete'
    assert chart['Target portfolio %'].sum() == pytest.approx(100.)
    by_label(app.toggle, 'Break down ETFs').set_value(False).run()
    by_label(app.toggle, 'Break down ETFs').set_value(True).run()
    assert not by_label(app.toggle, 'Synthetic World').value
    assert by_label(app.toggle, 'Synthetic Emerging').value
    assert path.read_bytes() == before


def test_stock_view_respects_individual_fund_expansion(multi_fund_workspace):
    app = launch(multi_fund_workspace)
    by_label(app.toggle, 'Break down ETFs').set_value(True).run()
    by_label(app.toggle, 'Synthetic World').set_value(False).run()
    by_label(app.checkbox, 'Show stock-only company exposure').check().run()
    assert not app.exception and not app.error
    companies = next(item.value for item in app.dataframe if 'Company ID' in item.value and 'Total (EUR)' in item.value)
    assert companies['Total (EUR)'].sum() == 125.
    unresolved = next(item.value for item in app.dataframe if 'Status' in item.value and 'Exposure' in item.value)
    assert 'Synthetic World' in unresolved.Exposure.tolist()


def test_proxy_status_and_newly_supported_funds_default_on(multi_fund_workspace):
    directory = multi_fund_workspace / 'etfs'
    path = directory / 'world.yaml'
    manifest = yaml.safe_load(path.read_text())
    manifest['proxy_source'] = 'Invented same-index fund; approximate exposure'
    path.write_text(yaml.safe_dump(manifest))
    app = launch(multi_fund_workspace)
    by_label(app.toggle, 'Break down ETFs').set_value(True).run()
    assert by_label(app.toggle, 'Synthetic World (proxy)').value
    assert any('Approximate breakdown' in item.value for item in app.info)
    by_label(app.toggle, 'Synthetic Emerging').set_value(False).run()
    manifest.update(fund_id='new', name='New synthetic breakdown', isin='ZZ4444444444', tickers=['FUND-C'], proxy_source='')
    (directory / 'new.yaml').write_text(yaml.safe_dump(manifest))
    app.run()
    assert not app.exception
    assert by_label(app.toggle, 'New synthetic breakdown').value
    assert not by_label(app.toggle, 'Synthetic Emerging').value


def test_reviewed_company_mapping_reaches_allocation_and_stock_ui(multi_fund_workspace):
    directory = multi_fund_workspace / 'etfs'
    for name in ['world', 'emerging']:
        (directory / f'{name}.csv').write_text(
            'constituent_id,name,ticker,isin,weight,instrument_type\n'
            'invented-local-share,Invented local ordinary share,,,0.5,equity\n')
    (multi_fund_workspace / 'company-identities.yaml').write_text(yaml.safe_dump({
        'security:ZZ1111111111': 'reviewed-invented-issuer',
        'instrument:invented-local-share': 'reviewed-invented-issuer',
    }))
    app = launch(multi_fund_workspace)
    by_label(app.toggle, 'Break down ETFs').set_value(True).run()
    assert not app.exception and not app.error
    company = effective(app).loc[lambda x: x.Asset == 'Invented Alpha'].iloc[0]
    assert company['Direct (EUR)'] == 100.
    assert company['ETF-derived (EUR)'] == 100.
    by_label(app.checkbox, 'Show stock-only company exposure').check().run()
    assert not app.exception
    companies = next(item.value for item in app.dataframe if 'Company ID' in item.value and 'Total (EUR)' in item.value)
    assert companies['Total (EUR)'].tolist() == [200.]


def test_estimated_merge_review_undo_restore_and_reload(multi_fund_workspace):
    directory = multi_fund_workspace / 'etfs'
    for index, name in enumerate(['world', 'emerging']):
        label = 'Invented Photon NV' if index == 0 else 'INVENTED PHOTON'
        (directory / f'{name}.csv').write_text(
            'constituent_id,name,ticker,isin,weight,instrument_type\n'
            f'provider-{index},{label},,,0.5,equity\n')
    protected = {path: path.read_bytes() for path in [multi_fund_workspace / 'holdings.csv', *directory.iterdir()]}
    app = launch(multi_fund_workspace)
    by_label(app.toggle, 'Break down ETFs').set_value(True).run()
    assert not app.exception and not app.error
    assert effective(app).loc[lambda x: x.Asset.str.endswith(' *'), 'Total (EUR)'].tolist() == [100.]
    assert any('Estimated name match' in item.value for item in app.caption)
    provenance = next(item.value for item in app.dataframe if 'Original asset' in item.value)
    assert set(provenance.Source) == {'Synthetic World', 'Synthetic Emerging'}
    by_label(app.button, 'Undo merge').click().run()
    assert not app.exception and not app.error
    assert not effective(app).Asset.str.endswith(' *').any()
    assert len(effective(app).loc[lambda x: x.Asset.str.contains('Photon')]) == 2
    by_label(app.checkbox, 'Show stock-only company exposure').check().run()
    stock = next(item.value for item in app.dataframe if 'Company ID' in item.value and 'Total (EUR)' in item.value)
    assert len(stock) == 3  # The original direct stock plus two separate fund stocks.
    reloaded = launch(multi_fund_workspace)
    by_label(reloaded.toggle, 'Break down ETFs').set_value(True).run()
    assert not effective(reloaded).Asset.str.endswith(' *').any()
    by_label(reloaded.button, 'Restore merge').click().run()
    assert not reloaded.exception and not reloaded.error
    assert effective(reloaded).loc[lambda x: x.Asset.str.endswith(' *'), 'Total (EUR)'].tolist() == [100.]
    assert all(path.read_bytes() == content for path, content in protected.items())


def test_interrupted_render_restores_missing_filter_widget_state(multi_fund_workspace):
    app = launch(multi_fund_workspace)
    by_label(app.multiselect, 'Holdings').set_value(['world', 'emerging']).run()
    key = app.session_state['filter_holdings_widget']
    # Recreate a session whose remembered selection survived widget cleanup.
    app = AppTest.from_string(
        'from pathlib import Path\nfrom portfolio_app.ui import render_app\n'
        f'render_app(Path({str(multi_fund_workspace)!r}), demo=True)\n',
        default_timeout=15,
    )
    app.session_state['filter_holdings_widget'] = key
    app.session_state['filter_holdings_selection'] = ['world', 'emerging']
    app.run()
    assert not app.exception
    assert set(by_label(app.multiselect, 'Holdings').value) == {'world', 'emerging'}
    # An intentional empty selection remains empty.
    by_label(app.multiselect, 'Holdings').set_value([]).run()
    assert by_label(app.multiselect, 'Holdings').value == []


def test_reviewed_company_name_reaches_chart_and_merge_review(multi_fund_workspace):
    (multi_fund_workspace / 'company-identities.yaml').write_text('security:ZZ1111111111: invented-issuer\n')
    (multi_fund_workspace / 'company-names.yaml').write_text('invented-issuer: Invented issuer display name\n')
    app = launch(multi_fund_workspace)
    by_label(app.toggle, 'Break down ETFs').set_value(True).run()
    assert not app.exception and not app.error
    assert 'Invented issuer display name' in effective(app).Asset.tolist()
    provenance = next(item.value for item in app.dataframe if 'Original asset' in item.value)
    assert set(provenance['Original asset']) == {'Invented Alpha'}


def test_fund_selection_survives_master_hiding_it_in_the_same_rerun(multi_fund_workspace):
    app = launch(multi_fund_workspace)
    by_label(app.toggle, 'Break down ETFs').set_value(True).run()
    by_label(app.toggle, 'Synthetic World').set_value(False)
    by_label(app.toggle, 'Break down ETFs').set_value(False).run()
    by_label(app.toggle, 'Break down ETFs').set_value(True).run()
    assert not app.exception and not app.error
    assert not by_label(app.toggle, 'Synthetic World').value
    assert by_label(app.toggle, 'Synthetic Emerging').value
