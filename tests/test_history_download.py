"""Downloads use invented holdings and injected market observations only."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.account_journal import Journal
from portfolio_app.history_commands import reconstruction_candidate
from portfolio_app.history_observations import HistoryScope, Observation, ObservationStore, load_observations
from portfolio_app.history_view import history_view
from portfolio_app.personal_market import download_scope_observations
from portfolio_app.security_history import SecurityBook, SecurityEvent, Charge


def setup(root):
    (root/'holdings.csv').write_text('id,name,ticker,shares\ninvented,Invented,FAKE,2\n')
    trade = SecurityEvent('buy', 'buy', '2024-01-02', quantity=D(2), price=D(10), currency='EUR',
                          charges=(Charge(D(1), 'GBP'),), charges_known=True)
    book = reconstruction_candidate(SecurityBook('position-0', 'invented', ('2', '', ''), records=(trade,)),
                                    ('buy',), starts_at_zero=True)
    return Journal(version=2, securities=(book,)), HistoryScope(('position-0',))


def test_loader_uses_observed_and_recorded_currencies_and_keeps_manual_values(tmp_path, monkeypatch):
    import portfolio_app.personal_market as market
    journal, scope = setup(tmp_path)
    original = Observation('manual', 'price', 'position-0', '2024-01-02', D(9), 'USD')
    store = ObservationStore((original,))
    calls = []
    def prices(ticker, identity, start, end):
        calls.append((ticker, start, end))
        return (replace(original, id='download', amount=D(11), source='Invented feed'),)
    def fx(source, target, start, end):
        calls.append((source, target, start, end))
        return (Observation(source, 'fx', source+'/'+target, start, D(1), source, target=target, source='Invented FX'),)
    monkeypatch.setattr(market, 'fetch_security_prices', prices)
    monkeypatch.setattr(market, 'fetch_fx', fx)
    updated, errors = download_scope_observations(tmp_path, journal, scope, store, '2024-01-01', '2024-02-01', 'EUR')
    assert not errors and updated.observations[0] == original
    assert len(updated.observations) == 3
    assert calls == [('FAKE', '2024-01-01', '2024-02-01'),
                     ('GBP', 'EUR', '2024-01-01', '2024-02-01'), ('USD', 'EUR', '2024-01-01', '2024-02-01')]


def test_empty_history_load_action_fetches_through_today_without_review_or_journal_changes(tmp_path, monkeypatch):
    import portfolio_app.personal_market as market
    from portfolio_app.account_journal import save_journal, load_journal
    journal, scope = setup(tmp_path)
    save_journal(tmp_path, journal, None)
    assert not history_view(journal, scope, ObservationStore(), today=date.today()).useful
    calls = []
    def fetch(ticker, identity, start, end):
        calls.append((start, end))
        return tuple(Observation(str(i), 'price', identity, stamp, D(value), 'EUR', source='Invented feed')
                     for i, (stamp, value) in enumerate([('2024-01-02', 10), (end, 12)]))
    monkeypatch.setattr(market, 'fetch_security_prices', fetch)
    monkeypatch.setattr(market, 'fetch_fx', lambda *a: ())
    app = AppTest.from_string(f'''from pathlib import Path
from portfolio_app.account_journal import load_journal
from portfolio_app.history_observations import HistoryScope
from portfolio_app.history_ui import render_history_results
root = Path({str(tmp_path)!r})
render_history_results(root, load_journal(root)[0], HistoryScope(('position-0',)), key='test_')
''', default_timeout=20).run()
    assert not app.exception
    next(b for b in app.button if b.label == 'Load prices and FX').click().run()
    assert not app.exception
    assert calls == [('2024-01-01', date.today().isoformat())]
    store, _ = load_observations(tmp_path)
    assert not store.reviews and load_journal(tmp_path)[0] == journal
    view = history_view(journal, scope, store, today=date.today())
    assert view.useful and view.all_end == date.today().isoformat()
    assert view.all_result.money_weighted is None
    app.segmented_control[0].set_value('All').run()
    assert app.get('plotly_chart')


def test_partial_download_keeps_prices_and_exposes_missing_fx(tmp_path, monkeypatch):
    import portfolio_app.personal_market as market
    journal, scope = setup(tmp_path)
    price = Observation('price', 'price', 'position-0', '2024-01-02', D(10), 'USD', source='Invented feed')
    monkeypatch.setattr(market, 'fetch_security_prices', lambda *a: (price,))
    def unavailable(*args):
        raise OSError('Invented outage')
    monkeypatch.setattr(market, 'fetch_fx', unavailable)
    updated, errors = download_scope_observations(tmp_path, journal, scope, ObservationStore(), '2024-01-01', '2024-02-01', 'EUR')
    assert updated.observations == (price,) and not updated.reviews
    assert len(errors) == 2 and all('retry historical FX' in error for error in errors)
    assert not history_view(journal, scope, updated, today=date(2024, 2, 1)).useful


def test_receiving_account_download_includes_currency_from_fx_leg(tmp_path, monkeypatch):
    import portfolio_app.personal_market as market
    from portfolio_app.account_journal import Account, Event
    setup(tmp_path)
    journal = Journal(version=2, accounts=(Account('a', 'Invented source'), Account('b', 'Invented destination')),
        events=(Event('fx', '2024-01-02', 'fx', account_id='a', currency='USD', amount=D(10),
                      other_account_id='b', other_currency='GBP', other_amount=D(8)),))
    calls = []
    def fx(source, target, start, end):
        calls.append(source)
        return (Observation(source, 'fx', source+'/'+target, start, D(1), source, target=target, source='Invented FX'),)
    monkeypatch.setattr(market, 'fetch_fx', fx)
    updated, errors = download_scope_observations(tmp_path, journal, HistoryScope(accounts=('b',)),
        ObservationStore(), '2024-01-01', '2024-02-01', 'EUR')
    assert not errors and len(updated.observations) == 2
    assert calls == ['GBP', 'USD']


def test_accepted_futures_archive_marks_correct_values_without_cash_events(tmp_path,monkeypatch):
    from portfolio_app.account_journal import save_journal,load_journal
    from portfolio_app.derivative_types import PriceObservation
    from portfolio_app.settlement_archive import SettlementArchive
    from portfolio_app.personal_performance import calculate_history
    from test_account_journal import invented,event,trade
    journal=replace(invented(),events=(event('opening','cash_open',account_id='broker',currency='EUR',amount=1000),
        trade('buy',1,100),event('settled','settlement',date='2026-01-06',position_id='future',price=110)))
    (tmp_path/'holdings.csv').write_text('id,name,ticker,shares\ninvented,Invented,,0\n')
    save_journal(tmp_path,journal,None)
    scope=HistoryScope(accounts=('broker',));contract=journal.contracts[0]
    archive=SettlementArchive(tmp_path)
    for stamp,price in [('2026-01-05',100),('2026-01-06',110),('2026-01-07',112)]:
        archive.record(contract,PriceObservation(contract.id,stamp,D(price),source='Invented archive'))
    import portfolio_app.personal_market as market
    monkeypatch.setattr(market,'fetch_security_prices',lambda *a: (_ for _ in ()).throw(AssertionError('No security download needed')))
    store,errors=download_scope_observations(tmp_path,journal,scope,ObservationStore(),'2026-01-05','2026-01-07','EUR')
    result=calculate_history(journal,scope,store,'2026-01-05','2026-01-07')
    assert not errors and result.opening_value==1000 and result.ending_value==1120
    assert result.money_weighted is not None and not result.flow_reasons
    assert result.gain == 120 and result.time_weighted == pytest.approx(.12)
    # The original 100 of settled gain already resides in cash, plus 20 unsettled.
    archive.record(contract,PriceObservation(contract.id,'2026-01-07',D(113),source='Invented correction'))
    pending,_=download_scope_observations(tmp_path,journal,scope,store,'2026-01-05','2026-01-07','EUR')
    assert pending==store
    archive.accept_latest(contract,'2026-01-07',D(113))
    corrected,_=download_scope_observations(tmp_path,journal,scope,store,'2026-01-05','2026-01-07','EUR')
    assert calculate_history(journal,scope,corrected,'2026-01-05','2026-01-07').ending_value==1130
    assert len(corrected.observations)==len(store.observations)+1
    assert not corrected.reviews and load_journal(tmp_path)[0]==journal
