"""Session-local ETF look-through choices, independent of allocation ownership."""

from hashlib import sha256
from pathlib import Path

import pandas as pd

import streamlit as st

from portfolio_app.display_names import display_name
from portfolio_app.etf import FundSnapshot, matching_fund


def render_etf_selection(holdings: pd.DataFrame, funds: list[FundSnapshot], data_dir: Path) -> tuple[bool, list[FundSnapshot]]:
    context = sha256(str(data_dir.resolve()).encode()).hexdigest()[:16]
    enabled = st.toggle('Break down ETFs', key=f'etf_lookthrough_{context}')
    held = {fund.isin for row in holdings.to_dict('records')
            if (fund := matching_fund(row, funds)) is not None}
    known = [fund for fund in funds if fund.isin in held]
    excluded_key = f'etf_excluded_{context}'
    excluded = set(st.session_state.get(excluded_key, []))
    if not enabled:
        return False, []
    with st.expander('Individual ETFs', expanded=True):
        for fund in known:
            label = display_name(fund.name) + (' (proxy)' if fund.proxy_source else '')
            expand = st.toggle(label, value=fund.isin not in excluded,
                               key=f'etf_expand_{context}_{fund.isin}',
                               help=f'{fund.isin} · Snapshot {fund.as_of.isoformat()}')
            if fund.proxy_source:
                st.caption(f'Proxy: {fund.proxy_source} · {fund.as_of.isoformat()}')
            if expand:
                excluded.discard(fund.isin)
            else:
                excluded.add(fund.isin)
        missing = sorted({display_name(row['name']) for row in holdings.to_dict('records')
                          if row.get('instrument_type') == 'etf' and matching_fund(row, funds) is None})
        if missing:
            st.info('No breakdown available: ' + ', '.join(missing) + '. These remain whole fund positions.')
        if not known and not missing:
            st.caption('No supported ETF positions in this portfolio.')
    # Store exceptions separately so hiding the widgets does not erase choices.
    st.session_state[excluded_key] = sorted(excluded)
    return True, [fund for fund in known if fund.isin not in excluded]
