"""Recovery controls stay accessible when saved portfolio inputs are damaged."""
import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.demo import create_demo_data


@pytest.mark.parametrize('broken', ['portfolio.yaml', 'holdings.csv'])
def test_recovery_controls_survive_input_errors(tmp_path, broken):
    directory = create_demo_data(tmp_path / 'invented')
    (directory / broken).write_text('deliberately invalid synthetic input')
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.ui import render_app
from portfolio_app.prices import PriceService, StaticProvider
root = Path({str(directory)!r})
render_app(root, price_service=PriceService(StaticProvider(root / 'demo_prices.json')))
''').run()
    assert not app.exception
    assert app.error
    assert next(b for b in app.button if b.label == 'Restore backup').disabled is False
    next(b for b in app.button if b.label == 'Restore backup').click().run()
    assert not app.exception
    assert next(b for b in app.button if b.label == 'Cancel restore')


def test_demo_backup_actions_are_disabled(tmp_path):
    directory = create_demo_data(tmp_path / 'invented')
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.ui import render_app
render_app(Path({str(directory)!r}), demo=True)
''').run()
    assert not app.exception
    for label in ('Create backup', 'Restore backup'):
        assert next(b for b in app.button if b.label == label).disabled
