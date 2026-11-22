"""Recovery controls stay accessible when saved portfolio inputs are damaged."""
import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.demo_data.demo import create_demo_data


@pytest.mark.parametrize('broken', ['portfolio.yaml', 'holdings.csv'])
def test_recovery_controls_survive_input_errors(tmp_path, broken):
    directory = create_demo_data(tmp_path / 'invented')
    (directory / broken).write_text('deliberately invalid synthetic input')
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.interface.ui import render_app
from portfolio_app.market.prices import PriceService, StaticProvider
root = Path({str(directory)!r})
render_app(root, managed=True, price_service=PriceService(StaticProvider(root / 'demo_prices.json')))
''').run()
    assert not app.exception
    assert app.error
    next(b for b in app.button if b.label == 'Open workspace controls').click().run()
    assert app.session_state['app_menu'] and app.session_state['menu_workspace']
    assert next(b for b in app.button if b.label == 'Restore backup').disabled is False
    next(b for b in app.button if b.label == 'Restore backup').click().run()
    assert not app.exception
    assert next(b for b in app.button if b.label == 'Cancel restore')


def test_demo_explains_where_backups_are_available(tmp_path):
    directory = create_demo_data(tmp_path / 'invented')
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.interface.ui import render_app
render_app(Path({str(directory)!r}), demo=True)
''').run()
    assert not app.exception
    assert not {'Create backup', 'Restore backup'} & {button.label for button in app.button}
    assert any('Backups are available in My portfolio' in caption.value for caption in app.caption)


def test_direct_streamlit_exposes_only_supported_workspace_operations(tmp_path):
    directory = create_demo_data(tmp_path / 'invented')
    app = AppTest.from_string(f"""
from pathlib import Path
from portfolio_app.interface.ui import render_app
from portfolio_app.market.prices import PriceService, StaticProvider
root = Path({str(directory)!r})
render_app(root, price_service=PriceService(StaticProvider(root / 'demo_prices.json')))
""").run()
    assert not app.exception
    actions = {button.label: button for button in app.button}
    assert not actions['Create backup'].disabled
    assert actions['Restore backup'].disabled
    assert 'Stop application' not in actions
    assert any('desktop shortcut to restore' in caption.value for caption in app.caption)
