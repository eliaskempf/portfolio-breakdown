"""Session-local ETF look-through choices, independent of allocation ownership."""

from hashlib import sha256
import streamlit as st

from portfolio_app.interface.shared.display_names import instrument_name
from portfolio_app.funds.etf import matching_fund


def render_etf_toggle(data_dir, *, futures=False):
    context = sha256(str(data_dir.resolve()).encode()).hexdigest()[:16]
    key = f'etf_lookthrough_{context}'
    def remember():
        choices = dict(st.session_state.get('etf_breakdown_preferences', {}))
        choices[context] = st.session_state[key]
        st.session_state['etf_breakdown_preferences'] = choices
    return st.toggle('Look through funds & futures' if futures else 'Break down ETFs', key=key, on_change=remember,
                     value=st.session_state.get('etf_breakdown_preferences', {}).get(context, True))


def render_etf_choices(holdings, funds, data_dir, *, enabled=True):
    context = sha256(str(data_dir.resolve()).encode()).hexdigest()[:16]
    panel = st.expander('Individual ETFs', key='exposure_individual_funds_open', on_change='rerun')
    if not enabled:
        return []
    excluded_key = f'etf_excluded_{context}'
    excluded = set(st.session_state.get(excluded_key, []))
    positions = {}
    missing = set()
    for row in holdings.to_dict('records'):
        fund = matching_fund(row, funds)
        if fund:
            positions.setdefault(fund.isin, row)
        elif panel.open and row.get('instrument_type') == 'etf':
            missing.add(instrument_name(row))
    known = [fund for fund in funds if fund.isin in positions]
    selected = [fund for fund in known if fund.isin not in excluded]
    if not panel.open:
        return selected

    def remember_fund(isin: str, widget_key: str) -> None:
        # Persist before rendering: another control can interrupt a rerun before
        # it reaches the end of this expander or hide these widgets entirely.
        saved = set(st.session_state.get(excluded_key, []))
        if st.session_state[widget_key]:
            saved.discard(isin)
        else:
            saved.add(isin)
        st.session_state[excluded_key] = sorted(saved)

    with panel:
        for fund in known:
            label = instrument_name(positions[fund.isin]) + (' (proxy)' if fund.proxy_source else '')
            widget_key = f'etf_expand_{context}_{fund.isin}'
            st.toggle(label, value=fund.isin not in excluded,
                               key=widget_key, on_change=remember_fund, args=(fund.isin, widget_key),
                               help=f'{fund.isin} · Snapshot {fund.as_of.isoformat()}')
            if fund.proxy_source:
                st.caption(f'Proxy: {fund.proxy_source} · {fund.as_of.isoformat()}')
        if missing:
            st.info('No breakdown available: ' + ', '.join(sorted(missing)) + '. These remain whole fund positions.')
        if not known and not missing:
            st.caption('No supported ETF positions in this portfolio.')
    return selected
