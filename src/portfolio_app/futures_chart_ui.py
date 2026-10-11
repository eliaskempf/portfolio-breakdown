"""Underlying market context in the ordinary position chart component."""
import streamlit as st

from portfolio_app.futures_chart_reference import REFERENCES, reference_for
from portfolio_app.market_history_ui import render_market_history


def render_futures_chart(directory, contracts, *, key):
    # Rolls of the same product share a reference. Distinct products are never
    # spliced into one historical series, even in a manually grouped investment.
    unique = {(c.exchange, c.product, c.currency, c.return_convention): c for c in contracts}
    choices = list(unique)
    selected = (st.selectbox('Contract market', choices,
                format_func=lambda item: f'{item[1]} · {item[2]} · {item[3]} return', key=key+'market')
                if len(choices) > 1 else choices[0])
    contract = unique[selected]
    reference = reference_for(contract)
    verified = reference is not None
    if reference is None:
        st.info('No verified chart reference is configured for this contract. Choose a market comparison for this session.')
        symbol = st.selectbox('Chart reference', [None, *REFERENCES],
            format_func=lambda symbol: ('Choose a reference' if symbol is None else
                f'{REFERENCES[symbol].name} · {REFERENCES[symbol].currency} · {symbol}'),
            key=key+'reference_'+':'.join(selected))
        if symbol is None:
            return
        reference = REFERENCES[symbol]
        st.caption('User-selected comparison; its match to this contract’s underlying is unverified.')
    st.caption(f'{reference.name} · {reference.ticker} · {reference.currency} · {reference.convention} · Source: Yahoo Finance')
    if reference.index_points:
        heading = 'Underlying index history — proxy'
        caption = ('Index closing levels in native points · '+reference.convention+
                   ' · Index currency: '+reference.currency+' · Not your personal return history')
        st.caption('Index points remain in the index’s native convention when portfolio currency changes.')
    else:
        heading = 'Underlying market history — ETF proxy'
        caption = ('ETF closing prices in {currency} · Income accumulates within the fund; '
                   'fund costs and tracking differences remain · Not your personal return history')
    if not verified:
        heading = 'Market comparison — ' + ('index' if reference.index_points else 'ETF proxy')
    st.caption('Market context only. This is not a futures-price or settlement series; '
               'it does not include your contract quantity, futures basis, rolls, fees or recorded taxes.')
    render_market_history(reference.ticker, directory, key=key+reference.ticker+'_',
                          heading=heading, caption=caption, index_points=reference.index_points,
                          expected_currency=reference.currency)
