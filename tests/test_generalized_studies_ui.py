"""Synthetic normal-workspace study workflows; no network or private inputs."""
from types import SimpleNamespace

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.generalized_studies import ScenarioContract
from portfolio_app.generalized_studies_ui import (
    MODES, fetch_proxy_rows, load_study_settings, prepare_study, save_study_settings,
)
from portfolio_app.holdings import DataError


def config(mode='Forward Monte Carlo'):
    return dict(product='SYNTHETIC', currency='EUR', multiplier=10., tick=.01,
        expiry='2026-06-18', convention='price', quantity=1, mark=100.,
        start='2026-01-01', end='2026-01-05', mode=mode,
        pools=[dict(currency=c, bank=0., broker=1000. if c == 'EUR' else 0., fx=1.,
                    bank_credit=0., broker_credit=0., broker_debit=0.) for c in ('EUR','USD','GBP')],
        collateral=[dict(currency='EUR', value=0., haircut=.2)],
        initial_margin=100., maintenance_margin=50., haircut=10., credit=0., scale_margin=False,
        policies=[dict(name='Synthetic baseline', enabled=False, target=0., trigger=0., sources='EUR,USD,GBP',
                       delay=3, conversion_cost=0., transfer_fee=0., reenter=False, reset='never')],
        fee=0., slippage=0., payments=[dict(date='',currency='EUR',amount=0.,kind='tax',pool='broker')],
        annual_return=0., volatility=0., carry=0., day_count=360, paths_count=2,
        rolls=[dict(date='2026-01-02',next_expiry='2026-09-17')] if mode == 'Rolling Monte Carlo' else [dict(date='',next_expiry='')],
        history=[dict(date=f'2026-01-0{i}',price=100.+i-1,EUR=1.,USD=1.,GBP=1.,roll_entry=None,next_expiry='') for i in range(1,4)],
        history_source='Explicitly invented index history', history_benchmark='Invented index',
        history_currency='EUR',history_convention='price',cohort_window=3,cohort_step=1,gate=False,threshold=15.)


@pytest.mark.parametrize('mode', MODES)
def test_all_modes_run_from_normal_workspace_and_save_settings_only(tmp_path, mode):
    save_study_settings(tmp_path, 'manual:EUR', config(mode), None)
    actual = tmp_path/'synthetic-real-journal.json'
    actual.write_text('{"synthetic": true}')
    before = actual.read_bytes()
    script = ('from pathlib import Path\n'
              'from portfolio_app.generalized_studies_ui import render_generalized_studies\n'
              f'render_generalized_studies(Path({str(tmp_path)!r}))')
    app = AppTest.from_string(script).run(timeout=20)
    assert not app.exception
    next(b for b in app.button if b.label == 'Run study').click().run(timeout=20)
    assert not app.exception
    assert not app.error, [x.value for x in app.error]
    assert any('Cash reconciliation error' in x.value for x in app.caption)
    next(b for b in app.button if b.label == 'Save study settings').click().run(timeout=20)
    assert not app.exception and not app.error
    assert load_study_settings(tmp_path)[0]['manual:EUR']['mode'] == mode
    assert actual.read_bytes() == before
    assert not (tmp_path/'futures-sandbox.json').exists()


def test_actual_position_is_copied_into_study_form(tmp_path):
    script = ('from portfolio_app.derivative_types import ContractSpec\n'
              'from portfolio_app.generalized_studies_ui import render_generalized_studies\n'
              'from decimal import Decimal\n'
              "contract=ContractSpec('SYNTHETIC','2027-06-18','USD',Decimal('5'),Decimal('.1'))\n"
              "render_generalized_studies(positions=[dict(id='synthetic-position',name='Invented future',contract=contract,quantity=-2,last_price=Decimal('300'))])")
    app = AppTest.from_string(script).run()
    next(s for s in app.selectbox if s.label == 'Study position').select('synthetic-position').run()
    assert not app.exception
    assert next(n for n in app.number_input if n.label == 'Signed scenario contracts').value == -2
    assert next(n for n in app.number_input if n.label == 'Opening price in points').value == 300.


def test_fractional_contract_and_delay_inputs_are_rejected():
    c = ScenarioContract('SYNTHETIC', 'EUR', 10., '2026-06-18')
    data = config()
    data['quantity'] = 1.5
    with pytest.raises(DataError, match='whole'):
        prepare_study(data, c, 'EUR')
    data = config()
    data['policies'][0].update(enabled=True, delay=1.5)
    with pytest.raises(DataError, match='whole'):
        prepare_study(data, c, 'EUR')


def test_known_product_refuses_wrong_proxy_index_identity():
    c = ScenarioContract('FESX', 'EUR', 10., '2026-06-18', return_convention='price')
    with pytest.raises(DataError, match='benchmark'):
        prepare_study(config('Historical proxy reconstruction'), c, 'EUR')


def test_free_history_uses_exact_fx_and_does_not_guess_proxy_identity():
    dates = pd.date_range('2026-01-01', periods=3)
    class Service:
        def get(self, *args, **kwargs):
            return SimpleNamespace(prices=pd.Series([100.,101.,102.],index=dates),currency='USD',status='fresh')
        def cash(self, source, target, *args, **kwargs):
            return SimpleNamespace(prices=pd.Series([.8,.81,.82],index=dates),currency=target,status='fresh')
    rows, provenance = fetch_proxy_rows('SYNTHETIC', 3, 'EUR', service=Service())
    assert rows[1]['price'] == 101.
    assert rows[1]['USD'] == .81
    assert 'USD' in provenance
    class Missing(Service):
        def cash(self, *args, **kwargs):
            return SimpleNamespace(prices=pd.Series([.8,.82],index=dates[[0,2]]),currency='EUR',status='fresh')
    with pytest.raises(DataError, match='align'):
        fetch_proxy_rows('SYNTHETIC', 3, 'EUR', service=Missing())


def test_old_results_disappear_when_inputs_change(tmp_path):
    save_study_settings(tmp_path, 'manual:EUR', config(), None)
    script = ('from pathlib import Path\nfrom portfolio_app.generalized_studies_ui import render_generalized_studies\n'
              f'render_generalized_studies(Path({str(tmp_path)!r}))')
    app = AppTest.from_string(script).run()
    next(b for b in app.button if b.label == 'Run study').click().run()
    next(n for n in app.number_input if n.label == 'Opening price in points').set_value(110.).run()
    assert any('Inputs changed' in i.value for i in app.info)
    assert not any('Cash reconciliation error' in c.value for c in app.caption)


def test_external_settings_change_cannot_overwrite_open_draft(tmp_path):
    save_study_settings(tmp_path, 'manual:EUR', config(), None)
    script = ('from pathlib import Path\nfrom portfolio_app.generalized_studies_ui import render_generalized_studies\n'
              f'render_generalized_studies(Path({str(tmp_path)!r}))')
    app = AppTest.from_string(script).run()
    _, revision = load_study_settings(tmp_path)
    changed = config()
    changed['mark'] = 200.
    save_study_settings(tmp_path, 'manual:EUR', changed, revision)
    next(b for b in app.button if b.label == 'Save study settings').click().run()
    assert any('changed' in e.value for e in app.error)
    assert load_study_settings(tmp_path)[0]['manual:EUR']['mark'] == 200.
    next(b for b in app.button if b.label == 'Reload saved study settings').click().run()
    assert next(n for n in app.number_input if n.label == 'Opening price in points').value == 200.
