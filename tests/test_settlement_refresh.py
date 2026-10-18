"""Polling schedules work; only new financial inputs request a global render."""
from dataclasses import replace
from datetime import timedelta

import pytest

from portfolio_app import account_journal as ledger
from portfolio_app import settlement_archive_ui as ui
from portfolio_app.futures_quotes import record_result, collector as quotes
from test_futures_freshness import setup, quote, NOW


def test_watcher_ignores_ticks_starts_retrievals_and_notices_but_defers_new_prices(tmp_path, monkeypatch):
    _, journal, contract = setup(tmp_path)
    observed = quote(contract)
    record_result(tmp_path, contract, observed, '', attempted_at=NOW, now=NOW)
    clock = [NOW]; allowed = [True]; callbacks = []; reruns = []; scheduled = []
    import portfolio_app.futures_valuation as valuation
    monkeypatch.setattr(valuation, 'utc_now', lambda: clock[0])
    monkeypatch.setattr(ui, 'refresh_allowed', lambda: allowed[0])
    monkeypatch.setattr(ui.collector, 'schedule', lambda *a, **k: scheduled.append('settlement') or True)
    monkeypatch.setattr(quotes, 'schedule', lambda *a, **k: scheduled.append('quote') or True)
    def fragment(**options):
        assert options == {'run_every': 2}
        def decorate(fn):
            callbacks.append(fn)
            return fn
        return decorate
    monkeypatch.setattr(ui.st, 'fragment', fragment)
    monkeypatch.setattr(ui.st, 'rerun', lambda: reruns.append(True))
    ui.watch_settlements(tmp_path, journal)
    tick = callbacks[0]
    clock[0] += timedelta(minutes=5)
    tick()
    assert not reruns and len(scheduled) == 6
    # A later download time cannot make the same source observation fresh.
    record_result(tmp_path, contract, replace(observed, observed_at=clock[0].isoformat()),
                  '', attempted_at=clock[0], now=clock[0])
    tick(); assert not reruns
    record_result(tmp_path, contract, None, 'Invented outage', attempted_at=clock[0], now=clock[0])
    tick(); assert not reruns
    assert 'Futures quote refresh unavailable' in ui.settlement_notices(tmp_path, journal)[0][-1]
    # New quotes can be newer than the clock at the last full render.
    newer = replace(observed, price=observed.price+1, quote_at=clock[0].isoformat())
    allowed[0] = False
    record_result(tmp_path, contract, newer, '', attempted_at=clock[0], now=clock[0])
    tick(); tick(); assert not reruns
    allowed[0] = True
    tick(); assert reruns == [True]


def test_journal_changes_refresh_even_without_new_quote(tmp_path, monkeypatch):
    _, journal, _ = setup(tmp_path)
    callbacks = []; reruns = []
    monkeypatch.setattr(ui.collector, 'schedule', lambda *a, **k: False)
    monkeypatch.setattr(quotes, 'schedule', lambda *a, **k: False)
    monkeypatch.setattr(ui, 'refresh_allowed', lambda: True)
    def decorate(fn):
        callbacks.append(fn)
        return fn
    monkeypatch.setattr(ui.st, 'fragment', lambda **k: decorate)
    monkeypatch.setattr(ui.st, 'rerun', lambda: reruns.append(True))
    ui.watch_settlements(tmp_path, journal)
    current, revision = ledger.load_journal(tmp_path)
    current = ledger.append_event(current, ledger.Event('invented-fee', '2026-01-07', 'fee',
                                                       position_id='p', amount=1))
    ledger.save_journal(tmp_path, current, revision)
    callbacks[0]()
    assert reruns == [True]


@pytest.mark.parametrize('demo,journal', [(True, object()), (False, None), (False, ledger.Journal())])
def test_inactive_watcher_does_not_schedule(tmp_path, monkeypatch, demo, journal):
    monkeypatch.setattr(ui.collector, 'schedule', lambda *a, **k: pytest.fail('unexpected collection'))
    ui.watch_settlements(tmp_path, journal, demo=demo)
