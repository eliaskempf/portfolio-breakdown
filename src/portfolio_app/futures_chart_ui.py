"""Underlying market context in the ordinary position chart component."""
import streamlit as st

from portfolio_app.futures_chart_reference import REFERENCES, reference_for
from portfolio_app.market_history_ui import render_market_history


def render_futures_chart(directory, contracts, *, key):
    unique = {(c.exchange, c.product, c.currency, c.return_convention): c for c in contracts}
    choices = list(unique)
    selected = st.session_state.get(key+'selected_market', choices[0])
    if selected not in unique:
        selected = choices[0]
    # Widget state is available before rendering, including on a selection change.
    selected = st.session_state.get(key+'market', selected)
    if selected not in unique:
        selected = choices[0]
    contract = unique[selected]
    reference = reference_for(contract)
    verified = reference is not None
    choice_key = key+'reference_'+':'.join(selected)
    saved_key = key+'selected_reference_'+':'.join(selected)
    chosen = st.session_state.get(choice_key, st.session_state.get(saved_key))
    if not verified:
        reference = REFERENCES.get(chosen)
    result = None
    if reference:
        subtitle = f'{reference.name} · {reference.convention} · {reference.currency}'
        st.caption(subtitle + ('' if verified else ' · Comparison'))
        result = render_market_history(reference.ticker, directory, key=key+reference.ticker+'_',
            index_points=True, expected_currency=reference.currency, compact=True)
    else:
        st.caption('Choose a market reference to display a chart.')
    with st.expander('Chart details', expanded=reference is None):
        if len(choices) > 1:
            market = st.selectbox('Contract market', choices, index=choices.index(selected),
                format_func=lambda item: f'{item[1]} · {item[2]} · {item[3]} return', key=key+'market')
            st.session_state[key+'selected_market'] = market
        if not verified:
            options = [None, *REFERENCES]
            symbol = st.selectbox('Chart reference', options,
                index=options.index(chosen) if chosen in options else 0,
                format_func=lambda symbol: ('Choose a reference' if symbol is None else
                    f'{REFERENCES[symbol].name} · {REFERENCES[symbol].currency} · {REFERENCES[symbol].convention}'),
                key=choice_key)
            st.session_state[saved_key] = symbol
            st.caption('User-selected comparison; its match to this contract’s underlying is unverified.')
        if reference:
            st.caption(f'Source: {reference.source} · {reference.ticker} · {reference.currency}')
            st.caption('Native index points, not futures prices or personal returns. '
                'Contract quantities, futures basis, rolls, fees and recorded taxes are excluded. '
                'Changing portfolio currency does not convert index levels into cash prices.')
        if result:
            if result.fetched_at:
                st.caption(f'History last retrieved: {result.fetched_at}')
            if result.note and result.status in {'fresh', 'cached'}:
                st.caption(result.note)
