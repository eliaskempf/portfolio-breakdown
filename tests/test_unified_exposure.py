"""Invented exposure, funding and lifecycle examples; no working portfolio data."""
from dataclasses import replace
from unittest.mock import patch

import pandas as pd
import pytest

from portfolio_app.exposure_workspace import analysis_sources, analysis_contributions, analytical_signature
from portfolio_app.exposure_tables import asset_exposure_table, exposure_sources
from portfolio_app.leverage_settings import MarginLiability
from test_future_proxy_exposure import fixture, calculate, write_proxy
from test_portfolio_exposure_ui import synthetic_workspace
from test_futures_ui import launch
from test_ui import activate, by_label, list_frame


@pytest.mark.parametrize('contracts', [1, -1, -3])
def test_sources_merge_signed_notional_and_keep_funding_separate(contracts):
    frame, fund, future = fixture()
    future = replace(future, contracts=contracts)
    result = calculate(frame, fund, future)
    sources = analysis_sources(frame, result, future)
    rows = analysis_contributions(result, sources, [fund], sources.position_id, expanded_funds=[fund])
    table = asset_exposure_table(rows, equity=result.denominator).set_index('asset_id')
    assert table.loc['stock', 'Total'] == 54000 + 20000 * contracts
    assert table.loc['stock', 'Futures-derived'] == 20000 * contracts
    assert rows.value.sum() == 90000 + 50000 * contracts
    assert table.loc['cash', 'Total'] == 0
    assert table.loc['stock', 'Equity %'] == pytest.approx((54000 + 20000 * contracts) / 1200)
    if rows.value.sum() <= 0:
        assert table['Allocation %'].isna().all()
    details = exposure_sources(rows, sources, 'stock', measure='Market exposure')
    assert details.loc[details.Exposure.eq('Futures proxy'), 'Asset weight (%)'].item() == pytest.approx(40)
    funded = analysis_contributions(result, sources, [fund], sources.position_id,
        measure='Funded value', expanded_funds=[fund])
    assert funded.value.sum() == 120000
    assert funded.loc[funded.source_type.eq('future'), 'value'].tolist() == [20000]
    assert funded.loc[funded.asset_id.eq('stock'), 'value'].sum() == 54000


def test_scope_keeps_proxy_lookup_and_collapsing_does_not_change_totals():
    frame, fund, future = fixture()
    result = calculate(frame, fund, future)
    sources = analysis_sources(frame, result, future)
    proxy_only = analysis_contributions(result, sources, [fund], ['future:overlay'], expanded_funds=[fund])
    assert 'stock' in set(proxy_only.asset_id)
    assert proxy_only.value.sum() == 50000
    for expanded in ([], [fund]):
        rows = analysis_contributions(result, sources, [fund], sources.position_id, expanded_funds=expanded)
        assert rows.value.sum() == 140000
        if not expanded:
            assert len(rows) == len(sources)
            assert rows.loc[rows.source_type.eq('future'), 'asset_id'].item() == future.instrument


def test_unknowns_and_legacy_funding_reconcile_without_notional_wealth():
    frame, fund, future = fixture()
    future = replace(future, reference_capital_eur=None, mark_price=510.)
    result = calculate(frame, fund, future, liabilities=(MarginLiability('invented', 'Invented loan', 5000.),))
    sources = analysis_sources(frame, result, future)
    rows = analysis_contributions(result, sources, [fund], sources.position_id, measure='Funded value', expanded_funds=[fund])
    assert rows.value.sum() == result.net_equity == 96000
    frame.loc[1, 'current_value_reporting'] = float('nan')
    result = calculate(frame, fund, future)
    sources = analysis_sources(frame, result, future)
    rows = analysis_contributions(result, sources, [fund], sources.position_id, expanded_funds=[fund])
    table = asset_exposure_table(rows, complete=False)
    assert pd.isna(table.set_index('asset_id').loc['stock', 'Total'])
    assert table['Allocation %'].isna().all()


def test_cache_signature_changes_with_facts_but_not_age():
    frame, fund, future = fixture()
    signature = analytical_signature(frame, [fund], future)
    assert analytical_signature(frame.assign(price_age_hours=999, fx_observed_at='later'), [fund], future) == signature
    changed = frame.copy()
    changed.loc[0, 'current_value_reporting'] += 1
    assert analytical_signature(changed, [fund], future) != signature
    assert analytical_signature(frame, [fund], replace(future, contracts=-1)) != signature
    weights = fund.constituents.copy()
    weights.loc[0, 'weight'] = .3
    assert analytical_signature(frame, [replace(fund, constituents=weights)], future) != signature
    assert analytical_signature(frame, [replace(fund, asset_class='fixed_income')], future) != signature


