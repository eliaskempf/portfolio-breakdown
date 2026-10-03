"""First-use choices use isolated, invented workspaces and existing save flows."""
from portfolio_app.demo import create_demo_data
from portfolio_app.positions import read_snapshot
from test_startup import launch_workspaces
from test_ui import by_label


def add_category(app, name, target=None):
    by_label(app.text_input, 'Category name').set_value(name)
    if target is not None:
        by_label(app.number_input, 'Target (%) · optional').set_value(target)
    by_label(app.button, 'Add category').click().run()
    assert not app.exception


def test_welcome_demo_and_manual_save_are_isolated(tmp_path):
    personal = tmp_path / 'invented-personal'
    demo = create_demo_data(tmp_path / 'demo')
    app = launch_workspaces(personal, demo)
    assert not app.exception and not app.tabs
    assert {button.label for button in app.button} >= {'Explore demo', 'Start my portfolio'}
    assert 'Import holdings' not in {button.label for button in app.button}
    by_label(app.button, 'Explore demo').click().run()
    assert not app.exception
    assert by_label(app.selectbox, 'Portfolio workspace').value == 'Demo portfolio'
    assert app.metric[0].value == '€100,000.00'
    assert not (personal / 'holdings.csv').exists()
    by_label(app.selectbox, 'Portfolio workspace').set_value('My portfolio').run()
    by_label(app.button, 'Start my portfolio').click().run()
    by_label(app.button, 'Skip setup').click().run()
    by_label(app.button, 'Add position').click().run()
    assert not app.exception
    by_label(app.text_input, 'Instrument name').set_value('Invented first position')
    by_label(app.number_input, 'Quantity held (total)').set_value(2.)
    by_label(app.button, 'Save position').click().run()
    assert not app.exception
    assert read_snapshot(personal / 'holdings.csv').holdings.shares.tolist() == [2.]
    assert read_snapshot(demo / 'holdings.csv').holdings.shares.iloc[0] == 450
    restarted = launch_workspaces(personal, demo)
    assert not restarted.exception and restarted.tabs
    assert 'Explore demo' not in {button.label for button in restarted.button}


def test_import_choice_and_workspace_switch_clear_first_use_state(tmp_path):
    personal = tmp_path / 'invented-personal'
    demo = create_demo_data(tmp_path / 'demo')
    app = launch_workspaces(personal, demo)
    by_label(app.button, 'Start my portfolio').click().run()
    by_label(app.button, 'Skip setup').click().run()
    by_label(app.button, 'Import portfolio — experimental').click().run()
    assert not app.exception
    assert app.session_state['main_tabs'] == 'Positions'
    assert by_label(app.get('button_group'), 'Position tools').value == 'Import portfolio'
    assert app.get('file_uploader')
    assert not (personal / 'holdings.csv').exists()
    by_label(app.selectbox, 'Portfolio workspace').set_value('Demo portfolio').run()
    by_label(app.selectbox, 'Portfolio workspace').set_value('My portfolio').run()
    assert not app.exception and not app.tabs
    assert 'onboarding_started' not in app.session_state.filtered_state
    assert not any(key.startswith('import_') for key in app.session_state.filtered_state)


