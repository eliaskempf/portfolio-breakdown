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


@pytest.mark.parametrize('price_offsets,fx_offsets', [
    (('01:00','02:00','02:00','01:00'), ('00:00',)*4),
    (('00:00',)*4, ('01:00','02:00','02:00','01:00')),
    (('01:00','02:00','02:00','01:00'), ('00:00','01:00','01:00','00:00')),
])
@pytest.mark.parametrize('target', ['EUR', 'GBP'])
def test_daily_conversion_preserves_trading_dates_across_dst(price_offsets,fx_offsets,target):
    from portfolio_app.history import HistoryResult, convert_price_history
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
    from portfolio_app.history import HistoryResult, convert_price_history
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


def test_chart_renders_converted_and_native_history_across_dst(tmp_path):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from pathlib import Path
import streamlit as st
from portfolio_app.history import HistoryResult, HistoryService
from portfolio_app.market_history_ui import render_market_history
class InventedProvider:
    def history(self,ticker,period):
        dates=('2026-03-27T00:00:00+01:00','2026-03-30T00:00:00+02:00')
        return (HistoryResult(dates,(.9,.8),'EUR','fresh') if ticker.endswith('EUR=X')
                else HistoryResult(dates,(100.,110.),'USD','fresh'))
st.session_state.reporting_currency='EUR'
render_market_history('INVENTED',Path(st.session_state.directory),key='synthetic_',demo=True,
    service=HistoryService(InventedProvider(),Path(st.session_state.directory)/'history'))
''',default_timeout=10)
    app.session_state.directory = str(tmp_path)
    app.run()
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
