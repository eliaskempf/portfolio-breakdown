"""Analytics interaction checks in temporary, explicitly synthetic workspaces."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.analytics_service import load_metrics
from portfolio_app.holdings import load_holdings
from test_ui import activate, by_label, launch


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
    activate(app, 'Positions')
    assert by_label(app.get('button_group'), 'Position view').value == 'Holdings'
    assert not any(c.label == 'Show instrument metrics' for c in app.checkbox)
    by_label(app.get('button_group'), 'Position view').set_value('Valuation').run()
    assert not app.exception
    assert by_label(app.multiselect, 'Visible metrics').value == ['trailing_pe', 'forward_pe', 'fund_pe']
    app.session_state['position_edit_selected'] = 'position-0'
    app.session_state['position_edit_action'] = 'Details'
    app.session_state['position_edit_dialog'] = True
    app.run()
    by_label(app.get('button_group'), 'Position detail view').set_value('Key metrics').run()
    assert not app.exception
    assert by_label(app.metric, 'P/E (trailing)').value == '20.00'
    by_label(app.button, 'Close').click().run()
    by_label(app.get('button_group'), 'Position view').set_value('Income & fees').run()
    assert not app.exception
    by_label(app.get('button_group'), 'Position view').set_value('Risk').run()
    assert not app.exception
    assert by_label(app.text_input, 'Benchmark ticker').value == 'IUSQ.DE'
    by_label(app.selectbox, 'History window').set_value(1).run()
    assert not app.exception
    activate(app, 'Overview')
    activate(app, 'Positions')
    assert by_label(app.get('button_group'), 'Position view').value == 'Risk'
    assert by_label(app.selectbox, 'History window').value == 1
    by_label(app.get('button_group'), 'Position view').set_value('Valuation').run()
    by_label(app.multiselect, 'Visible metrics').set_value(['forward_pe']).run()
    by_label(app.get('button_group'), 'Position view').set_value('Income & fees').run()
    by_label(app.get('button_group'), 'Position view').set_value('Valuation').run()
    assert by_label(app.multiselect, 'Visible metrics').value == ['forward_pe']


def test_portfolio_analytics_uses_overview_scope_and_risk(sample_data_dir):
    from portfolio_app.allocation import migration_preview
    app = launch(sample_data_dir)
    activate(app, 'Overview')
    by_label(app.get('button_group'), 'Overview view').set_value('Analytics').run()
    assert not app.exception
    assert not any(c.label in {'Show portfolio analytics', 'Load portfolio fundamentals', 'Load historical risk'} for c in app.checkbox)
    assert by_label(app.metric, 'Direct-stock P/E').value == '20.00'
    by_label(app.button, 'Calculate risk').click().run()
    assert not app.exception
    assert any('known valued assets' in caption.value for caption in app.caption)
    assert by_label(app.metric, 'Beta').value != '—'
    by_label(app.text_input, 'Benchmark ticker').set_value('SYNTHETIC-BENCHMARK').run()
    assert not app.exception
    activate(app, 'Positions')
    activate(app, 'Overview')
    assert by_label(app.get('button_group'), 'Overview view').value == 'Analytics'
    assert by_label(app.text_input, 'Benchmark ticker').value == 'SYNTHETIC-BENCHMARK'
    config, _ = migration_preview(load_holdings(sample_data_dir / 'holdings.csv'))
    category = config.buckets[0].id
    by_label(app.selectbox, 'Category').set_value(category).run()
    assert app.session_state['strategic_category'] == category
    assert by_label(app.get('button_group'), 'Overview view').value == 'Analytics'
    assert not app.exception
    by_label(app.multiselect, 'Accounts').set_value([]).run()
    assert not app.exception
    assert any('No held positions' in info.value for info in app.info)


def test_private_fee_edit_roundtrip_in_invented_workspace(tmp_path):
    from portfolio_app.fundamentals import load_fee_overrides
    (tmp_path / 'holdings.csv').write_text('id,name,ticker,isin,shares,instrument_type\nf,Invented fund,FFF,,1,etf\n')
    script = ('from pathlib import Path\n'
              'from portfolio_app.positions import read_snapshot\n'
              'from portfolio_app.position_metrics_ui import render_instrument_metrics\n'
              f'p = Path({str(tmp_path / "holdings.csv")!r})\n'
              'render_instrument_metrics(read_snapshot(p).holdings.iloc[0], p.parent, demo=True)\n')
    app = AppTest.from_string(script).run()
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


def test_workspace_switch_resets_analytics_options(tmp_path, sample_data_dir):
    import shutil
    second = tmp_path / 'second-demo'
    shutil.copytree(sample_data_dir, second)
    script = ('from pathlib import Path\nimport streamlit as st\n'
              'from portfolio_app.holdings import load_holdings\n'
              'from portfolio_app.prices import PriceService, StaticProvider\n'
              'from portfolio_app.portfolio import prepare_portfolio\n'
              'from portfolio_app.portfolio_analytics_ui import render_portfolio_analytics\n'
              f'directories = [Path({str(sample_data_dir)!r}), Path({str(second)!r})]\n'
              'index = st.selectbox("Test workspace", [0, 1])\n'
              'p = directories[index]\n'
              'valued = prepare_portfolio(load_holdings(p / "holdings.csv"), PriceService(StaticProvider(p / "demo_prices.json")))\n'
              'render_portfolio_analytics(valued, p, [], demo=True)\n')
    app = AppTest.from_string(script, default_timeout=15).run()
    by_label(app.multiselect, 'Accounts').set_value([]).run()
    by_label(app.selectbox, 'Test workspace').set_value(1).run()
    assert len(by_label(app.multiselect, 'Accounts').value) == 2
    assert not app.exception