@pytest.mark.parametrize('measure', ['Market exposure', 'Funded value'])
def test_unified_views_lazy_details_and_tour_restoration(tmp_path, measure):
    from portfolio_app.futures_workspace import load_sandbox, save_sandbox
    _, directory = synthetic_workspace(tmp_path)
    write_proxy(directory)
    future, revision = load_sandbox(directory)
    save_sandbox(directory, replace(future, reference_capital_eur=20000., proxy_position_id='fund'), revision)
    app = launch(directory, open_futures=False)
    activate(app, 'Exposure')
    assert not app.exception
    assert by_label(app.selectbox, 'Analysis measure').value == 'Market exposure'
    assert not app.get('plotly_chart')
    assert not any(item.label == 'Company' for item in app.selectbox)
    by_label(app.selectbox, 'Analysis measure').set_value(measure).run()
    expected = 140000 if measure == 'Market exposure' else 120000
    assert list_frame(app, 'Exposure assets').Total.sum() == expected
    for view in ['Themes & sectors', 'Geography', 'Assets']:
        by_label(app.get('button_group'), 'Exposure view').set_value(view).run()
        assert not app.exception
    by_label(app.text_input, 'Search exposure').set_value('Invented').run()
    by_label(app.toggle, 'Look through funds & futures').set_value(False).run()
    before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    by_label(app.button, 'Take the tour').click().run()
    assert not app.exception
    by_label(app.button, 'Skip to Exposure →').click().run()
    assert not app.exception
    by_label(app.button, 'Skip tour').click().run()
    assert not app.exception
    assert app.session_state['main_tabs'] == 'Exposure'
    assert by_label(app.selectbox, 'Analysis measure').value == measure
    assert by_label(app.text_input, 'Search exposure').value == 'Invented'
    assert not by_label(app.toggle, 'Look through funds & futures').value
    assert all(p.read_bytes() == content for p, content in before.items())


def test_rerun_search_and_view_change_reuse_exposure_calculation(tmp_path):
    from portfolio_app.application import calculate_portfolio_exposure
    _, directory = synthetic_workspace(tmp_path)
    write_proxy(directory)
    app = launch(directory, open_futures=False)
    activate(app, 'Exposure')
    with patch('portfolio_app.application.calculate_portfolio_exposure', wraps=calculate_portfolio_exposure) as calculate_mock:
        app.run()
        by_label(app.text_input, 'Search exposure').set_value('Invented').run()
        by_label(app.get('button_group'), 'Exposure view').set_value('Geography').run()
        assert not app.exception
        assert calculate_mock.call_count == 0


def test_actual_account_exposure_and_funded_views(tmp_path):
    from test_actual_portfolio import invented
    from test_accounts_ui import launch_actual
    from portfolio_app.account_journal import save_journal
    _, journal, _ = invented(tmp_path)
    save_journal(tmp_path, journal, None)
    app = launch_actual(tmp_path)
    activate(app, 'Exposure')
    assert not app.exception
    assert list_frame(app, 'Exposure assets')['Futures-derived'].abs().sum() > 0
    for measure in ['Funded value', 'Market exposure']:
        by_label(app.selectbox, 'Analysis measure').set_value(measure).run()
        for view in ['Assets', 'Themes & sectors', 'Geography']:
            by_label(app.get('button_group'), 'Exposure view').set_value(view).run()
            assert not app.exception


def test_partial_fund_preserves_known_subtotal_without_inventing_total():
    from portfolio_app.exposure_presentation import exposure_positions
    from portfolio_app.strategic import strategic_summary
    from test_portfolio_exposure import CONFIG
    frame, fund, future = fixture()
    frame.loc[frame.position_id.eq('fund'), 'exposure_kind'] = 'unknown'
    constituents = fund.constituents.copy()
    constituents.loc[0, ['instrument_type', 'weight']] = ['equity', .6]
    constituents.loc[1, 'instrument_type'] = 'unknown'
    fund = replace(fund, constituents=constituents, equity_fund=False)
    result = calculate(frame, fund, future)
    sources = analysis_sources(frame, result, future)
    for expanded in ([], [fund]):
        rows = analysis_contributions(result, sources, [fund], ['fund'], expanded_funds=expanded)
        assert rows.known_value.sum() == pytest.approx(36000.)
        assert rows.value.isna().any()
        table = asset_exposure_table(rows, complete=False)
        assert table['Known exposure'].sum() == pytest.approx(36000.)
        assert table['Allocation %'].isna().all()
    whole = exposure_positions(frame, result, future)
    known = exposure_positions(frame, result, future, known_only=True)
    assert pd.isna(whole.set_index('position_id').loc['fund', 'current_value_reporting'])
    assert known.set_index('position_id').loc['fund', 'current_value_reporting'] == pytest.approx(36000.)
    table = strategic_summary(whole, CONFIG, known_exposure=known.set_index('position_id').current_value_reporting).set_index('Category')
    assert pd.isna(table.loc['Core', 'Value'])
    assert table.loc['Core', 'Known exposure'] == pytest.approx(86000.)
    assert table['Current (%)'].isna().all()