def test_live_demo_uses_normal_market_mode_and_preserves_workspace_isolation(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from test_ui import activate
    from portfolio_app.history import HistoryResult
    from types import SimpleNamespace
    personal = tmp_path / 'invented-personal'
    live = create_demo_data(tmp_path / 'live', live=True)
    fixture = create_demo_data(tmp_path / 'offline')
    calls = []
    monkeypatch.setattr('portfolio_app.etf_refresh.coordinator.schedule',
                        lambda *a, **kw: calls.append(kw['demo']))
    app = AppTest.from_string(
        'from pathlib import Path\n'
        'from portfolio_app.ui import render_app\n'
        'from portfolio_app.prices import PriceService, StaticProvider\n'
        f'prices = PriceService(StaticProvider(Path({str(fixture / "demo_prices.json")!r})))\n'
        f'render_app(Path({str(personal)!r}), demo_dir=Path({str(live)!r}), demo=True, price_service=prices)\n',
        default_timeout=15,
    ).run()
    assert not app.exception and app.tabs
    assert calls and not any(calls)
    assert not by_label(app.button, 'Refresh prices').disabled
    assert any('Public market data' in caption.value for caption in app.caption)
    initial = (live / 'holdings.csv').read_bytes()
    by_label(app.button, 'Refresh prices').click().run()
    assert (live / 'holdings.csv').read_bytes() == initial
    assert not (personal / 'holdings.csv').exists()
    # Opening a live demo position must use the ordinary history path.
    requested = []
    def history(directory):
        requested.append(directory)
        return SimpleNamespace(get=lambda *a, **kw: HistoryResult(note='Injected public-history path'),
                               pending=lambda *a: False)
    monkeypatch.setattr('portfolio_app.position_detail.history_for', history)
    snapshot = read_snapshot(live / 'holdings.csv')
    app.session_state['position_edit_selected'] = snapshot.holdings.position_id.iloc[0]
    app.session_state['position_edit_dialog'] = True
    app.session_state['position_edit_action'] = 'Details'
    activate(app, 'Positions')
    assert not app.exception
    assert requested == [live]
    assert any('Injected public-history path' in info.value for info in app.info)
    by_label(app.selectbox, 'Portfolio workspace').set_value('My portfolio').run()
    assert not app.exception and not app.tabs


def test_recovery_controls_remain_available_with_invalid_holdings(tmp_path):
    from streamlit.testing.v1 import AppTest
    (tmp_path / 'holdings.csv').write_text('id,name,shares\ninvented,Invented broken input,invalid\n', encoding='utf-8')
    app = AppTest.from_string(
        'from pathlib import Path\nfrom portfolio_app.ui import render_app\n'
        f'render_app(Path({str(tmp_path)!r}), demo=True)\n', default_timeout=15,
    ).run()
    assert not app.exception and app.error
    assert {'Open data folder', 'Stop application'} <= {button.label for button in app.button}


def test_guided_categories_targets_and_first_physical_position(tmp_path):
    from portfolio_app.allocation import load_allocation
    from portfolio_app.prices import PriceService, UnavailableProvider
    from portfolio_app.valuation import value_holdings
    personal = tmp_path / 'invented-personal'
    app = launch_workspaces(personal, create_demo_data(tmp_path / 'demo'))
    by_label(app.button, 'Start my portfolio').click().run()
    assert by_label(app.text_input, 'Category name').value == ''
    add_category(app, 'Invented equities', 80.)
    add_category(app, 'Invented gold', 30.)
    by_label(app.button, 'Save categories & continue').click().run()
    assert app.error and not (personal / 'allocation.yaml').exists()
    by_label(app.number_input, 'Target 2 (%)').set_value(20.).run()
    assert not (personal / 'allocation.yaml').exists()
    by_label(app.button, 'Keep editing').click().run()
    assert by_label(app.text_input, 'Category 1').value == 'Invented equities'
    assert by_label(app.number_input, 'Target 2 (%)').value == 20.
    by_label(app.button, 'Save categories & continue').click().run()
    assert not app.exception
    config = load_allocation(personal / 'allocation.yaml')
    assert [b.target for b in config.buckets] == [.8, .2]
    by_label(app.get('button_group'), 'Position type').set_value('Physical asset').run()
    by_label(app.radio, 'Valuation method').set_value('Manual price').run()
    by_label(app.text_input, 'Instrument name').set_value('Invented gold coins')
    by_label(app.number_input, 'Quantity held (total)').set_value(2.5)
    by_label(app.number_input, 'Current price per troy oz (optional)').set_value(2000.)
    gold = next(b.id for b in config.buckets if b.name == 'Invented gold')
    by_label(app.selectbox, 'Category').set_value(gold)
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    saved = read_snapshot(personal / 'holdings.csv').holdings
    row = saved.iloc[0]
    assert row.quantity_unit == 'troy oz' and row.instrument_type == 'physical'
    assert row.ticker == '' and row.bucket_id == gold
    assert row.manual_price == 2000.
    assert value_holdings(saved, PriceService(UnavailableProvider())).current_value_eur.iloc[0] == 5000.
    assert app.session_state['onboarding_step'] == 'done'
    assert not list(personal.glob('*.json'))


def test_guided_optional_targets_and_finish_later(tmp_path):
    from portfolio_app.allocation import load_allocation
    personal = tmp_path / 'invented-personal'
    app = launch_workspaces(personal, create_demo_data(tmp_path / 'demo'))
    by_label(app.button, 'Start my portfolio').click().run()
    add_category(app, 'Invented reserve')
    by_label(app.button, 'Save categories & continue').click().run()
    assert not app.exception
    assert all(b.target is None for b in load_allocation(personal / 'allocation.yaml').buckets)
    by_label(app.button, 'Finish later').click().run()
    assert not app.exception and not (personal / 'holdings.csv').exists()
    # Saved categories survive restarting an empty workspace; they are not overwritten.
    before = (personal / 'allocation.yaml').read_bytes()
    app = launch_workspaces(personal, create_demo_data(tmp_path / 'second-demo'))
    by_label(app.button, 'Start my portfolio').click().run()
    assert not app.exception and by_label(app.selectbox, 'Category')
    assert (personal / 'allocation.yaml').read_bytes() == before


def test_physical_unit_change_clears_amounts_and_edit_keeps_unit(tmp_path):
    from test_ui import position_action
    from portfolio_app.prices import PriceService, UnavailableProvider
    from portfolio_app.valuation import value_holdings
    personal = tmp_path / 'invented-personal'
    app = launch_workspaces(personal, create_demo_data(tmp_path / 'demo'))
    by_label(app.button, 'Start my portfolio').click().run()
    by_label(app.button, 'Skip setup').click().run()
    by_label(app.button, 'Add position').click().run()
    by_label(app.get('button_group'), 'Position type').set_value('Physical asset').run()
    by_label(app.radio, 'Valuation method').set_value('Manual price').run()
    by_label(app.number_input, 'Quantity held (total)').set_value(2.)
    by_label(app.number_input, 'Current price per troy oz (optional)').set_value(2000.).run()
    by_label(app.selectbox, 'Quantity unit').set_value('grams').run()
    assert by_label(app.number_input, 'Quantity held (total)').value == 0
    assert by_label(app.number_input, 'Current price per grams (optional)').value is None
    by_label(app.number_input, 'Quantity held (total)').set_value(10.)
    by_label(app.number_input, 'Current price per grams (optional)').set_value(60.)
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    saved = read_snapshot(personal / 'holdings.csv').holdings
    assert value_holdings(saved, PriceService(UnavailableProvider())).current_value_eur.iloc[0] == 600.
    position_action(app, 'Edit position')
    assert by_label(app.selectbox, 'Quantity unit').disabled
    assert by_label(app.selectbox, 'Quantity unit').value == 'grams'
    by_label(app.button, 'Save position').click().run()
    assert not app.exception
    assert read_snapshot(personal / 'holdings.csv').holdings.manual_price.iloc[0] == 60.


def test_existing_physical_holding_with_unknown_unit_is_not_assigned_ounces(tmp_path):
    from streamlit.testing.v1 import AppTest
    path = tmp_path / 'holdings.csv'
    path.write_text('id,name,shares,instrument_type\nmetal,Invented unspecified metal,10,physical\n', encoding='utf-8')
    app = AppTest.from_string(
        'from pathlib import Path\nimport streamlit as st\n'
        'from portfolio_app.positions import read_snapshot\n'
        'from portfolio_app.position_ui import render_position_form\n'
        f'path=Path({str(path)!r})\n'
        'st.session_state["position_edit_selected"]="position-0"\n'
        'render_position_form(path,read_snapshot(path),[],action="Edit position")\n', default_timeout=15,
    ).run()
    assert not app.exception
    assert by_label(app.text_input, 'Quantity unit').value == ''
    by_label(app.button, 'Save position').click().run()
    assert not app.exception
    row = read_snapshot(path).holdings.iloc[0]
    assert row.quantity_unit == '' and row.shares == 10.


def test_guided_confirmation_saves_only_after_continue(tmp_path):
    from portfolio_app.allocation import load_allocation
    personal = tmp_path / 'invented-personal'
    app = launch_workspaces(personal, create_demo_data(tmp_path / 'demo'))
    by_label(app.button, 'Start my portfolio').click().run()
    add_category(app, 'Invented core', 100.)
    assert not (personal / 'allocation.yaml').exists()
    by_label(app.button, 'Continue to first position').click().run()
    assert not app.exception
    assert load_allocation(personal / 'allocation.yaml').buckets[0].target == 1.


def test_guided_duplicate_remove_partial_targets_and_skip(tmp_path):
    personal = tmp_path / 'invented-personal'
    app = launch_workspaces(personal, create_demo_data(tmp_path / 'demo'))
    by_label(app.button, 'Start my portfolio').click().run()
    add_category(app, 'Invented core', 60.)
    add_category(app, ' invented CORE ')
    assert app.error and len(app.session_state['onboarding_rows']) == 1
    add_category(app, 'Invented reserve')
    app.button(key='onboarding_remove_0').click().run()
    assert by_label(app.text_input, 'Category 1').value == 'Invented reserve'
    by_label(app.button, 'Skip setup').click().run()
    assert not app.exception and not (personal / 'allocation.yaml').exists()


def test_physical_gold_spot_save_edit_and_explicit_manual_switch(tmp_path):
    from portfolio_app.prices import PriceService
    from portfolio_app.valuation import value_holdings
    from test_ui import position_action
    from datetime import datetime, timezone
    from types import SimpleNamespace
    personal = tmp_path / 'invented-gold'
    app = launch_workspaces(personal, create_demo_data(tmp_path / 'demo'))
    by_label(app.button, 'Start my portfolio').click().run()
    by_label(app.button, 'Skip setup').click().run()
    by_label(app.button, 'Add position').click().run()
    by_label(app.get('button_group'), 'Position type').set_value('Physical asset').run()
    assert by_label(app.radio, 'Valuation method').value == 'Gold spot price'
    assert set(by_label(app.selectbox, 'Quantity unit').options) == {'troy oz', 'grams', 'kg'}
    by_label(app.selectbox, 'Quantity unit').set_value('kg').run()
    by_label(app.number_input, 'Quantity held (total)').set_value(.1)
    by_label(app.text_input, 'Instrument name').set_value('Invented gold weight')
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    row = read_snapshot(personal / 'holdings.csv').holdings.iloc[0]
    assert row.price_source == 'gold_spot' and row.quantity_unit == 'kg'
    assert row.shares == .1 and row.ticker == ''
    position_action(app, 'Edit position')
    assert by_label(app.radio, 'Valuation method').value == 'Gold spot price'
    assert by_label(app.selectbox, 'Quantity unit').disabled
    by_label(app.radio, 'Valuation method').set_value('Manual price').run()
    by_label(app.number_input, 'Current price per kg (optional)').set_value(60000.)
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    saved = read_snapshot(personal / 'holdings.csv').holdings
    assert saved.price_source.tolist() == ['']
    prices = PriceService(SimpleNamespace(price=lambda _: None), now=lambda: datetime.now(timezone.utc))
    assert value_holdings(saved, prices).current_value_eur.tolist() == [6000.]
    position_action(app, 'Edit position')
    assert by_label(app.radio, 'Valuation method').value == 'Manual price'
    by_label(app.radio, 'Valuation method').set_value('Gold spot price').run()
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    saved = read_snapshot(personal / 'holdings.csv').holdings
    assert saved.price_source.tolist() == ['gold_spot'] and saved.manual_price.isna().all()


def test_new_manual_units_cannot_be_reinterpreted_as_gold_ounces(tmp_path):
    app = launch_workspaces(tmp_path / 'invented-gold', create_demo_data(tmp_path / 'demo'))
    by_label(app.button, 'Start my portfolio').click().run()
    by_label(app.button, 'Skip setup').click().run()
    by_label(app.button, 'Add position').click().run()
    by_label(app.get('button_group'), 'Position type').set_value('Physical asset').run()
    by_label(app.radio, 'Valuation method').set_value('Manual price').run()
    by_label(app.selectbox, 'Quantity unit').set_value('units').run()
    by_label(app.number_input, 'Quantity held (total)').set_value(10.).run()
    by_label(app.radio, 'Valuation method').set_value('Gold spot price').run()
    assert not app.exception
    assert by_label(app.number_input, 'Quantity held (total)').value == 0.
    assert by_label(app.selectbox, 'Quantity unit').value == 'troy oz'
