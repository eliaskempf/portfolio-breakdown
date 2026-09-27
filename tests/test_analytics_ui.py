"""Analytics interaction checks in temporary, explicitly synthetic workspaces."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.analytics_ui import load_metrics
from portfolio_app.holdings import load_holdings
from test_ui import by_label, launch


@pytest.fixture(autouse=True)
def no_live_analytics(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise AssertionError('UI tests must not fetch live analytics')
    monkeypatch.setattr('portfolio_app.fundamentals.YahooFundamentalsProvider.fetch', fail)
    monkeypatch.setattr('portfolio_app.risk_data.YahooRiskHistoryProvider.fetch', fail)
    yield
    assert not calls, 'A demo UI attempted live analytics access'


def test_position_presets_details_and_risk(sample_data_dir):
    app = launch(sample_data_dir)
    assert by_label(app.selectbox, 'Position metrics').value == 'Default'
    by_label(app.selectbox, 'Position metrics').set_value('Valuation').run()
    assert not app.exception
    assert by_label(app.multiselect, 'Metric columns').value == ['trailing_pe', 'forward_pe', 'price_book']
    by_label(app.checkbox, 'Show instrument metrics').check().run()
    assert not app.exception
    details = next(table.value for table in app.dataframe if 'Definition' in table.value)
    assert details.loc[details.Metric == 'P/E (trailing)', 'Value'].iloc[0] == '20.00'
    by_label(app.selectbox, 'Position metrics').set_value('Income & fees').run()
    assert not app.exception
    by_label(app.selectbox, 'Position metrics').set_value('Risk').run()
    assert not app.exception
    assert by_label(app.text_input, 'Risk benchmark ticker').value == 'IUSQ.DE'
    by_label(app.selectbox, 'Risk window (years)').set_value(1).run()
    assert not app.exception


def test_portfolio_analytics_scopes_fundamentals_and_risk(sample_data_dir):
    app = launch(sample_data_dir)
    by_label(app.checkbox, 'Show portfolio analytics').check().run()
    assert not app.exception
    by_label(app.checkbox, 'Load portfolio fundamentals').check().run()
    assert not app.exception
    table = next(item.value for item in app.dataframe if 'Eligible valued assets (EUR)' in item.value)
    assert table.loc[table.Metric == 'Trailing P/E · profitable direct equities', 'Value'].iloc[0] == '20.00'
    by_label(app.checkbox, 'Load historical risk').check().run()
    assert not app.exception
    assert any('known valued assets' in caption.value for caption in app.caption)
    assert by_label(app.metric, 'Covered-subportfolio beta').value != '—'
    by_label(app.text_input, 'Risk benchmark ticker').set_value('SYNTHETIC-BENCHMARK').run()
    assert not app.exception
    by_label(app.multiselect, 'Analytics accounts').set_value([]).run()
    assert not app.exception
    assert any('No held positions' in info.value for info in app.info)


def test_private_fee_edit_roundtrip_in_invented_workspace(tmp_path):
    from portfolio_app.fundamentals import load_fee_overrides
    (tmp_path / 'holdings.csv').write_text('id,name,ticker,isin,shares,instrument_type\nf,Invented fund,FFF,,1,etf\n')
    script = ('from pathlib import Path\n'
              'from portfolio_app.positions import read_snapshot\n'
              'from portfolio_app.position_list import render_position_list\n'
              f'p = Path({str(tmp_path / "holdings.csv")!r})\n'
              'render_position_list(p, read_snapshot(p), demo=True)\n')
    app = AppTest.from_string(script).run()
    by_label(app.checkbox, 'Show instrument metrics').check().run()
    assert not app.exception
    by_label(app.number_input, 'Annual fund fee (%)').set_value(.4)
    by_label(app.text_input, 'Fee source').set_value('Invented issuer factsheet')
    by_label(app.checkbox, 'Verified accumulating share class').check()
    by_label(app.button, 'Save private fee').click().run()
    assert not app.exception
    fee = load_fee_overrides(tmp_path / 'fund-fees.json')['ticker:FFF']
    assert fee.rate == .004 and fee.accumulating
    by_label(app.button, 'Remove private fee').click().run()
    assert not app.exception
    assert load_fee_overrides(tmp_path / 'fund-fees.json') == {}


def test_fundamental_fetch_deduplicates_account_rows(tmp_path, monkeypatch):
    from portfolio_app.fundamentals import Fundamentals
    (tmp_path / 'holdings.csv').write_text('id,name,ticker,shares,account\na,Invented A,AAA,1,First\na,Invented A,AAA,2,Second\n')
    calls = []
    def fetch(self, ticker):
        calls.append(ticker)
        return Fundamentals(ticker, 'equity')
    monkeypatch.setattr('portfolio_app.fundamentals.YahooFundamentalsProvider.fetch', fetch)
    snapshots = load_metrics(load_holdings(tmp_path / 'holdings.csv'), tmp_path)
    assert calls == ['AAA']
    assert set(snapshots) == {'a'}


def test_workspace_switch_resets_analytics_scope(tmp_path, sample_data_dir):
    # Two synthetic demo directories, no user's working files.
    import shutil
    second = tmp_path / 'second-demo'
    shutil.copytree(sample_data_dir, second)
    script = ('from pathlib import Path\nimport streamlit as st\n'
              'from portfolio_app.holdings import load_holdings\n'
              'from portfolio_app.analytics_ui import render_portfolio_analytics\n'
              f'directories = [Path({str(sample_data_dir)!r}), Path({str(second)!r})]\n'
              'index = st.selectbox("Test workspace", [0, 1])\n'
              'p = directories[index]\n'
              'render_portfolio_analytics(load_holdings(p / "holdings.csv"), p, [], demo=True)\n')
    app = AppTest.from_string(script).run()
    by_label(app.checkbox, 'Show portfolio analytics').check().run()
    by_label(app.multiselect, 'Analytics accounts').set_value([]).run()
    by_label(app.selectbox, 'Test workspace').set_value(1).run()
    assert not by_label(app.checkbox, 'Show portfolio analytics').value
    by_label(app.checkbox, 'Show portfolio analytics').check().run()
    assert len(by_label(app.multiselect, 'Analytics accounts').value) == 2
    assert not app.exception
