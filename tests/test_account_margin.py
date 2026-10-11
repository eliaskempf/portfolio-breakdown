"""Invented collateral and cash only; no broker accounts or live services."""
from dataclasses import replace
from decimal import Decimal as D

import pandas as pd
import pytest

from portfolio_app.account_journal import (
    Account, Event, Journal, MarginRequirement, append_events, load_journal, replay, save_journal,
)
from portfolio_app.account_margin import (
    adopt_liability, bind_holding, conversion_rate, margin_coverage, reserve_collateral, validate_reservations,
)
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.leverage_settings import MarginLiability

DAY = '2026-01-05'
CSV = ('id,name,shares,instrument_type,position_key\n'
       'synthetic,Invented security,2.5,equity,security\n')


def fixture():
    held = parse_holdings(CSV)
    valued = held.copy()
    valued['current_value_reporting'] = 1000.
    journal = Journal(accounts=(Account('broker', 'Invented broker'), Account('bank', 'Invented bank', 'bank')))
    journal = append_events(journal, (
        Event('cash', DAY, 'cash_open', account_id='broker', currency='USD', amount=1000),
        Event('other', DAY, 'cash_open', account_id='bank', currency='EUR', amount=9000),
    ))
    return journal, held, valued


def security_reserve(journal, holdings, quantity='0.125', identity='security-reserve', haircut='.2'):
    journal = bind_holding(journal, holdings, 'security', 'broker')
    return reserve_collateral(journal, Event(identity, DAY, 'reserve', account_id='broker', currency='EUR',
        amount=quantity, holding_id='security', collateral_id=identity, haircut=haircut), holdings)


def test_fractional_security_ownership_and_haircut_do_not_create_assets():
    journal, held, valued = fixture()
    journal = security_reserve(journal, held, '0.1234567')
    reservation = replay(journal).reservations['security-reserve']
    assert reservation.amount == D('.1234567')
    result = margin_coverage(journal, held, valued, 'broker', 'EUR')
    assert result.eligible_collateral == D('.1234567')/D('2.5')*1000*D('.8')
    assert result.initial_headroom is None
    assert replay(journal).cash['broker', 'USD'] == 1000
    assert replay(journal).cash['bank', 'EUR'] == 9000
    assert not replay(journal).loans


def test_foreign_security_collateral_is_not_usd_settlement_cash():
    journal, held, valued = fixture()
    journal = security_reserve(journal, held)
    journal = replace(journal, margin_requirements=(MarginRequirement('margin', 'broker', 'USD', 100, 80, DAY),))
    result = margin_coverage(journal, held, valued, 'broker', 'USD', fx_rates={'USD': '.9'})
    assert result.eligible_collateral == D(40)/D('.9')
    assert result.initial_headroom == D(40)/D('.9')-100
    assert result.settled_cash == 1000
    assert result.pending_cash == 0
    assert not result.issues


def test_cash_reservations_transit_and_requirement_currencies_are_separate():
    journal, held, valued = fixture()
    journal = reserve_collateral(journal, Event('reserve', DAY, 'reserve', account_id='broker',
        currency='USD', amount=500, collateral_id='cash', haircut='.1'), held)
    journal = append_events(journal, (
        Event('transit', DAY, 'transfer', account_id='bank', other_account_id='broker', currency='EUR', amount=900, value_date='2026-01-07'),
        Event('debt', DAY, 'debt_open', account_id='broker', currency='USD', amount=100, reference='loan'),
    ))
    journal = replace(journal, margin_requirements=(MarginRequirement('margin', 'broker', 'EUR', 450, 360, DAY),))
    result = margin_coverage(journal, held, valued, 'broker', 'USD', fx_rates={'USD': '.9'})
    assert result.eligible_collateral == 450
    assert result.initial_requirement == 500
    assert result.maintenance_requirement == 400
    assert result.initial_headroom == -50
    assert result.unreserved_cash == 500
    assert result.pending_cash == 0 and result.loans == 100
    assert margin_coverage(journal, held, valued, 'broker', 'EUR', fx_rates={'USD': '.9'}).pending_cash == 900


def test_missing_valuation_or_fx_withholds_coverage():
    journal, held, valued = fixture()
    journal = security_reserve(journal, held)
    result = margin_coverage(journal, held, valued, 'broker', 'USD')
    assert result.eligible_collateral is None
    valued.loc[:, 'current_value_reporting'] = float('nan')
    result = margin_coverage(journal, held, valued, 'broker', 'EUR')
    assert result.eligible_collateral is None and result.issues


