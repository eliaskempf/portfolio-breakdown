"""Net movement diagnostics and approximate prices over activated forward history."""
from datetime import date
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio_app.asset_history import journal_asset_history
from portfolio_app.asset_history_store import evidence_rows
from portfolio_app.chart_hover import chart_hover
from portfolio_app.charts import style_figure, chart_config
from portfolio_app.currency_display import money, reporting_currency
from portfolio_app.history_view import PERIODS, period_start
from portfolio_app.presentation import return_percent
from portfolio_app.transaction_evidence import asset_movement_totals, recorded_asset_changes


def render_net_changes(rows, owned_accounts=None, *, asset=None, key='net_', chart=True):
    totals = asset_movement_totals(rows, owned_accounts)
    if asset is not None:
        totals = tuple(t for t in totals if t['asset'] == asset)
    st.dataframe(pd.DataFrame(totals).rename(columns={'asset':'Asset', 'known_unit_change':'Recorded net change',
        'undated_movements':'Undated movements', 'unknown_asset_charges':'Unknown asset charges'}), hide_index=True, width='stretch')
    if asset is not None and chart:
        points = recorded_asset_changes(rows, asset, owned_accounts)
        if points:
            fig = style_figure(go.Figure(go.Scatter(x=[p['date'] for p in points],
                y=[float(p['net_change']) for p in points], mode='lines+markers', name='Recorded net change')))
            fig.update_layout(height=340, yaxis_title=asset+' unit change')
            st.plotly_chart(chart_hover(fig), key=key+'net_chart', width='stretch', config=chart_config())
    st.caption('Signed changes, not absolute balances. Openings and checkpoints are separate; undated movements appear only in totals.')
    boundaries = [dict(Asset=r.facts['asset_id'], Location=r.facts['account_id'], Type=r.facts['event_type'],
        Date=r.date or 'Unknown', Quantity=r.facts['quantity']) for r in rows
        if r.facts['event_type'] in ('opening', 'checkpoint') and (asset is None or r.facts['asset_id'] == asset)
        and (owned_accounts is None or r.facts['account_id'] in owned_accounts)]
    if boundaries:
        with st.expander('Separate balance evidence'):
            st.dataframe(pd.DataFrame(boundaries), hide_index=True, width='stretch')
            st.caption('A location-specific balance does not establish an opening for all owned locations.')


def render_asset_history(directory, journal, scope, settings, *, key):
    from portfolio_app.market_data import history_for
    from portfolio_app.history_observations import load_observations
    from portfolio_app.security_history import selected_events
    choices = {settings.anchors[p]['asset']: (p, settings.anchors[p]) for p in scope.positions}
    assets = sorted(choices)
    asset = st.selectbox('Asset', assets, key=key+'asset') if len(assets) > 1 else assets[0]
    position, config = choices[asset]
    rows = evidence_rows(journal, settings)
    st.caption(settings.scope_label)
    book = next((b for b in journal.securities if b.position_id == position and b.active), None)
    boundaries = [e.date for e in selected_events(book) if e.kind in ('opening', 'checkpoint')] if book else []
    if not boundaries:
        st.caption('Opening inventory unknown · recorded net changes')
        render_net_changes(rows, settings.owned_accounts, asset=asset, key=key)
        return
    # Receipt mappings describe the activated scope; view settings cannot expand
    # a custody book into an aggregate history by changing only its label.
    bound = {(a, s) for r in journal.transaction_imports for a, s, p in r.positions if p == position}
    requested = {(a, s) for r in rows for a, s in r.pairs if a in settings.owned_accounts and s == asset}
    if requested != {(a,s) for a,s in bound if s == asset}:
        st.caption('Activated history covers a different location scope · recorded net changes')
        render_net_changes(rows, settings.owned_accounts, asset=asset, key=key)
        return
    target = reporting_currency()
    earliest = date.fromisoformat(min(boundaries))
    summary = st.container()
    period = st.segmented_control('Period', PERIODS, default='All', required=True,
        key=key+'asset_period', label_visibility='collapsed') or 'All'
    start = period_start(period, date.today(), earliest)
    if not config['ticker']:
        st.caption('Connect a price listing for historical values. Recorded units remain available below.')
        render_net_changes(rows, settings.owned_accounts, asset=asset, key=key)
        return
    service = history_for(directory)
    service.get(config['ticker'], 'Max')
    pending = service.pending(config['ticker'], 'Max')

    @st.fragment(run_every=2 if pending else None)
    def results():
        result = service.get(config['ticker'], 'Max')
        prices = {stamp[:10]: Decimal(str(value)) for stamp, value in zip(result.dates, result.prices)
                  if stamp[:10] <= date.today().isoformat()}
        if result.currency != target or not prices:
            st.caption('Loading daily prices…' if service.pending(config['ticker'], 'Max') else 'Daily prices unavailable in '+target+'. '+result.note)
            render_net_changes(rows, settings.owned_accounts, asset=asset, key=key)
            return
        end = min(date.today(), date.fromisoformat(max(prices)))
        if max(start, earliest) > end:
            st.caption('No market prices in the supported forward interval.')
            render_net_changes(rows, settings.owned_accounts, asset=asset, key=key)
            return
        history = journal_asset_history(journal, position, prices, load_observations(directory)[0],
            currency=target, start=start.isoformat(), end=end.isoformat())
        latest = history.points[-1]
        with summary:
            columns = st.columns(4)
            columns[0].metric('Asset value', money(latest['value']))
            columns[1].metric('Known gain' if history.partial else 'Period gain', money(latest['gain'], signed=True),
                help='Change in market value less net external additions. Rewards are included once. Unresolved flows leave dependent gains unavailable.')
            columns[2].metric('Recorded income', money(latest['income']), help='Supplied income valuations; estimates retain their provenance. Included in gain once.')
            columns[3].metric('Approx. return', return_percent(latest['return_pct']),
                help='Linked daily Dietz with midpoint flow timing, not exact TWR or annualized return.')
            st.caption(('Approximate · partial evidence' if history.partial else 'Approximate forward history')+
                       f" · {history.points[0]['date']} – {end.isoformat()}")
        measure = st.segmented_control('History measure', ['Value', 'Gain', 'Return', 'Units'], default='Value',
            required=True, key=key+'asset_measure', label_visibility='collapsed') or 'Value'
        fields = [('value', 'Asset value'), ('capital', 'Period value and net additions')] if measure == 'Value' else [
            {'Gain': ('gain', 'Period gain'), 'Return': ('return_pct', 'Approx. return'), 'Units': ('quantity', asset+' units')}[measure]]
        figure = style_figure(go.Figure())
        for field, label in fields:
            figure.add_trace(go.Scatter(x=[p['date'] for p in history.points],
                y=[float(p[field]) if p[field] is not None else None for p in history.points],
                name=label, mode='lines', connectgaps=False, line=dict(dash='dash' if field == 'capital' else 'solid')))
        figure.update_layout(height=340, margin=dict(l=0,r=0,t=12,b=0),
            yaxis_title='%' if measure == 'Return' else asset+' units' if measure == 'Units' else target,
            legend=dict(orientation='h', y=1.12))
        st.plotly_chart(chart_hover(figure), key=key+'asset_chart', width='stretch', config=chart_config())
        with st.expander('Calculation and evidence'):
            st.caption('Units follow the activated forward journal. Ending comparisons never determine earlier quantities. '+settings.note)
            for reason in history.reasons:
                st.caption(reason)
            render_net_changes(rows, settings.owned_accounts, asset=asset, key=key+'evidence_', chart=False)
        if pending and not service.pending(config['ticker'], 'Max'):
            st.rerun()
    results()
