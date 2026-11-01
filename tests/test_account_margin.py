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


def test_adopted_legacy_debt_does_not_block_foreign_currency_settings(tmp_path):
    from portfolio_app.leverage_settings import LeverageSettings, load_leverage_settings, save_leverage_settings
    from portfolio_app.portfolio_settings import save_settings
    journal, _, _ = fixture()
    legacy = MarginLiability('old', 'Invented legacy debt', 300)
    settings = LeverageSettings(liabilities=(legacy,))
    save_leverage_settings(tmp_path, settings, None)
    adopted = adopt_liability(journal, legacy, 'broker', DAY, 'adopt')
    (tmp_path/'holdings.csv').write_text(CSV)
    save_journal(tmp_path, adopted, None)
    save_settings(tmp_path, 'USD', None)
    settings, revision = load_leverage_settings(tmp_path)
    save_leverage_settings(tmp_path, replace(settings, active_basis='exposure'), revision)
    assert load_leverage_settings(tmp_path)[0].active_basis == 'exposure'
    assert replay(load_journal(tmp_path)[0]).loans['broker','EUR','legacy:old'] == 300


def test_collateral_displays_names_and_financial_details_without_internal_ids(tmp_path):
    from streamlit.testing.v1 import AppTest
    from portfolio_app.account_margin_ui import _review_rows
    (tmp_path/'holdings.csv').write_text(CSV)
    journal, holdings, _ = fixture()
    reserved = security_reserve(journal, holdings, identity='opaque-reservation-id')
    reserved = replace(reserved, margin_requirements=(
        MarginRequirement('opaque-requirement-id', 'broker', 'EUR', 100, 80, DAY),))
    rows = _review_rows(journal, reserved, {'security': 'Invented security'})
    assert any(row.get('Holding') == 'Invented security' and row.get('Haircut (%)') == 20 for row in rows)
    assert all(row['Account'] == 'Invented broker' for row in rows)
    assert 'opaque-' not in str(rows)
    save_journal(tmp_path, reserved, None)
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
    next(w for w in app.selectbox if w.label == 'Collateral action').set_value('Release reservation').run()
    reservation = next(w for w in app.selectbox if w.label == 'Reservation to release')
    assert reservation.options == ['1. Invented security · 0.125 units · 20% haircut']
    assert any('Invented security' in frame.value.to_string() for frame in app.dataframe)
    assert all('opaque-' not in frame.value.to_string() for frame in app.dataframe)
    next(w for w in app.button if w.label == 'Review collateral changes').click().run()
    assert not app.exception
    review = app.dataframe[-1].value
    assert review.loc[0, 'Action'] == 'Release reservation'
    assert review.loc[0, 'Holding'] == 'Invented security'
    assert review.loc[0, 'Quantity / native cash'] == '0.125'
    assert all('opaque-' not in frame.value.to_string() for frame in app.dataframe)
    assert 'opaque-reservation-id' in replay(load_journal(tmp_path)[0]).reservations


