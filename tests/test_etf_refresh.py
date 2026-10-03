"""Automatic refresh tests use invented snapshots and injected offline workers."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event

import pandas as pd
import pytest
import yaml

from portfolio_app.etf import load_funds
from portfolio_app.etf_refresh import RefreshCoordinator, due, preferences, read_json, save_preferences
from portfolio_app.etf_sources import SOURCES


@pytest.fixture
def refresh_workspace(tmp_path, monkeypatch):
    directory = tmp_path / 'etfs'
    directory.mkdir()
    (directory / 'invented.csv').write_text('constituent_id,name,ticker,isin,weight\na,Invented company,SYNTH,ZZ1111111111,1\n')
    (directory / 'invented.yaml').write_text(yaml.safe_dump(dict(
        fund_id='invented', name='Invented ETF', isin='ZZ9999999999', tickers=['SYNTH-FUND'],
        as_of='2026-01-01', source='https://example.invalid', holdings_file='invented.csv')))
    monkeypatch.setitem(SOURCES, 'ZZ9999999999', None)
    holdings = pd.DataFrame([dict(isin='ZZ9999999999', ticker='SYNTH-FUND')])
    return tmp_path, holdings, load_funds(directory)


def join(service, path):
    service._workers[str(path.resolve())].join(timeout=3)
    assert not service.running(path)


def test_refresh_is_nonblocking_single_writer_and_persists_throttle(refresh_workspace):
    path, holdings, funds = refresh_workspace
    started, release = Event(), Event()
    calls = []
    def refresh(fund):
        calls.append(fund.isin)
        started.set()
        assert release.wait(3)
        return fund  # Same provider date, as can happen on weekends.
    now = [datetime(2026, 1, 10, tzinfo=timezone.utc)]
    service = RefreshCoordinator(refresh=refresh, now=lambda: now[0])
    assert service.schedule(path, holdings, funds)
    try:
        assert started.wait(2)
        assert service.running(path)
        assert not service.schedule(path, holdings, funds)
        # A second app process/coordinator shares the persistent writer lock.
        other = RefreshCoordinator(refresh=lambda _: pytest.fail('Concurrent download'), now=lambda: now[0])
        assert other.schedule(path, holdings, funds, force=True)
        join(other, path)
        assert not other.error(path)
    finally:
        release.set()
        join(service, path)
    assert calls == ['ZZ9999999999']
    records = read_json(path / '.cache/etf-refresh/status.json')
    assert records[funds[0].isin]['status'] == 'checked'
    assert records[funds[0].isin]['as_of'] == '2026-01-01'
    restarted = RefreshCoordinator(refresh=refresh, now=lambda: now[0])
    assert not restarted.schedule(path, holdings, funds)
    now[0] += timedelta(days=1)
    assert restarted.schedule(path, holdings, funds)
    join(restarted, path)
    assert len(calls) == 2


def test_failures_keep_snapshots_and_are_throttled_with_manual_retry(refresh_workspace):
    path, holdings, funds = refresh_workspace
    before = {p.name: p.read_bytes() for p in (path / 'etfs').iterdir()}
    def fail(_):
        raise ValueError('Invented invalid download')
    service = RefreshCoordinator(refresh=fail, now=lambda: datetime(2026, 1, 10, tzinfo=timezone.utc))
    assert service.schedule(path, holdings, funds)
    join(service, path)
    status = read_json(path / '.cache/etf-refresh/status.json')[funds[0].isin]
    assert status['status'] == 'failed' and 'invalid download' in status['error']
    assert not service.schedule(path, holdings, funds)
    assert service.schedule(path, holdings, funds, force=True)
    join(service, path)
    assert {p.name: p.read_bytes() for p in (path / 'etfs').iterdir()} == before


def test_demo_disabled_fresh_and_unheld_funds_never_download(refresh_workspace):
    path, holdings, funds = refresh_workspace
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    service = RefreshCoordinator(refresh=lambda _: pytest.fail('Unexpected download'), now=lambda: now)
    assert not service.schedule(path, holdings, funds, demo=True, force=True)
    assert not service.schedule(path, holdings.iloc[:0], funds)
    assert not service.schedule(path, holdings.assign(shares=0), funds)
    assert not service.schedule(path, holdings, [replace(funds[0], as_of=now.date())])
    save_preferences(path, enabled=True, minimum_age_days=7)
    assert not service.schedule(path, holdings, funds)
    save_preferences(path, enabled=False, minimum_age_days=1)
    assert not service.schedule(path, holdings, funds)
    assert preferences(path) == dict(enabled=False, minimum_age_days=1)


def test_due_uses_attempt_time_and_tolerates_invalid_timestamp(refresh_workspace):
    _, _, funds = refresh_workspace
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert due(funds[0], {}, now)
    assert due(funds[0], {'attempted_at': 'bad date'}, now)
    assert not due(funds[0], {'attempted_at': (now - timedelta(hours=23)).isoformat()}, now)
    assert due(funds[0], {'attempted_at': (now - timedelta(days=1)).isoformat()}, now)


def test_refresh_import_does_not_require_unix_fcntl():
    import subprocess
    import sys

    # Simulate the absent Unix module without changing platform identity or
    # reading a workspace. Native Windows lock behavior is checked in CI.
    result = subprocess.run([sys.executable, '-c',
        "import sys; sys.modules['fcntl'] = None; import portfolio_app.etf_refresh"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
