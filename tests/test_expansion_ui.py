"""Synthetic Streamlit smoke tests for allocation setup and scoped workflows."""
import shutil
from test_ui import launch, by_label
from portfolio_app.allocation import Allocation, Bucket, migrate, migration_preview
from portfolio_app.positions import read_snapshot


def workspace(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / 'demo_prices.json', tmp_path)
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,ticker,shares,portfolio,target_allocation\na,Invented A,NVDA,1,Active,0.6\nb,Invented B,TSM,1,Active,0.4\nc,Invented C,ENR.DE,10,Reserve,1\n')
    return path


def test_review_enable_and_reopen_allocation(tmp_path, sample_data_dir):
    path = workspace(tmp_path, sample_data_dir)
    app = launch(tmp_path)
    by_label(app.radio, 'Position action').set_value('Strategic allocation').run()
    assert not app.exception
    by_label(app.button, 'Enable reviewed allocation').click().run()
    assert not app.exception
    assert (tmp_path / 'allocation.yaml').exists()
    assert 'position_key' in read_snapshot(path).holdings
    reopened = launch(tmp_path)
    assert not reopened.exception
    assert any(item.value == 'Strategic allocation' for item in reopened.subheader)


def test_scoped_plans_balance_save_and_macro_overview(tmp_path, sample_data_dir):
    path = workspace(tmp_path, sample_data_dir)
    snap = read_snapshot(path)
    _, preview = migration_preview(snap.holdings)
    config = Allocation((Bucket('bucket-1', 'Active', target=.4), Bucket('bucket-2', 'Reserve', target=.6)))
    migrate(path, config, preview, expected_revision=snap.revision)
    app = launch(tmp_path)
    assert not app.exception
    by_label(app.button, 'Save replacement balances').click().run()
    assert not app.exception
    by_label(app.button, 'Calculate portfolio contribution').click().run()
    assert not app.exception
    assert not app.error
    by_label(app.radio, 'Planning scope').set_value('Within a bucket').run()
    assert not app.exception
    by_label(app.selectbox, 'Rebalancing mode').set_value('Allocate new money').run()
    by_label(app.button, 'Calculate rebalance').click().run()
    assert not app.exception
    assert any(item.value == 'Portfolio impact' for item in app.subheader)


def test_optional_stock_view_and_manual_position_entry(tmp_path, sample_data_dir):
    workspace(tmp_path, sample_data_dir)
    app = launch(tmp_path)
    by_label(app.checkbox, 'Show stock-only company exposure').check().run()
    assert not app.exception
    assert any('stock-universe total is unknown' in item.value for item in app.info)
    by_label(app.radio, 'Position action').set_value('Add position').run()
    by_label(app.text_input, 'Instrument name').set_value('Invented physical holding')
    by_label(app.number_input, 'Quantity held (total)').set_value(2.5)
    by_label(app.selectbox, 'Instrument type').set_value('physical')
    by_label(app.text_input, 'Quantity unit').set_value('grams')
    by_label(app.number_input, 'Manual unit price (optional)').set_value(12.)
    by_label(app.text_input, 'Manual price currency').set_value('EUR')
    by_label(app.text_input, 'Manual price date (YYYY-MM-DD)').set_value('2026-01-01')
    by_label(app.button, 'Save position').click().run()
    assert not app.exception
    assert not app.error
    assert read_snapshot(tmp_path / 'holdings.csv').holdings.shares.iloc[-1] == 2.5
