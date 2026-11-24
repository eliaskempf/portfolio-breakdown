import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from portfolio_app.market.history import HistoryService, normalize_history


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


@pytest.mark.parametrize('price_offsets,fx_offsets', [
    (('01:00','02:00','02:00','01:00'), ('00:00',)*4),
    (('00:00',)*4, ('01:00','02:00','02:00','01:00')),
    (('01:00','02:00','02:00','01:00'), ('00:00','01:00','01:00','00:00')),
])
@pytest.mark.parametrize('target', ['EUR', 'GBP'])
def test_daily_conversion_preserves_trading_dates_across_dst(price_offsets,fx_offsets,target):
    from portfolio_app.market.history import HistoryResult, convert_price_history
    days = ('2026-03-27','2026-03-30','2026-10-23','2026-10-26')
    original = HistoryResult(tuple(f'{day}T00:00:00+{offset}' for day,offset in zip(days,price_offsets)),
                             (100.,110.,120.,130.), 'USD', 'cached')
    rates = HistoryResult(tuple(f'{day}T00:00:00+{offset}' for day,offset in zip(days,fx_offsets)),
                          (.9,.8,.7,.6), 'EUR', 'cached')
    sterling = HistoryResult(days, (1.2,1.2,1.2,1.2), 'EUR', 'cached')
    result = convert_price_history(original,target,{'USD':rates,'GBP':sterling})
    assert result.dates == tuple(f'{day}T00:00:00' for day in days)
    assert result.prices == pytest.approx(tuple(x/(1.2 if target=='GBP' else 1.) for x in (90.,88.,84.,78.)))
    assert result.currency == target and result.status == 'cached'


def test_cached_timezone_offsets_convert_without_refresh(tmp_path):
    from portfolio_app.market.history import HistoryResult, convert_price_history
    class Provider:
        def history(self,*_):
            return HistoryResult(('2026-03-27T00:00:00+01:00','2026-03-30T00:00:00+02:00'),(100.,110.),'GBP','fresh')
    now = datetime(2026,4,1,tzinfo=timezone.utc)
    HistoryService(Provider(),tmp_path,now=lambda:now).get('INVENTED')
    class Offline:
        def history(self,*_):raise AssertionError('Existing cache must remain usable')
    cached = HistoryService(Offline(),tmp_path,now=lambda:now).get('INVENTED')
    assert cached.status == 'cached'
    rates = HistoryResult(('2026-03-27','2026-03-30'),(1.2,1.3),'EUR')
    result = convert_price_history(cached,'EUR',{'GBP':rates})
    assert result.prices == pytest.approx((120.,143.))
    assert result.dates == ('2026-03-27T00:00:00','2026-03-30T00:00:00')


@pytest.mark.parametrize('background', [False, True], ids=['synchronous', 'finished-before-poll'])
def test_chart_renders_converted_and_native_history_across_dst(tmp_path, background):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from pathlib import Path
import streamlit as st
from portfolio_app.market.history import HistoryResult, HistoryService
from portfolio_app.interface.views.history.market_history_ui import render_market_history
class InventedProvider:
    def history(self,ticker,period):
        dates=('2026-03-27T00:00:00+01:00','2026-03-30T00:00:00+02:00')
        return (HistoryResult(dates,(.9,.8),'EUR','fresh') if ticker.endswith('EUR=X')
                else HistoryResult(dates,(100.,110.),'USD','fresh'))
st.session_state.reporting_currency='EUR'
service=HistoryService(InventedProvider(),Path(st.session_state.directory)/'history')
if st.session_state.background:
    from portfolio_app.market.market_data import BackgroundHistory
    class ImmediateJobs:
        def __init__(self): self.finished=0
        def request(self, workspace, key, work):
            work()
            self.finished+=1
            return True
        def pending(self,*args): return False
        def revision(self,workspace): return self.finished
    service=BackgroundHistory(service, ImmediateJobs(), st.session_state.directory)
render_market_history('INVENTED',Path(st.session_state.directory),key='synthetic_',demo=not st.session_state.background,
    service=service)
