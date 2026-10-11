"""Retrieve prices into a draft, then explicitly review and post daily settlements."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from portfolio_app import account_journal as ledger
from portfolio_app.accounts_ui import _save_review
from portfolio_app.derivative_market import SettlementCache
from portfolio_app.holdings import DataError
from portfolio_app.investment_history import settlement_history
from portfolio_app.settlement_backfill import propose_settlements, retrieve_settlements


def render_settlement_backfill(directory, journal, revision, members, *, key):
    state = ledger.replay(journal)
    st.markdown('**Retrieve daily settlements**')
    identity = st.selectbox('Contract', members,
        format_func=lambda pid: f'{state.positions[pid].contract.product} · {state.positions[pid].contract.expiry}',
        key=key+'contract')
    key += identity+'_'
    events = [e for e in ledger.effective_events(journal) if e.position_id == identity
              and e.kind in {'trade', 'snapshot', 'settlement', 'final_settlement'}]
    yesterday = date.today()-timedelta(days=1)
    first = min((date.fromisoformat(e.date)+timedelta(days=e.kind == 'snapshot') for e in events), default=yesterday)
    left, right = st.columns(2)
    start = left.date_input('From', value=min(yesterday, max(first, yesterday-timedelta(days=6))),
        max_value=yesterday, key=key+'from')
    end = right.date_input('Through', value=yesterday, max_value=yesterday, key=key+'through')
    context = (revision, start.isoformat(), end.isoformat())
    if st.button('Retrieve settlements', icon=':material/download:', key=key+'fetch',
                 help='Free exact-contract daily prices, up to 31 calendar days. Missing dates stay visible. Fetching does not change account cash.'):
        try:
            cache = SettlementCache(directory/'.cache'/'derivative-settlements')
            def fetch(contract, day):
                result = cache.get(contract, day)
                if result.observation is None:
                    raise DataError(result.message or 'No exact-contract settlement available.')
                return result.observation
            with st.spinner('Retrieving exact-contract settlements…'):
                days = retrieve_settlements(journal, identity, start.isoformat(), end.isoformat(), fetch=fetch)
            st.session_state[key+'download'] = (context, days)
        except (DataError, OSError, ValueError) as exc:
            st.error(str(exc))
    downloaded = st.session_state.get(key+'download')
    if downloaded is None or downloaded[0] != context:
        return
    days = downloaded[1]
    if not days:
        st.info('No weekdays in this range.')
        return
    st.dataframe(pd.DataFrame([{'Date': day.date, 'Settlement price': str(day.observation.price) if day.observation else '',
        'Status': day.status} for day in days]), hide_index=True, width='stretch')
    with st.expander('Sources & missing dates'):
        st.dataframe(pd.DataFrame([{'Date': day.date,
            'Source / detail': day.observation.source if day.observation else day.message or day.status}
            for day in days]), hide_index=True, width='stretch')
    observations = [day.observation for day in days if day.observation]
    if not observations:
        return
    selected = st.multiselect('Dates to record', [item.date for item in observations],
        default=[item.date for item in observations], key=key+'selected_'+str(start)+'_'+str(end),
        help='Only selected available days are posted. Each settlement follows that day’s trades and posts cash on its business date. Use Record transaction for different cash posting dates.')
    try:
        candidate = propose_settlements(journal, identity, start.isoformat(), end.isoformat(),
            [item for item in observations if item.date in selected])
        history = settlement_history(candidate, [identity])
        st.dataframe(history.loc[history.Date.isin(selected), ['Date','Contracts','Settlement P&L','Currency']],
            hide_index=True, width='stretch')
        _save_review(directory, journal, revision, candidate, key+'posting_',
            review_label='Review settlements', save_label='Save settlements')
    except (DataError, ValueError) as exc:
        st.info(str(exc))
