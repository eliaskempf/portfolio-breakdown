"""Overview policy comparisons using deliberately invented amounts and categories."""
from dataclasses import replace

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.allocation import Allocation, Bucket
from portfolio_app.exposure_target_presentation import market_target_summary
from portfolio_app.exposure_targets import ExposureTargets, save_exposure_targets
from portfolio_app.holdings import DataError
from portfolio_app.portfolio_exposure import PortfolioExposure


CONFIG = Allocation((Bucket('risk', 'Invented risk'), Bucket('stock', 'Invented stock', 'risk'),
                     Bucket('gold', 'Invented gold', 'risk'), Bucket('other', 'Invented other')))
TARGET = ExposureTargets(1.5, 'composition', {'stock': .4, 'gold': .2, 'other': .4}, version=2, universe='market')


def invented_result(amounts=(80., 20., 50.)):
    rows = pd.DataFrame([{'bucket_id': bucket, 'market_exposure_reporting': amount,
                          'exposure_reporting': amount if bucket == 'stock' else 0.,
                          'source_type': 'direct', 'status': ''}
                         for bucket, amount in zip(('stock', 'gold', 'other'), amounts)])
    return PortfolioExposure(pd.DataFrame(), rows, 100., 0., 0., 100., (), True)


def test_nested_scope_keeps_absolute_targets_and_scopes_composition():
    portfolio = market_target_summary(invented_result(), TARGET, CONFIG).set_index('Category')
    assert portfolio.loc['Invented risk', 'Target exposure'] == pytest.approx(90.)
    category = market_target_summary(invented_result(), TARGET, CONFIG, 'risk').set_index('Category')
    assert category.loc['Invented stock', '% of exposure'] == 80.
    assert category.loc['Invented stock', 'Target (%)'] == pytest.approx(100*2/3)
    assert category.loc['Invented stock', 'Target exposure'] == 60.
    assert category.loc['Invented stock', 'Gap'] == -20.
    assert category['Target exposure'].sum() == portfolio.loc['Invented risk', 'Target exposure']
    leaf = market_target_summary(invented_result(), TARGET, CONFIG, 'stock')
    assert leaf.Category.tolist() == ['Invented stock']
    assert leaf['Target (%)'].item() == 100.
    assert leaf['Target exposure'].item() == 60.


def test_zero_targets_signed_exposure_and_unassigned_are_not_normalized_away():
    zero = market_target_summary(invented_result(), replace(TARGET, multiple=0.), CONFIG)
    assert zero['Target (%)'].isna().all()
    assert zero['Target exposure'].eq(0.).all()
    signed = market_target_summary(invented_result((-20., 10., 0.)), TARGET, CONFIG, 'risk')
    assert signed['% of exposure'].isna().all()
    assert signed.Exposure.min() == -20.
    source = invented_result()
    rows = source.exposures.copy()
    rows.loc[0, 'bucket_id'] = ''
    unassigned = market_target_summary(replace(source, exposures=rows), TARGET, CONFIG, 'unassigned')
    assert unassigned.Exposure.item() == 80.
    assert unassigned['Target exposure'].item() == 0.
    assert unassigned.Gap.item() == -80.
    assert unassigned['Target (%)'].isna().all()


def test_missing_values_equity_only_and_signed_targets_are_not_presented_as_valid():
    with pytest.raises(DataError, match='Equity-only'):
        market_target_summary(invented_result(), replace(TARGET, universe='equity'), CONFIG)
    with pytest.raises(DataError, match='nonnegative'):
        market_target_summary(invented_result(), replace(TARGET, composition={'stock': 1.1, 'gold': -.1}), CONFIG)
    with pytest.raises(DataError, match='coverage'):
        market_target_summary(replace(invented_result(), exposure_complete=False), TARGET, CONFIG)
    with pytest.raises(DataError, match='Unknown exposure'):
        market_target_summary(invented_result((float('nan'), 20., 50.)), TARGET, CONFIG)
    with pytest.raises(DataError, match='positive net equity'):
        market_target_summary(replace(invented_result(), net_equity=0.), TARGET, CONFIG)


def launch(directory):
    return AppTest.from_string('''
from pathlib import Path
import streamlit as st
from portfolio_app.exposure_target_presentation import render_market_target_summary
from test_exposure_target_presentation import CONFIG, invented_result
table = render_market_target_summary(invented_result(), CONFIG, Path(st.session_state.directory), '',
    open_targets=lambda: st.session_state.update(opened=True))
if table is not None:
    st.dataframe(table)
''', default_timeout=20)


def test_overview_target_controls_unset_legacy_and_market(tmp_path):
    app = launch(tmp_path)
    app.session_state.directory = str(tmp_path)
    app.run()
    assert not app.exception
    assert any('Exposure targets are unset' in x.value for x in app.caption)
    assert app.button[0].label == 'Set exposure targets'
    app.button[0].click().run()
    assert app.session_state.opened
    assert not list(tmp_path.iterdir())
    save_exposure_targets(tmp_path, replace(TARGET, universe='equity'), CONFIG, None)
    app.run()
    assert any('equity only' in x.value for x in app.caption)
    assert not app.dataframe
    assert app.button[0].label == 'Edit exposure targets'
    from portfolio_app.exposure_targets import load_exposure_targets
    _, revision = load_exposure_targets(tmp_path, CONFIG)
    save_exposure_targets(tmp_path, TARGET, CONFIG, revision)
    app.run()
    assert not app.exception
    assert 'Target exposure' in app.dataframe[0].value
    assert any('Gap = target − current' in x.value for x in app.caption)


def test_normal_overview_market_targets_and_editor_navigation(tmp_path):
    from test_futures_ui import launch as launch_app
    from test_portfolio_exposure import CONFIG as categories
    from test_portfolio_exposure_ui import synthetic_workspace
    from test_ui import activate, by_label, list_frame

    _, workspace = synthetic_workspace(tmp_path)
    target = ExposureTargets(1.5, 'composition', {'core': .8, 'stocks': .2, 'cash': 0.}, version=2, universe='market')
    save_exposure_targets(workspace, target, categories, None)
    app = launch_app(workspace)
    activate(app, 'Overview')
    by_label(app.get('button_group'), 'Allocation measure').set_value('Market exposure').run()
    assert not app.exception
    table = list_frame(app, 'Market exposure').set_index('Category')
    assert table.loc['Core', 'Target exposure'] == 120000.
    assert table.loc['Core', 'Gap'] == 10000.
    by_label(app.selectbox, 'Category').set_value('core').run()
    table = list_frame(app, 'Market exposure')
    assert table.Category.tolist() == ['Core']
    assert table['Target (%)'].item() == 100.
    assert any('category as a whole' in x.value for x in app.caption)
    by_label(app.button, 'Edit exposure targets').click().run()
    assert not app.exception
    assert by_label(app.get('button_group'), 'Target measure').value == 'Exposure targets'
    assert by_label(app.number_input, 'Desired total exposure (× net equity)').value == 1.5
