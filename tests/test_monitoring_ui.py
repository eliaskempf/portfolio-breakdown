"""Synthetic Streamlit workflows with injected market data and no network."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from portfolio_app.monitoring_service import monitoring_for
from portfolio_app.monitoring_store import load_monitoring
from portfolio_app.monitoring import SeriesSpec
from test_ui import by_label


def launch(tmp_path, *, seed='INVENTED'):
    import json
    service=monitoring_for(tmp_path,demo=True)
    spec=SeriesSpec('INVENTED','Invented security','', 'close','Yahoo Finance','Closing price')
    service.series[json.dumps(vars(spec),sort_keys=True)]=service.market.get(spec)
    app=AppTest.from_string('''
from pathlib import Path
import pandas as pd
import streamlit as st
from portfolio_app.monitoring_ui import render_monitoring
from portfolio_app.account_journal import Journal
root=Path(st.session_state.directory)
held=pd.DataFrame([dict(position_id='invented',ticker='INVENTED',name='Invented security',instrument_type='equity')])
render_monitoring(root,held,Journal(),demo=True)
''',default_timeout=15)
    app.session_state.directory=str(tmp_path)
    service.browse=lambda spec, **kwargs:service.market.get(spec)
    if seed:
        app.session_state['monitoring_seed_symbol']=seed
    return app.run()


def test_browse_save_edit_pause_delete_and_recorded_history(tmp_path):
    app=launch(tmp_path)
    assert not app.exception
    assert len(app.get('plotly_chart')) == 1
    by_label(app.selectbox,'SMA preset').select('10 months').run()
    by_label(app.text_input,'Rule name').set_value('Invented trend').run()
    by_label(app.number_input,'Distance from SMA trigger (%)').set_value(-2.).run()
    by_label(app.button,'Track rule').click().run()
    assert not app.exception
    doc,_=load_monitoring(tmp_path)
    assert len(doc['rules']) == 1 and doc['rules'][0]['window'] == 10
    assert doc['rules'][0]['period'] == 'months'
    assert app.session_state['monitoring_view'] == 'Tracked rules'
    by_label(app.button,'Edit rule').click().run()
    assert not app.exception
    by_label(app.text_input,'Rule name').set_value('Edited invented trend').run()
    by_label(app.button,'Save changes').click().run()
    assert not app.exception
    assert load_monitoring(tmp_path)[0]['rules'][0]['revision'] == 2
    by_label(app.button,'Pause rule').click().run()
    assert not load_monitoring(tmp_path)[0]['rules'][0]['enabled']
    by_label(app.button,'Resume rule').click().run()
    by_label(app.button,'Delete rule').click().run()
    by_label(app.button,'Confirm deletion').click().run()
    assert not app.exception and not load_monitoring(tmp_path)[0]['rules']


def test_msci_has_no_intraday_option_and_account_limits_require_input(tmp_path):
    app=launch(tmp_path)
    app.session_state['monitoring_seed_symbol']='MSCI:990100:NETR:EUR'
    app.run()
    assert 'Latest intraday observation' not in by_label(app.selectbox,'Evaluate on').options
    assert not app.exception


def test_route_switches_to_saved_rule_without_widget_mutation(tmp_path):
    from portfolio_app.monitoring_store import save_rule
    from test_monitoring import rule
    save_rule(tmp_path,rule())
    app=launch(tmp_path)
    app.session_state['monitoring_route']='invented'
    app.run()
    assert not app.exception
    assert app.session_state['monitoring_view']=='Tracked rules'


def test_switching_index_portfolio_and_crypto_keeps_price_bases_valid(tmp_path):
    app=launch(tmp_path)
    app.session_state['monitoring_seed_symbol']='MSCI:891800:NETR:USD'
    app.run()
    assert by_label(app.selectbox,'Price basis').options==['Published index levels']
    app.session_state['monitoring_seed_symbol']='INVENTED'
    app.run()
    assert 'Dividend-adjusted history' in by_label(app.selectbox,'Price basis').options
    by_label(app.selectbox,'Price basis').select('adjusted').run()
    app.session_state['monitoring_seed_symbol']='BTC-EUR'
    app.run()
    assert by_label(app.selectbox,'Price basis').value in {'close','adjusted'}
    assert 'Latest intraday observation' in by_label(app.selectbox,'Evaluate on').options
    assert not app.exception
    assert any(x.label=='Distance from SMA trigger (%)' for x in app.number_input)
    assert by_label(app.selectbox,'Age unit').value=='Days'
    assert by_label(app.number_input,'Maximum data age').value==7


def test_historical_chart_uses_full_warmup_and_never_creates_events(tmp_path):
    import json
    import base64
    import numpy as np
    import pandas as pd
    from portfolio_app.monitoring import MarketSeries, utc_now
    app=launch(tmp_path)
    dates=pd.bdate_range('1990-01-01','2026-10-09')
    values=tuple(100.+i*.01 for i in range(len(dates)))
    series=MarketSeries(tuple(d.date().isoformat() for d in dates),values,(True,)*len(dates),'EUR',utc_now().isoformat(),quality='valid')
    monitoring_for(tmp_path,demo=True).browse=lambda *a,**kw:series
    app.run()
    by_label(app.number_input,'Distance from SMA trigger (%)').set_value(-2.).run()
    by_label(app.selectbox,'Chart period').select('GFC (2007–2009)').run()
    chart=json.loads(app.get('plotly_chart')[0].proto.spec)
    first=chart['data'][0]['x'][0]
    assert first.startswith('2007-01') and chart['data'][0]['x'][-1].startswith('2009-12')
    sma=chart['data'][1]['y']
    numeric=np.frombuffer(base64.b64decode(sma['bdata']),dtype=sma['dtype']) if isinstance(sma,dict) else sma
    index=dates.get_loc(pd.Timestamp(first))
    assert numeric[0]==sum(values[index-199:index+1])/200
    assert any(m.label=='Historical condition' for m in app.metric)
    assert load_monitoring(tmp_path)[0]['events']==[]
    by_label(app.selectbox,'Chart period').select('Custom dates').run()
    by_label(app.date_input,'Start date').set_value(pd.Timestamp('2008-01-01').date()).run()
    by_label(app.date_input,'End date').set_value(pd.Timestamp('2008-12-31').date()).run()
    chart=json.loads(app.get('plotly_chart')[0].proto.spec)
    assert all(x.startswith('2008-') for x in chart['data'][0]['x'])
    assert not app.exception


def test_first_visit_shows_plain_world_sma_until_a_threshold_is_entered(tmp_path):
    import json
    app=launch(tmp_path,seed=None)
    assert not app.exception
    assert by_label(app.selectbox,'Market index').value=='990100'
    assert by_label(app.selectbox,'SMA preset').value=='200 sessions'
    threshold=by_label(app.number_input,'Distance from SMA trigger (%)')
    assert threshold.value is None
    def traces():
        return [x['name'] for x in json.loads(app.get('plotly_chart')[0].proto.spec)['data']]
    assert traces()==['Completed close','SMA']
    assert not any(m.label in {'Rule status','Preview condition','Historical condition'} for m in app.metric)
    assert by_label(app.button,'Track rule').disabled
    assert by_label(app.number_input,'Approaching band (percentage points)').disabled
    threshold.set_value(0.).run()
    assert 'Trigger boundary' in traces() and 'Approaching boundary' in traces()
    assert any(m.label=='Preview condition' for m in app.metric)
    assert not by_label(app.button,'Track rule').disabled
    assert load_monitoring(tmp_path)[0]['rules']==[]
    by_label(app.number_input,'Distance from SMA trigger (%)').set_value(None).run()
    assert traces()==['Completed close','SMA']
    assert by_label(app.button,'Track rule').disabled
    assert not app.exception
