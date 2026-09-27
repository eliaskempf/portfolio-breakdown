"""Session-local ETF look-through choices, independent of allocation ownership."""

from hashlib import sha256
import streamlit as st

from portfolio_app.display_names import compact_fund_name, instrument_name
from portfolio_app.etf import matching_fund


def render_etf_toggle(data_dir):
    context = sha256(str(data_dir.resolve()).encode()).hexdigest()[:16]
    key = f'etf_lookthrough_{context}'
    def remember():
        choices = dict(st.session_state.get('etf_breakdown_preferences', {}))
        choices[context] = st.session_state[key]
        st.session_state['etf_breakdown_preferences'] = choices
    return st.toggle('Break down ETFs', key=key, on_change=remember,
                     value=st.session_state.get('etf_breakdown_preferences', {}).get(context, True))


def render_etf_choices(holdings, funds, data_dir, *, enabled=True):
    context = sha256(str(data_dir.resolve()).encode()).hexdigest()[:16]
    held = {fund.isin for row in holdings.to_dict('records')
            if (fund := matching_fund(row, funds)) is not None}
    known = [fund for fund in funds if fund.isin in held]
    excluded_key = f'etf_excluded_{context}'
    excluded = set(st.session_state.get(excluded_key, []))
    if not enabled:
        return []

    def remember_fund(isin: str, widget_key: str) -> None:
        # Persist before rendering: another control can interrupt a rerun before
        # it reaches the end of this expander or hide these widgets entirely.
        saved = set(st.session_state.get(excluded_key, []))
        if st.session_state[widget_key]:
            saved.discard(isin)
        else:
            saved.add(isin)
        st.session_state[excluded_key] = sorted(saved)

    with st.container():
        for fund in known:
            position = next((row for row in holdings.to_dict('records') if matching_fund(row, [fund]) is not None), None)
            label = (instrument_name(position) if position else compact_fund_name(fund.name)) + (' (proxy)' if fund.proxy_source else '')
            widget_key = f'etf_expand_{context}_{fund.isin}'
            expand = st.toggle(label, value=fund.isin not in excluded,
                               key=widget_key, on_change=remember_fund, args=(fund.isin, widget_key),
                               help=f'{fund.isin} · Snapshot {fund.as_of.isoformat()}')
            if fund.proxy_source:
                st.caption(f'Proxy: {fund.proxy_source} · {fund.as_of.isoformat()}')
            if expand:
                excluded.discard(fund.isin)
            else:
                excluded.add(fund.isin)
        missing = sorted({instrument_name(row) for row in holdings.to_dict('records')
                          if row.get('instrument_type') == 'etf' and matching_fund(row, funds) is None})
        if missing:
            st.info('No breakdown available: ' + ', '.join(missing) + '. These remain whole fund positions.')
        if not known and not missing:
            st.caption('No supported ETF positions in this portfolio.')
    # Store exceptions separately so hiding the widgets does not erase choices.
    st.session_state[excluded_key] = sorted(excluded)
    return [fund for fund in known if fund.isin not in excluded]