def test_ownership_shrink_caps_eligibility_but_cannot_save_overpledged_quantity(tmp_path):
    journal, held, valued = fixture()
    journal = security_reserve(journal, held, '2.5')
    held.loc[:, 'shares'] = 1.
    valued.loc[:, 'shares'] = 1.
    valued.loc[:, 'current_value_reporting'] = 400.
    result = margin_coverage(journal, held, valued, 'broker', 'EUR')
    assert result.eligible_collateral == 320 and result.issues
    with pytest.raises(DataError, match='exceeds'):
        validate_reservations(journal, held)
    (tmp_path/'holdings.csv').write_text(CSV.replace(',2.5,', ',1,'))
    with pytest.raises(DataError, match='exceeds'):
        save_journal(tmp_path, journal, None)
    assert not (tmp_path/'account-journal.json').exists()


def test_reservations_cannot_exceed_owned_or_duplicate_other_accounts():
    journal, held, valued = fixture()
    journal = security_reserve(journal, held, '2')
    with pytest.raises(DataError, match='exceeds'):
        security_reserve(journal, held, '1', identity='more')
    with pytest.raises(DataError, match='another account'):
        bind_holding(journal, held, 'security', 'bank')
    cash_held = held.copy()
    cash_held['instrument_type'] = 'cash'
    with pytest.raises(DataError, match='canonical'):
        validate_reservations(journal, cash_held)


def test_security_save_roundtrip_requires_existing_holdings_and_preserves_precision(tmp_path):
    journal, held, _ = fixture()
    journal = security_reserve(journal, held, '.1234567')
    with pytest.raises(DataError, match='existing holding'):
        save_journal(tmp_path, journal, None)
    (tmp_path/'holdings.csv').write_text(CSV)
    save_journal(tmp_path, journal, None)
    loaded, _ = load_journal(tmp_path)
    assert replay(loaded).reservations['security-reserve'].amount == D('.1234567')


def test_legacy_liability_adoption_preserves_cash_and_consumes_original_once():
    journal, _, _ = fixture()
    legacy = MarginLiability('old', 'Invented legacy debt', 300)
    adopted = adopt_liability(journal, legacy, 'broker', DAY, 'adopt')
    assert adopted.adopted_liability_ids == ('old',)
    assert replay(adopted).cash == replay(journal).cash
    assert replay(adopted).loans['broker', 'EUR', 'legacy:old'] == 300
    assert legacy.balance_eur == 300
    with pytest.raises(DataError, match='already'):
        adopt_liability(adopted, legacy, 'broker', DAY, 'again')
    with pytest.raises(DataError, match='balance'):
        adopt_liability(journal, replace(legacy, balance_eur=None), 'broker', DAY, 'unknown')
    with pytest.raises(DataError, match='retain its loan'):
        append_events(adopted, (Event('void-adoption', DAY, 'void', replaces='adopt'),))


def test_fx_paths_reciprocals_and_conflicts():
    observations = {('USD', 'EUR'): {'rate': '.9'}, ('GBP', 'EUR'): {'rate': '1.2'}}
    assert conversion_rate(observations, 'GBP', 'USD', 'EUR') == D('1.2')/D('.9')
    assert conversion_rate({}, 'EUR', 'GBP', 'EUR') is None
    with pytest.raises(DataError, match='Conflicting'):
        conversion_rate({**observations, ('GBP', 'USD'): {'rate': '2'}}, 'GBP', 'USD', 'EUR')
    with pytest.raises(DataError):
        conversion_rate({'GBP': 0}, 'GBP', 'EUR', 'EUR')


def test_margin_ui_shows_security_quantity_and_review_without_posting(tmp_path):
    from streamlit.testing.v1 import AppTest
    (tmp_path/'holdings.csv').write_text(CSV)
    journal, _, _ = fixture()
    save_journal(tmp_path, journal, None)
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.account_journal import load_journal
from portfolio_app.account_margin_ui import render_account_margin
from portfolio_app.positions import read_snapshot
directory = Path({str(tmp_path)!r})
journal, revision = load_journal(directory)
holdings = read_snapshot(directory/'holdings.csv').holdings
valued = holdings.copy()
valued['current_value_reporting'] = 1000.
render_account_margin(directory, journal, revision, holdings, valued, 'EUR', fx_rates={{'USD': '.9'}})
''').run()
    assert not app.exception
    next(w for w in app.radio if w.label == 'Collateral asset').set_value('Security / asset').run()
    next(w for w in app.text_input if w.label == 'Shares / units to reserve').set_value('.1234567').run()
    next(w for w in app.text_input if w.label == 'Collateral haircut (%)').set_value('20').run()
    next(w for w in app.button if w.label == 'Review collateral changes').click().run()
    assert not app.exception
    assert any(w.label == 'Save reviewed collateral changes' for w in app.button)
    assert not replay(load_journal(tmp_path)[0]).reservations
    next(w for w in app.selectbox if w.label == 'Collateral action').set_value('Record margin requirement').run()
    assert not app.exception
    next(w for w in app.text_input if w.label == 'Initial margin requirement').set_value('100').run()
    next(w for w in app.text_input if w.label == 'Maintenance margin requirement').set_value('80').run()
    next(w for w in app.button if w.label == 'Review collateral changes').click().run()
    assert not app.exception