st.caption('After the chart')
''',default_timeout=10)
    app.session_state.directory = str(tmp_path)
    app.session_state.background = background
    app.run()
    assert any(c.value == 'After the chart' for c in app.caption)
    assert not app.exception and len(app.get('plotly_chart')) == 1
    for period in ('1M','6M','1Y','5Y','Max'):
        app.get('button_group')[0].set_value(period).run()
        assert not app.exception and len(app.get('plotly_chart')) == 1
    for units in ('USD','EUR'):
        app.selectbox[0].set_value(units).run()
        assert not app.exception and len(app.get('plotly_chart')) == 1
        import json
        figure = json.loads(app.get('plotly_chart')[0].proto.spec)
        symbol = '$' if units == 'USD' else '€'
        assert figure['layout']['yaxis']['tickprefix'] == symbol
        assert 'Price: ' + symbol in figure['data'][0]['hovertemplate']
        assert figure['layout']['height'] == 340


def test_price_refresh_finishing_during_mount_preserves_following_page_content(tmp_path):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from pathlib import Path
import streamlit as st
from portfolio_app.market.history import HistoryResult
from portfolio_app.interface.views.history.market_history_ui import render_market_history
class FinishingHistory:
    def __init__(self): self.checked = False
    def get(self, *args, **kwargs):
        if self.checked:
            return HistoryResult(('2024-01-02',), (12.,), 'EUR', 'cached')
        return HistoryResult()
    def pending(self, *args):
        waiting = not self.checked
        self.checked = True
        return waiting
render_market_history('SYNTH', Path(st.session_state.directory), key='immediate_', service=FinishingHistory())
st.button('Following control')
''', default_timeout=10)
    app.session_state.directory = str(tmp_path)
    app.run()
    assert not app.exception and any(button.label == 'Following control' for button in app.button)


def test_reopened_market_history_decodes_each_saved_revision_once(tmp_path, monkeypatch):
    import os
    from dataclasses import replace
    from portfolio_app.market import history

    clock = datetime(2026, 7, 1, tzinfo=timezone.utc)
    reader = HistoryService(None, tmp_path, now=lambda: clock)
    path = reader._path('INVENTED', 'Max')
    def publish(value):
        entry = history.HistoryResult(('2026-01-01',), (value,), 'USD', 'fresh', clock.isoformat())
        stage = path.with_suffix('.new')
        stage.write_text(json.dumps(vars(entry)))
        stage.replace(path)

    publish(12.)
    stamp = path.stat().st_mtime_ns
    opened, validated = [], []
    original_open, validate = Path.open, history._validated_history
    def counted_open(self, *args, **kwargs):
        if self == path:
            opened.append(self)
        return original_open(self, *args, **kwargs)
    def counted_validate(result):
        validated.append(result.prices)
        return validate(result)
    monkeypatch.setattr(Path, 'open', counted_open)
    monkeypatch.setattr(history, '_validated_history', counted_validate)
    first, due = reader.cached('INVENTED', 'Max')
    assert not due and first.prices == (12.,)
    for _ in range(4):
        reread, due = reader.cached('INVENTED', 'Max')
        assert not due and reread.prices is first.prices
    clock += timedelta(hours=1)
    stale, due = reader.cached('INVENTED', 'Max')
    assert due and stale == replace(first, status='stale', note='Showing saved market prices while refreshing.')
    assert len(opened) == len(validated) == 1

    publish(13.)
    os.utime(path, ns=(stamp, stamp))  # An equal-size atomic replacement with the original mtime.
    refreshed = reader.get('INVENTED', 'Max')
    assert refreshed.status == 'cached' and refreshed.prices == (13.,)
    assert len(opened) == len(validated) == 2
    path.unlink()
    missing, due = reader.cached('INVENTED', 'Max')
    assert due and not missing.prices


def test_failed_market_refresh_preserves_newer_concurrent_publication(tmp_path):
    from portfolio_app.market.history import HistoryResult
    clock = datetime(2026, 7, 1, tzinfo=timezone.utc)
    class Invented:
        price = 10.
        def history(self, *args):
            return HistoryResult(('2026-01-01',), (self.price,), 'EUR', 'fresh')
    quoted = Invented()
    writer = HistoryService(quoted, tmp_path, now=lambda: clock)
    writer.get('INVENTED')
    clock += timedelta(hours=1)
    class Offline:
        def history(self, *args):
            quoted.price = 20.
            writer.get('INVENTED', refresh=True)
            raise OSError('Synthetic outage')
    reader = HistoryService(Offline(), tmp_path, now=lambda: clock)
    result = reader.get('INVENTED')
    assert result.status == 'cached' and result.prices == (20.,)
    assert HistoryService(None, tmp_path, now=lambda: clock).cached('INVENTED') == (result, False)


