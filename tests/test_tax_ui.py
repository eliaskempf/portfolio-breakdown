"""Synthetic offline calculator navigation and persistence."""
from decimal import Decimal as D
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

from tax_sample import create_tax_sample
from test_ui import by_label, activate
from portfolio_app.tax_evidence import load_inputs


def launch(tmp_path):
    create_tax_sample(tmp_path)
    app = AppTest.from_string('from pathlib import Path\nfrom portfolio_app.ui import render_app\n'
        f'render_app(Path({str(tmp_path)!r}), demo=True)', default_timeout=20).run()
    assert not app.exception
    activate(app, 'Taxes')
    assert not app.exception and not app.error
    return app


def test_broker_selection_and_allowance_reset(tmp_path):
    app = launch(tmp_path)
    by_label(app.selectbox, 'Calculation year').set_value(2025).run()
    assert by_label(app.metric, 'Vorabpauschale').value == '€442.75'
    by_label(app.number_input, 'Available allowance · 2026 (€)').set_value(100.).run()
    assert by_label(app.metric, 'Estimated tax').value == '€55.36'
    by_label(app.multiselect, 'Broker / account').set_value(['Invented broker A']).run()
    assert by_label(app.number_input, 'Available allowance · 2026 (€)').value == 0
    assert by_label(app.metric, 'Vorabpauschale').value == '€354.20'
    by_label(app.multiselect, 'ETFs').set_value([by_label(app.multiselect, 'ETFs').value[0]]).run()
    assert by_label(app.metric, 'Vorabpauschale').value == '€177.10'
    assert not app.exception and not app.error


def test_save_review_and_forecast(tmp_path):
    app = launch(tmp_path)
    before = (tmp_path/'account-journal.json').read_bytes()
    by_label(app.selectbox, 'Calculation year').set_value(2026).run()
    assert by_label(app.metric, 'Vorabpauschale').value == '€560.00'
    by_label(app.selectbox, 'Church tax').set_value(D('.09')).run()
    by_label(app.text_input, 'Year-start price override').set_value('120')
    by_label(app.text_input, 'Evidence / assumption').set_value('Invented edited assumption')
    by_label(app.button, 'Save tax inputs').click().run()
    assert not app.exception and not app.error
    raw, _ = load_inputs(tmp_path)
    assert raw['funds']['2026:SYNTHETIC001']['start_price'] == '120'
    assert raw['settings']['church_rate'] == '.09' or D(raw['settings']['church_rate']) == D('.09')
    assert (tmp_path/'account-journal.json').read_bytes() == before


def test_inactive_tax_view_does_not_fetch_or_calculate(tmp_path):
    create_tax_sample(tmp_path)
    with patch('portfolio_app.tax_ui.render_taxes') as render:
        app = AppTest.from_string('from pathlib import Path\nfrom portfolio_app.ui import render_app\n'
            f'render_app(Path({str(tmp_path)!r}), demo=True)', default_timeout=20).run()
        assert not app.exception and not render.called


def test_inputs_survive_navigation(tmp_path):
    app = launch(tmp_path)
    by_label(app.selectbox, 'Calculation year').set_value(2025).run()
    by_label(app.number_input, 'Available allowance · 2026 (€)').set_value(50.).run()
    assert all(isinstance(key, str) for key in app.session_state['parsed_inputs'])
    activate(app, 'Overview')
    assert not app.exception
    activate(app, 'Taxes')
    assert by_label(app.selectbox, 'Calculation year').value == 2025
    assert by_label(app.number_input, 'Available allowance · 2026 (€)').value == 50
    assert not app.exception
