"""Synthetic offline calculator navigation and persistence."""
from decimal import Decimal as D
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

from tax_sample import create_tax_sample
from test_ui import by_label, activate
from portfolio_app.taxes.tax_evidence import load_inputs


def launch(tmp_path):
    create_tax_sample(tmp_path)
    app = AppTest.from_string('from pathlib import Path\nfrom portfolio_app.interface.ui import render_app\n'
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
    assert any('Allowance reset to €0' in c.value for c in app.caption)
    assert any('2 of 3 fund holdings selected' in c.value for c in app.caption)
    assert by_label(app.number_input, 'Available allowance · 2026 (€)').value == 0
    assert by_label(app.metric, 'Vorabpauschale').value == '€354.20'
    by_label(app.multiselect, 'ETFs').set_value([by_label(app.multiselect, 'ETFs').value[0]]).run()
    assert by_label(app.metric, 'Vorabpauschale').value == '€177.10'
    assert not app.exception and not app.error


def test_refresh_requires_a_selected_fund_with_a_ticker(tmp_path, monkeypatch):
    create_tax_sample(tmp_path)
    monkeypatch.setattr('portfolio_app.interface.views.taxes.tax_ui.request_market', lambda *a, **kw: False)
    app = AppTest.from_string(
        'from pathlib import Path\n'
        'from portfolio_app.ledger.account_journal import load_journal\n'
        'from portfolio_app.portfolio.positions import read_snapshot\n'
        'from portfolio_app.interface.views.taxes.tax_ui import render_taxes\n'
        f'root = Path({str(tmp_path)!r})\n'
        "render_taxes(root, read_snapshot(root / 'holdings.csv').holdings, load_journal(root)[0])"
    ).run()
    assert not app.exception
    assert not by_label(app.button, 'Refresh').disabled
    by_label(app.multiselect, 'ETFs').set_value([]).run()
    assert by_label(app.button, 'Refresh').disabled
    assert 'linked ticker' in by_label(app.button, 'Refresh').help


def test_tax_header_reflects_effective_price_bases(tmp_path):
    from portfolio_app.taxes.tax_evidence import save_inputs
    app = launch(tmp_path)
    by_label(app.selectbox, 'Calculation year').set_value(2025).run()
    inputs, revision = load_inputs(tmp_path)
    inputs['funds']['2025:SYNTHETIC001']['price_basis'] = 'Redemption prices'
    save_inputs(tmp_path, inputs, revision)
    app.run()
    assert any('Mixed price bases' in item.value for item in app.caption)
    inputs, revision = load_inputs(tmp_path)
    inputs['funds']['2025:SYNTHETIC002']['price_basis'] = 'Redemption prices'
    save_inputs(tmp_path, inputs, revision)
    app.run()
    assert any('Historical estimate · Redemption prices' in item.value for item in app.caption)


def test_save_review_and_forecast(tmp_path):
    app = launch(tmp_path)
    before = (tmp_path/'account-journal.json').read_bytes()
    by_label(app.selectbox, 'Calculation year').set_value(2026).run()
    assert by_label(app.metric, 'Vorabpauschale').value == '€560.00'
    by_label(app.selectbox, 'Church tax').set_value(D('.09')).run()
    by_label(app.button, 'Edit inputs').click().run()
    by_label(app.text_input, 'Year-start price override').set_value('120')
    by_label(app.text_input, 'Evidence / assumption').set_value('Invented edited assumption')
    by_label(app.button, 'Save tax inputs').click().run()
    assert not app.exception and not app.error
    raw, _ = load_inputs(tmp_path)
    assert raw['funds']['2026:SYNTHETIC001']['start_price'] == '120'
    assert raw['settings']['church_rate'] == '.09' or D(raw['settings']['church_rate']) == D('.09')
    assert (tmp_path/'account-journal.json').read_bytes() == before


def test_cancel_fund_input_changes_preserves_saved_values(tmp_path):
    app = launch(tmp_path)
    before = load_inputs(tmp_path)
    by_label(app.button, 'Edit inputs').click().run()
    by_label(app.text_input, 'Year-start price override').set_value('987')
    by_label(app.button, 'Cancel').click().run()
    assert not app.exception and load_inputs(tmp_path) == before
    by_label(app.button, 'Edit inputs').click().run()
    assert by_label(app.text_input, 'Year-start price override').value != '987'


def test_inactive_tax_view_does_not_fetch_or_calculate(tmp_path):
    create_tax_sample(tmp_path)
    with patch('portfolio_app.interface.views.taxes.tax_ui.render_taxes') as render:
        app = AppTest.from_string('from pathlib import Path\nfrom portfolio_app.interface.ui import render_app\n'
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


def test_default_view_keeps_optional_inputs_out_of_main_form(tmp_path):
    app = launch(tmp_path)
    assert not app.text_input
    assert len(app.metric) == 3


def test_accumulating_funds_calculate_automatically_with_existing_metadata(tmp_path, monkeypatch):
    from portfolio_app.analytics.fundamentals import FundFee
    from portfolio_app.taxes.tax_evidence import save_inputs
    app = launch(tmp_path)
    raw, revision = load_inputs(tmp_path)
    for value in raw['funds'].values():
        value.pop('distribution_mode')
    save_inputs(tmp_path, raw, revision)
    policy = FundFee(.001, 'https://example.invalid/invented-policy', '2026-01-01', accumulating=True)
    monkeypatch.setattr('portfolio_app.analytics.fundamentals.PUBLIC_FEES', dict(SYNTHETIC001=policy, SYNTHETIC002=policy))
    app.run()
    assert not app.exception and not app.error
    assert by_label(app.metric, 'Vorabpauschale').value == '€560.00'
    assert not any(b.label == 'Resolve missing inputs' for b in app.button)
    by_label(app.selectbox, 'Calculation year').set_value(2025).run()
    assert by_label(app.metric, 'Vorabpauschale').value == '€442.75'
    assert not any(b.label == 'Resolve missing inputs' for b in app.button)


def test_tax_interactions_reuse_price_index_until_evidence_changes(tmp_path, monkeypatch):
    from portfolio_app.interface.views.taxes import tax_ui
    from portfolio_app.performance.history_observations import Observation, ObservationStore, save_observations
    snapshots = []
    original = tax_ui.observation_market
    def recorded(item, index, year, boundary):
        snapshots.append(index)
        return original(item, index, year, boundary)
    monkeypatch.setattr(tax_ui, 'observation_market', recorded)
    app = launch(tmp_path)
    initial = snapshots[0]
    by_label(app.selectbox, 'Calculation year').set_value(2025).run()
    assert all(index is initial for index in snapshots)
    new_price = Observation('invented', 'price', 'invented-position', '2025-01-02', D('90'), 'EUR')
    save_observations(tmp_path, ObservationStore((new_price,)), None)
    app.run()
    assert not app.exception
    assert snapshots[-1] is not initial
    assert snapshots[-1]['invented-position'] == (new_price,)


def test_tax_candidates_depend_on_fund_identities_not_constituents(tmp_path, monkeypatch):
    from portfolio_app.interface.views.taxes import tax_ui
    original = tax_ui.candidates
    calls = []
    def counted(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(tax_ui, 'candidates', counted)
    app = launch(tmp_path)
    etfs = tmp_path / 'etfs'
    etfs.mkdir(exist_ok=True)
    (etfs / 'invented.yaml').write_text('fund_id: invented\nname: Invented fund\nisin: ZZ0000000001\n'
        'tickers: [INVENTED]\nas_of: 2025-10-01\nsource: Invented data\nholdings_file: invented.csv\n')
    path = etfs / 'invented.csv'
    path.write_text('constituent_id,name,ticker,isin,weight\na,Invented A,,,0.5\n')
    calls.clear()
    app.run()
    assert not app.exception and len(calls) == 1
    path.write_text(path.read_text().replace('0.5', '0.25'))
    app.run()
    assert not app.exception and len(calls) == 1


def test_allowance_edits_reuse_projected_ownership(tmp_path, monkeypatch):
    from portfolio_app.interface.views.taxes import tax_ui

    observed = {}
    original = tax_ui.result_for
    def capture(item, *args, **kwargs):
        observed[item.key] = item.holding
        return original(item, *args, **kwargs)
    monkeypatch.setattr(tax_ui, 'result_for', capture)
    app = launch(tmp_path)
    by_label(app.selectbox, 'Calculation year').set_value(2025).run()
    previous = observed.copy()
    observed.clear()
    by_label(app.number_input, 'Available allowance · 2026 (€)').set_value(100.).run()
    assert by_label(app.metric, 'Estimated tax').value == '€55.36'
    assert previous.keys() == observed.keys() and observed
    assert all(holding is previous[key] for key, holding in observed.items())