@pytest.mark.parametrize('denomination', ['EUR', 'USD'])
def test_unknown_cash_withholds_affected_collateral_and_liquidity(denomination, tmp_path):
    from portfolio_app.security_history import SecurityBook, SecurityEvent
    journal, held, valued = fixture()
    journal = reserve_collateral(journal, Event('reserve', DAY, 'reserve', account_id='broker',
        currency='USD', amount=500, collateral_id='cash'), held)
    records = (SecurityEvent('opening', 'opening', DAY),
        SecurityEvent('missing-charge', 'fee', '2026-01-06', currency='USD'))
    book = SecurityBook('security', 'synthetic', ('2.5', '', ''), account_id='broker',
        records=records, active_ids=tuple(e.id for e in records))
    journal = replace(journal, version=5, securities=(book,), margin_requirements=(
        MarginRequirement('margin', 'broker', 'EUR', 100, 80, DAY),))
    coverage = margin_coverage(journal, held, valued, 'broker', denomination, fx_rates={'USD': '.9'})
    assert coverage.eligible_collateral is None
    assert coverage.initial_headroom is None and coverage.maintenance_headroom is None
    assert coverage.collateral[0].eligible_value is None
    assert 'USD cash is incomplete' in coverage.collateral[0].issue
    if denomination == 'USD':
        assert coverage.settled_cash is None and coverage.unreserved_cash is None
        from streamlit.testing.v1 import AppTest
        (tmp_path/'holdings.csv').write_text(CSV)
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
render_account_margin(directory, journal, revision, holdings, valued, 'USD', fx_rates={{('USD', 'EUR'): '.9'}})
''').run()
        assert not app.exception
        liquidity = next(frame.value for frame in app.dataframe if 'Settled cash' in frame.value.columns)
        assert liquidity.loc[0, 'Settled cash'] == 'Unavailable'
        assert liquidity.loc[0, 'Cash less reservations'] == 'Unavailable'
        assert any('USD cash is incomplete' in warning.value for warning in app.warning)
    else:
        assert coverage.settled_cash == 0 and coverage.unreserved_cash == 0
    assert coverage.initial_requirement is not None and coverage.pending_cash == 0 and coverage.loans == 0
    # A correction restores only the affected currency's known cash coverage.
    corrected = replace(records[-1], id='known-charge', amount=D(10), replaces=records[-1].id)
    book = replace(book, records=(*records, corrected), active_ids=(records[0].id, corrected.id))
    known = margin_coverage(replace(journal, securities=(book,)), held, valued,
                            'broker', denomination, fx_rates={'USD': '.9'})
    assert known.eligible_collateral == (450 if denomination == 'EUR' else 500)
    if denomination == 'EUR':
        assert known.initial_headroom == 350
    else:
        assert known.settled_cash == 990 and known.unreserved_cash == 490
    assert not known.issues


def test_unknown_settlement_cash_preserves_independent_security_coverage():
    from portfolio_app.security_history import SecurityBook, SecurityEvent
    journal, held, valued = fixture()
    journal = security_reserve(journal, held)
    journal = append_events(journal, (Event('eur-cash', DAY, 'cash_open', account_id='broker', currency='EUR', amount=0),))
    records = (SecurityEvent('opening', 'opening', DAY, quantity=D('2.5')),
        SecurityEvent('missing-charge', 'fee', '2026-01-06', currency='EUR'))
    book = SecurityBook('security', 'synthetic', ('2.5', '', ''), account_id='broker',
        records=records, active_ids=tuple(e.id for e in records))
    journal = replace(journal, version=5, securities=(book,), margin_requirements=(
        MarginRequirement('margin', 'broker', 'EUR', 100, 80, DAY),))
    coverage = margin_coverage(journal, held, valued, 'broker', 'EUR')
    assert coverage.settled_cash is None and coverage.unreserved_cash is None
    assert coverage.eligible_collateral == 40 and coverage.initial_headroom == -60
    assert coverage.initial_requirement == 100 and coverage.maintenance_requirement == 80
    assert coverage.issues == ('EUR cash is incomplete; supply missing transaction amounts.',)


@pytest.mark.parametrize('snapshot_quantity', [0, 10, 30])
@pytest.mark.parametrize('projected_input', [False, True])
def test_collateral_ui_uses_applied_history_for_coverage_and_reviewed_save(tmp_path, snapshot_quantity, projected_input):
    from streamlit.testing.v1 import AppTest
    from portfolio_app.account_journal import HoldingLink
    from portfolio_app.cost_basis import fingerprint
    from portfolio_app.security_history import SecurityBook, SecurityEvent
    content = ('id,name,shares,instrument_type,position_key,acquisition_price,acquisition_currency\n'
               f'synthetic,Invented security,{snapshot_quantity},equity,security,10,EUR\n')
    (tmp_path/'holdings.csv').write_text(content)
    held = parse_holdings(content)
    records = (SecurityEvent('opening', 'opening', '2024-01-01', quantity=snapshot_quantity,
                            amount=snapshot_quantity*10, currency='EUR'),
        SecurityEvent('movement', 'buy' if snapshot_quantity < 20 else 'sell', '2024-02-01',
                      quantity=abs(20-snapshot_quantity), price=10, currency='EUR', charges_known=True))
    book = SecurityBook('security', 'synthetic', fingerprint(held.iloc[0]), records=records,
                        active_ids=tuple(e.id for e in records))
    journal = Journal(version=5, accounts=(Account('broker', 'Invented broker'),),
        securities=(book,), holding_links=(HoldingLink('security', 'broker'),),
        events=(Event('cash', '2024-01-01', 'cash_open', account_id='broker', currency='EUR', amount=1000),
            Event('pledge', '2024-02-02', 'reserve', account_id='broker', currency='EUR',
                  amount=5, holding_id='security', collateral_id='original-pledge')),
        margin_requirements=(MarginRequirement('margin', 'broker', 'EUR', 75, 60, '2024-02-02'),))
    save_journal(tmp_path, journal, None)
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.account_journal import load_journal
from portfolio_app.account_margin_ui import render_account_margin
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import effective_holdings
directory = Path({str(tmp_path)!r})
journal, revision = load_journal(directory)
holdings = read_snapshot(directory/'holdings.csv').holdings
valued = effective_holdings(holdings, journal.securities).assign(current_value_reporting=200.)
if {projected_input!r}:
    holdings = effective_holdings(holdings, journal.securities)
render_account_margin(directory, journal, revision, holdings, valued, 'EUR', fx_rates={{}})
''').run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == 'Eligible collateral').value == '€50.00'
    assert next(m for m in app.metric if m.label == 'Initial requirement headroom').value == '€-25.00'
    next(w for w in app.radio if w.label == 'Collateral asset').set_value('Security / asset').run()
    next(w for w in app.text_input if w.label == 'Shares / units to reserve').set_value('10').run()
    next(w for w in app.button if w.label == 'Review collateral changes').click().run()
    assert len(replay(load_journal(tmp_path)[0]).reservations) == 1
    next(w for w in app.button if w.label == 'Save reviewed collateral changes').click().run()
    assert not app.exception and not app.error
    saved = load_journal(tmp_path)[0]
    assert sum(r.amount for r in replay(saved).reservations.values()) == 15
    assert saved.securities == journal.securities
    assert (tmp_path/'holdings.csv').read_text() == content
    assert next(m for m in app.metric if m.label == 'Eligible collateral').value == '€150.00'


def test_collateral_rejects_stale_projected_journal_even_when_units_match(tmp_path):
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
holdings.attrs['history_journal_revision'] = 'older-journal-revision'
valued = holdings.assign(current_value_reporting=1000.)
render_account_margin(directory, journal, revision, holdings, valued, 'EUR', fx_rates={{'USD': '.9'}})
''').run()
    assert not app.exception and not app.metric
    assert any('Holdings changed' in item.value for item in app.info)
    assert not any(button.label == 'Review collateral changes' for button in app.button)