@pytest.mark.parametrize('malformed', [dict(dates=['2026-01-01'], prices=[-2.]),
                                      dict(dates=[], prices=[2.]), dict(prices=[], status='unavailable')])
def test_invalid_disk_history_never_masks_a_repair(tmp_path, malformed):
    clock = datetime(2026, 7, 1, tzinfo=timezone.utc)
    reader = HistoryService(None, tmp_path, now=lambda: clock)
    path = reader._path('INVENTED', 'Max')
    path.write_text(json.dumps(dict(attempted_at=clock.isoformat(), **malformed)))
    result, due = reader.cached('INVENTED', 'Max')
    assert due and not result.prices
    path.write_text(json.dumps(dict(dates=['2026-01-01'], prices=[5.], currency='EUR', fetched_at=clock.isoformat())))
    result, due = reader.cached('INVENTED', 'Max')
    assert not due and result.prices == (5.,)


def test_atomic_replacement_between_stat_and_open_cannot_poison_history_cache(tmp_path, monkeypatch):
    from portfolio_app.market.history import HistoryResult
    clock = datetime(2026, 7, 1, tzinfo=timezone.utc)
    reader = HistoryService(None, tmp_path, now=lambda: clock)
    path = reader._path('INVENTED', '1Y')
    stage = path.with_suffix('.new')
    for destination, price in ((path, 8.), (stage, 9.)):
        saved = HistoryResult(('2026-01-01',), (price,), 'EUR', 'fresh', clock.isoformat())
        destination.write_text(json.dumps(vars(saved)))
    original_open = Path.open
    count = 0
    def switch_before_open(self, *args, **kwargs):
        nonlocal count
        if self == path:
            count += 1
            if count == 1:
                stage.replace(path)
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', switch_before_open)
    for _ in range(2):
        result, due = reader.cached('INVENTED')
        assert not due and result.prices == (9.,)
    assert count == 1


def test_saved_history_eviction_keeps_recent_ranges_available_without_decoding(tmp_path, monkeypatch):
    from portfolio_app.market import history
    clock = datetime(2026, 7, 1, tzinfo=timezone.utc)
    reader = HistoryService(None, tmp_path, now=lambda: clock)
    calls = []
    validate = history._validated_history
    def counted(result):
        calls.append(result.prices[0])
        return validate(result)
    monkeypatch.setattr(history, '_validated_history', counted)
    for i in range(40):
        ticker = f'INVENTED-{i}'
        entry = history.HistoryResult(('2026-01-01',), (i + 1.,), 'EUR', 'fresh', clock.isoformat())
        reader._path(ticker, '1Y').write_text(json.dumps(vars(entry)))
        assert reader.get(ticker).prices == entry.prices
    assert reader.get('INVENTED-39').prices == (40.,)
    assert len(calls) == 40
    assert reader.get('INVENTED-0').prices == (1.,)
    assert len(calls) == 41


def test_invalid_provider_prices_leave_last_valid_history_usable(tmp_path):
    from portfolio_app.market.history import HistoryResult
    clock = datetime(2026, 7, 1, tzinfo=timezone.utc)
    prices = [8.]
    class Invented:
        def history(self, *args):
            return HistoryResult(('2026-01-01',), prices, 'EUR', 'fresh')
    service = HistoryService(Invented(), tmp_path, now=lambda: clock)
    first = service.get('INVENTED')
    prices[0] = float('nan')
    assert first.prices == (8.,)
    clock += timedelta(hours=1)
    result = service.get('INVENTED')
    assert result.status == 'stale' and result.prices == first.prices
    assert HistoryService(None, tmp_path, now=lambda: clock).cached('INVENTED') == (result, False)