def test_partial_fund_ui_explains_constituent_gap_and_keeps_chart(tmp_path):
    from base64 import b64decode
    import json
    import numpy as np
    import yaml
    _, directory = synthetic_workspace(tmp_path)
    write_proxy(directory)
    path = directory / 'etfs' / 'invented.csv'
    constituents = pd.read_csv(path)
    constituents['instrument_type'] = ['equity', 'unknown']
    constituents.loc[0, 'weight'] = .6
    constituents.to_csv(path, index=False)
    manifest = directory / 'etfs' / 'invented.yaml'
    settings = yaml.safe_load(manifest.read_text())
    settings['equity_fund'] = False
    manifest.write_text(yaml.safe_dump(settings))
    holdings_path = directory / 'holdings.csv'
    holdings = pd.read_csv(holdings_path)
    holdings.loc[holdings.id.eq('fund'), 'exposure_kind'] = 'unknown'
    holdings.to_csv(holdings_path, index=False)
    app = launch(directory, open_futures=False)
    assert not app.exception
    table = list_frame(app, 'Market exposure').set_index('Category')
    assert pd.isna(table.loc['Core', 'Exposure'])
    assert table.loc['Core', 'Known exposure'] == pytest.approx(86000.)
    assert table['% of exposure'].isna().all()
    chart = json.loads(app.get('plotly_chart')[0].proto.spec)['data'][0]
    index = [json.loads(key)[2] for key in chart['ids']].index('fund')
    values = chart['values']
    if isinstance(values, dict):
        values = np.frombuffer(b64decode(values['bdata']), dtype=values['dtype'])
    assert values[index] == pytest.approx(36000.)
    assert 'Known exposure' in chart['hovertemplate']
    details = next(x.value for x in app.dataframe if 'Unresolved asset' in x.value)
    assert details['Unresolved asset'].tolist() == ['Invented second company']
    assert 'reported market value' in details.Reason.item()
    by_label(app.selectbox, 'Category').set_value('stocks').run()
    assert not any(x.label == 'Why is exposure incomplete?' for x in app.expander)
    activate(app, 'Exposure')
    by_label(app.selectbox, 'Source scope').set_value('core').run()
    by_label(app.toggle, 'Look through funds & futures').set_value(False).run()
    assert not app.exception
    table = list_frame(app, 'Exposure assets').set_index('Asset')
    assert pd.isna(table.loc['Invented fund', 'Total'])
    assert table.loc['Invented fund', 'Known exposure'] == pytest.approx(36000.)
    assert by_label(app.metric, 'Known market exposure').value == '€86,000.00'
    details = next(x.value for x in app.dataframe if 'Unresolved asset' in x.value)
    assert details['Unresolved asset'].tolist() == ['Invented second company']


def test_ordinary_equity_fund_unknown_constituent_keeps_complete_ui_totals(tmp_path):
    _, directory = synthetic_workspace(tmp_path)
    write_proxy(directory)
    path = directory / 'etfs' / 'invented.csv'
    constituents = pd.read_csv(path)
    constituents['instrument_type'] = ['equity', 'unknown']
    constituents.to_csv(path, index=False)
    app = launch(directory, open_futures=False)
    assert not app.exception
    table = list_frame(app, 'Market exposure').set_index('Category')
    assert table.loc['Core', 'Exposure'] == 110000.
    assert table['% of exposure'].sum() == pytest.approx(100.)
    assert not any(x.label == 'Why is exposure incomplete?' for x in app.expander)
    activate(app, 'Exposure')
    for expanded in [True, False]:
        by_label(app.toggle, 'Look through funds & futures').set_value(expanded).run()
        table = list_frame(app, 'Exposure assets')
        assert table.Total.sum() == 140000.
        assert table['Allocation %'].sum() == pytest.approx(100.)
        assert not table.Total.isna().any()
