"""Compact per-asset approximate history over retained movement evidence."""
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from portfolio_app.asset_history import asset_history, project_asset_movements
from portfolio_app.asset_history_store import evidence_rows
from portfolio_app.chart_hover import chart_hover
from portfolio_app.charts import style_figure, chart_config
from portfolio_app.currency_display import money, reporting_currency
from portfolio_app.history_view import PERIODS, period_start
from portfolio_app.presentation import return_percent


def render_asset_history(directory, journal, scope, settings, *, key):
    from portfolio_app.market_data import history_for
    target = reporting_currency()
    if target != settings.currency:
        st.caption('Asset history needs dated prices and movement values in '+target+'.')
        return
    choices = {settings.anchors[p]['asset']: settings.anchors[p] for p in scope.positions}
    assets = sorted(choices)
    if len(assets) > 1:
        held = [a for a in assets if Decimal(choices[a]['quantity']) > 0]
        asset = st.selectbox('Asset', assets, index=assets.index(held[0] if held else assets[0]), key=key+'asset')
    else:
        asset = assets[0]
    anchor = choices[asset]
    st.caption(settings.scope_label, help='Performance covers these locations. Transfers into or out of this scope are valued as external flows, including transfers to or from an excluded exchange. It is not necessarily lifetime performance since the original purchase.')
    projection = project_asset_movements(evidence_rows(journal, settings), settings.owned_accounts, assets=(asset,))
    earliest = min((date.fromisoformat(m.date) for m in projection.movements), default=date.fromisoformat(settings.anchor_date))-timedelta(days=1)
    summary = st.container()
    period = st.segmented_control('Period', PERIODS, default='All', required=True,
        key=key+'asset_period', label_visibility='collapsed') or 'All'
    start = period_start(period, date.today(), earliest)
    service = history_for(directory)
    service.get(anchor['ticker'], 'Max')
    pending = service.pending(anchor['ticker'], 'Max')

    @st.fragment(run_every=2 if pending else None)
    def results():
        result = service.get(anchor['ticker'], 'Max')
        prices = {stamp[:10]: Decimal(str(value)) for stamp, value in zip(result.dates, result.prices)
                  if stamp[:10] <= date.today().isoformat()}
        if result.currency != target or not prices:
            st.caption('Loading daily prices…' if service.pending(anchor['ticker'], 'Max') else 'Daily prices unavailable. '+result.note)
            return
        end = min(date.today(), date.fromisoformat(max(prices)))
        if start > end:
            st.caption('No market prices in this period. Choose a longer period.')
            return
        history = asset_history(projection, asset, settings.anchor_date, anchor['quantity'], prices,
            currency=target, start=start.isoformat(), end=end.isoformat())
        latest = history.points[-1]
        with summary:
            columns = st.columns(4)
            columns[0].metric('Asset value', money(latest['value']))
            columns[1].metric('Known gain' if history.partial else 'Period gain', money(latest['gain'], signed=True),
                help='Ending value minus starting value and net external additions. Rewards are already included once. Unclassified movements are excluded from known gain.')
            columns[2].metric('Reward income', money(latest['income']),
                help='Value when rewards were received, using supplied estimates where available. Included in gain, not added to it again. Later price changes also affect gain.')
            columns[3].metric('Approx. return', return_percent(latest['return_pct']),
                help='Linked daily Dietz return with midpoint flow timing. This is not exact TWR or annualized return.')
            st.caption(('Approximate · partial evidence' if history.partial else 'Approximate asset history')+
                       f' · {start.isoformat()} – {end.isoformat()}')
        measure = st.segmented_control('History measure', ['Value', 'Gain', 'Return', 'Units'], default='Value',
            required=True, key=key+'asset_measure', label_visibility='collapsed') or 'Value'
        import plotly.graph_objects as go
        frame = pd.DataFrame(history.points)
        figure = style_figure(go.Figure())
        fields = [('value', 'Asset value'), ('capital', 'Gain reference' if history.partial else 'Net invested value')] if measure == 'Value' else [
            {'Gain': ('gain', 'Known gain' if history.partial else 'Period gain'),
             'Return': ('return_pct', 'Approx. return'), 'Units': ('quantity', asset+' units')}[measure]]
        for field, label in fields:
            figure.add_trace(go.Scatter(x=frame.date, y=[float(v) if v is not None else None for v in frame[field]],
                name=label, mode='lines', connectgaps=False,
                line=dict(dash='dash' if field == 'capital' else 'solid')))
        figure.update_layout(height=340, margin=dict(l=0,r=0,t=12,b=0),
            yaxis_title='%' if measure == 'Return' else asset+' units' if measure == 'Units' else target,
            legend=dict(orientation='h', y=1.12))
        st.plotly_chart(chart_hover(figure), key=key+'asset_chart', width='stretch', config=chart_config())
        with st.expander('Calculation and evidence'):
            st.write('Internal wallet and staking principal movements are excluded. Fees remain expenses; staking rewards increase units and income. '+settings.note)
            for reason in history.reasons:
                st.caption(reason)
            st.caption(f'Derived opening units: {history.opening_quantity} {asset}. This is implied by the supplied ending balance and movements, not an independently observed opening.')
            st.caption(f'{projection.ignored_internal} internal principal movements excluded. Ending balance reference: {settings.anchor_date}; this is a reconstruction boundary, not an asserted wallet observation date.')
            if projection.issues:
                st.caption('Affected gains and returns remain unavailable until these movements are resolved.')
            st.dataframe(pd.DataFrame([dict(Date=m.date, Type=m.kind, Units=str(m.quantity),
                Value=str(m.amount) if m.amount is not None else 'Daily close / unknown', Currency=m.currency,
                Evidence=m.note) for m in projection.movements]), hide_index=True, width='stretch')
        if pending and not service.pending(anchor['ticker'], 'Max'):
            st.rerun()

    results()
