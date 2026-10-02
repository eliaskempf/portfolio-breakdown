"""Synthetic onboarding UI, including delayed enrichment and workspace isolation."""
from io import BytesIO

from streamlit.testing.v1 import AppTest
from test_ui import activate, by_label

from portfolio_app.demo import create_demo_data
from portfolio_app.positions import read_snapshot


class Upload(BytesIO):
    name = 'invented-holdings.csv'


def launch(tmp_path, monkeypatch, content=None, *, workspaces=False):
    if content is None:
        content = b'Name;Quantity;WKN;Kurs;Kursdatum;Waehrung\nInvented fund;2,5;000123;12,00;2026-01-02;EUR\n'
    monkeypatch.setattr('portfolio_app.import_ui.st.file_uploader', lambda *args, **kwargs: [Upload(content)])
    directory = tmp_path / 'portfolio'
    suffix = ''
    if workspaces:
        demo = tmp_path / 'demo'
        create_demo_data(demo)
        suffix = f', demo_dir=Path({str(demo)!r})'
    app = AppTest.from_string(
        'from pathlib import Path\n'
        'from portfolio_app.ui import render_app\n'
        'from portfolio_app.prices import PriceService, UnavailableProvider\n'
        f'render_app(Path({str(directory)!r}), demo=True, price_service=PriceService(UnavailableProvider()){suffix})\n',
        default_timeout=20,
    ).run()
    if workspaces:
        by_label(app.radio, 'Portfolio workspace').set_value('My portfolio').run()
    by_label(app.button, 'Import portfolio — experimental').click().run()
    return app, directory / 'holdings.csv'


def confirm_units(app):
    return next(box for box in app.checkbox if box.label.startswith('Quantities are')).check().run()


def test_import_snapshots_then_allocation_and_live_linking(tmp_path, monkeypatch):
    app, path = launch(tmp_path, monkeypatch)
    assert not app.exception
    assert not path.exists()
    assert by_label(app.button, 'Import reviewed positions').disabled
    confirm_units(app)
    assert not app.exception
    assert not by_label(app.button, 'Import reviewed positions').disabled
    by_label(app.button, 'Import reviewed positions').click().run()
    assert not app.exception
    stored = read_snapshot(path).holdings
    assert stored.shares.tolist() == [2.5]
    assert stored.manual_price.tolist() == [12.]
    assert app.session_state['main_tabs'] == 'Overview'
    by_label(app.button, 'Set up allocation').click().run()
    assert not app.exception
    assert app.session_state['main_tabs'] == 'Rebalance'
    assert app.session_state['rebalance_tabs'] == 'Targets'
    by_label(app.button, 'Connect live prices').click().run()
    assert not app.exception
    by_label(app.text_input, 'Search by ISIN, WKN, ticker or name').set_value('NVDA').run()
    by_label(app.button, 'Search listings').click().run()
    by_label(app.selectbox, 'Price listing').set_value(0).run()
    by_label(app.checkbox, 'Switch to live prices and clear dated manual prices for this instrument').check()
    by_label(app.checkbox, 'I confirm this is the same instrument/share class and intended exchange listing').check().run()
    by_label(app.button, 'Save listing').click().run()
    assert not app.exception
    linked = read_snapshot(path).holdings
    assert linked.ticker.tolist() == ['NVDA']
    assert linked.manual_price.isna().all()
    assert linked.position_key.tolist() == stored.position_key.tolist()


def test_cancel_leaves_no_holdings_and_clears_private_draft(tmp_path, monkeypatch):
    app, path = launch(tmp_path, monkeypatch)
    confirm_units(app)
    by_label(app.button, 'Cancel import').click().run()
    assert not app.exception and not path.exists()
    assert not any(key.startswith('import_') for key in app.session_state.filtered_state)
    assert not any(key.startswith('import_') for key in app.session_state['view_editor_drafts'])


def test_workspace_switch_clears_import_state(tmp_path, monkeypatch):
    app, path = launch(tmp_path, monkeypatch, workspaces=True)
    confirm_units(app)
    assert any(key.startswith('import_') for key in app.session_state.filtered_state)
    by_label(app.radio, 'Portfolio workspace').set_value('Demo portfolio').run()
    assert not app.exception and not path.exists()
    assert not any(key.startswith('import_') for key in app.session_state.filtered_state)


def test_optional_prices_and_costs_do_not_block_minimal_import(tmp_path, monkeypatch):
    app, path = launch(tmp_path, monkeypatch, b'Name;Quantity\nInvented asset;0,125\n')
    confirm_units(app)
    by_label(app.button, 'Import reviewed positions').click().run()
    assert not app.exception
    stored = read_snapshot(path).holdings
    assert stored.shares.tolist() == [.125]
    assert stored.manual_price.isna().all()
    assert stored.acquisition_price.isna().all()
    original = path.read_bytes()
    activate(app, 'Positions')
    by_label(app.get('button_group'), 'Position tools').set_value('Import portfolio').run()
    assert not app.exception
    assert any('empty portfolio' in item.value for item in app.info)
    assert original == path.read_bytes()


def test_source_row_correction_and_explicit_subtotal_exclusion(tmp_path, monkeypatch):
    app, path = launch(tmp_path, monkeypatch, b'Name;Quantity\nInvented asset;not-a-number\nTotal;2,5\n')
    confirm_units(app)
    assert by_label(app.button, 'Import reviewed positions').disabled
    key = next(key for key in app.session_state.filtered_state if key.startswith('import_') and key.endswith('_rows'))
    app.session_state[key] = {'edited_rows': {0: {'Quantity': '2,5'}, 1: {'Include': False}},
                              'added_rows': [], 'deleted_rows': []}
    app.run()
    assert not app.exception
    assert not by_label(app.button, 'Import reviewed positions').disabled
    by_label(app.button, 'Import reviewed positions').click().run()
    assert not app.exception
    stored = read_snapshot(path).holdings
    assert stored.shares.tolist() == [2.5]
    assert stored.name.tolist() == ['Invented asset']