@pytest.mark.parametrize('fail_reads', [1, 2])
def test_transient_disk_read_failure_keeps_saved_prices_after_provider_failure(tmp_path, monkeypatch, fail_reads):
    from portfolio_app.market.history import HistoryResult
    clock = datetime(2026, 7, 1, tzinfo=timezone.utc)
    reader = HistoryService(None, tmp_path, now=lambda: clock)
    path = reader._path('INVENTED', '1Y')
    saved = HistoryResult(('2026-01-01',), (17.,), 'EUR', 'fresh', clock.isoformat())
    path.write_text(json.dumps(vars(saved)))
    original_open = Path.open
    def intermittent(self, *args, **kwargs):
        nonlocal fail_reads
        if self == path and fail_reads:
            fail_reads -= 1
            raise PermissionError('Synthetic sharing conflict')
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', intermittent)
    reader.get('INVENTED')
    reopened = HistoryService(None, tmp_path, now=lambda: clock)
    assert reopened.cached('INVENTED')[0].prices == (17.,)


def test_concurrent_failed_attempt_does_not_discard_successful_prices(tmp_path):
    from portfolio_app.market.history import HistoryResult
    clock = datetime(2026, 7, 1, tzinfo=timezone.utc)
    offline = HistoryService(None, tmp_path, now=lambda: clock)
    class Invented:
        def history(self, *args):
            assert offline.get('INVENTED').status == 'unavailable'
            return HistoryResult(('2026-01-01',), (22.,), 'EUR', 'fresh')
    service = HistoryService(Invented(), tmp_path, now=lambda: clock)
    assert service.get('INVENTED').prices == (22.,)
    assert offline.get('INVENTED').prices == (22.,)


def test_fx_conversion_parses_only_nonstandard_dates_individually(monkeypatch):
    from portfolio_app.market import history
    native = history.HistoryResult(
        ('2026-03-27T23:59:59.123456789-04:00', '2026-03-30T00:00:00+02:00',
         '2026-03-30T12:01:00Z', 'April 1, 2026', '2026-04-02'), (10., 12., 15., 20., 25.), 'USD', 'fresh')
    rates = history.HistoryResult(('2026-03-27', '2026-03-30', '2026-04-01'), (.8, .75, .9), 'EUR', 'stale')
    seen = []
    timestamp = pd.Timestamp
    def counted(stamp):
        seen.append(stamp)
        return timestamp(stamp)
    monkeypatch.setattr(pd, 'Timestamp', counted)
    converted = history.convert_price_history(native, 'EUR', {'USD': rates})
    assert seen == ['April 1, 2026']
    assert converted.dates == ('2026-03-27T00:00:00', '2026-03-30T00:00:00',
                               '2026-03-30T00:00:00', '2026-04-01T00:00:00')
    assert converted.prices == (8., 9., 11.25, 18.)
    assert converted.status == 'stale' and 'omitted' in converted.note


@pytest.mark.parametrize('invalid', ['2026-02-30', '2026-02-28T37:00:00', '2026-02-28T12:00:00oops'])
def test_fx_conversion_validates_the_full_timestamp(invalid):
    from portfolio_app.market.history import HistoryResult, convert_price_history
    native = HistoryResult((invalid,), (10.,), 'USD', 'fresh')
    rates = HistoryResult(('2026-02-28',), (.9,), 'EUR', 'fresh')
    with pytest.raises(ValueError):
        convert_price_history(native, 'EUR', {'USD': rates})


@pytest.mark.parametrize('days', [('2024-11-03', 'NaT', '2026-04-05'), ('1500-11-03', 'NaT', '2500-04-05')])
def test_converted_history_preserves_the_supported_date_range(days):
    from portfolio_app.market.history import HistoryResult, convert_price_history
    native = HistoryResult(days, (10., 20., 30.), 'USD', 'cached')
    rates = HistoryResult(native.dates, (.8, .9, 1.), 'EUR', 'cached')
    try:
        original_index = pd.DatetimeIndex([pd.Timestamp(day).date() for day in days])
    except pd.errors.OutOfBoundsDatetime:
        # pandas 2.2 constructs this index at nanosecond precision.
        with pytest.raises(pd.errors.OutOfBoundsDatetime):
            convert_price_history(native, 'EUR', {'USD': rates})
        return
    converted = convert_price_history(native, 'EUR', {'USD': rates})
    assert converted.dates == tuple(day.isoformat() for day in original_index)
    assert all(type(day) is str for day in converted.dates)
    assert converted.prices == (8., 18., 30.)
