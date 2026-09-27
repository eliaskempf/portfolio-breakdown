from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from portfolio_app.history import HistoryService, normalize_history


def test_history_normalizes_subunits_sorts_and_rejects_missing_currency():
    frame = pd.DataFrame({'Close': [120., 100., float('nan')]}, index=pd.to_datetime(['2026-01-02', '2026-01-01', '2026-01-03']))
    result = normalize_history(frame, 'GBp')
    assert result.currency == 'GBP'
    assert result.prices == (1., 1.2)
    assert result.dates[0].startswith('2026-01-01')
    with pytest.raises(ValueError):
        normalize_history(frame, '')


def test_history_cache_expiry_failure_and_manual_instruments(tmp_path):
    class Provider:
        calls = 0
        fail = False
        def history(self, ticker, period):
            self.calls += 1
            assert ticker == 'SYNTHETIC' and period == '1y'
            if self.fail:
                raise ConnectionError('offline')
            return normalize_history(pd.DataFrame({'Close': [42.]}, index=pd.to_datetime(['2026-01-01'])), 'EUR')
    provider = Provider()
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    service = HistoryService(provider, tmp_path, now=lambda: now)
    assert service.get('SYNTHETIC').status == 'fresh'
    assert service.get('SYNTHETIC').status == 'cached'
    assert provider.calls == 1
    now += timedelta(hours=2)
    provider.fail = True
    result = service.get('SYNTHETIC')
    assert result.status == 'stale' and result.prices == (42.,)
    assert service.get('SYNTHETIC', manual=True).status == 'unavailable'
    assert service.get('').status == 'unavailable'
    assert provider.calls == 2
    assert HistoryService(provider).get('SYNTHETIC').status == 'unavailable'
