"""Price provenance and explicit market-data actions for the futures sandbox."""
from datetime import date

import streamlit as st

from portfolio_app.futures_market import EUREX_URL, QUOTE_FILE, apply_quote, load_observation, refresh_quote
from portfolio_app.holdings import DataError


def render_market_reference(directory, position, revision):
    if position.instrument != 'FMWN':
        return
    with st.expander('Dated FMWN market price'):
        st.markdown(f'[Eurex FMWN settlements]({EUREX_URL})')
        try:
            quote = load_observation(directory, QUOTE_FILE)
        except DataError as exc:
            st.warning(str(exc))
            quote = None
        if st.button('Refresh Eurex settlement'):
            try:
                with st.spinner('Reading the latest completed Eurex trading day…'):
                    refresh_quote(directory)
            except (OSError, ValueError) as exc:
                st.error(f'Quote refresh failed; saved position unchanged. {exc}')
            else:
                st.rerun()
        if quote:
            st.write(f"{quote['price']:,.2f} points · FMWN last trading day {quote['contract_expiry']} · "
                     f"Daily settlement for {quote['quote_date']}")
            st.caption(f"Retrieved {quote['fetched_at']}. This is a daily reference price, not a live bid/ask or execution price. "
                       'The source supplies a business date, not a trade timestamp.')
            age = (date.today() - date.fromisoformat(quote['quote_date'])).days
            if age > 4:
                st.warning(f'The saved settlement is {age} calendar days old. Refresh before interpreting current risk.')
            st.caption('Starting at this settlement replaces both entry and mark, resets mock P&L, and backs up the prior position. '
                       'For a funded strategy, close the existing contract first to retain its P&L; previous contracts’ gains and cash are preserved. '
                       'Downloading a new quote alone never changes an existing trade.')
            if st.button('Start mock at saved settlement'):
                try:
                    apply_quote(directory, revision)
                except (OSError, ValueError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        else:
            st.warning('No dated market price is loaded. The default 500 points are invented and cannot represent current exposure.')


def render_price_provenance(position, entry, mark):
    if position.quote_date:
        st.caption(f'Price reference: {position.reference_price:,.2f} points, Eurex daily settlement {position.quote_date}; '
                   f'contract last trading day {position.contract_expiry}. No intraday timestamp or executable bid/ask is available.')
        if position.entry_date:
            st.caption(f'Simulated entry: {position.entry_date} at {position.entry_reference_price:,.2f} points. '
                       f'{position.entry_convention or "Assumed execution at the dated daily settlement"}. This is not an executed trade.')
            st.markdown(f'[Entry settlement source]({position.entry_price_source}) · [Mark settlement source]({position.price_source})')
            if entry != position.entry_reference_price:
                st.warning('Entry price override: the simulated entry differs from its dated settlement.')
            if mark != position.reference_price:
                st.warning('Mark price override: the simulated mark differs from its dated settlement.')
        elif entry != position.reference_price or mark != position.reference_price:
            st.warning('Price override: the entry or mark differs from the saved settlement. These edited prices are hypothetical.')
        if (date.today() - date.fromisoformat(position.quote_date)).days > 4:
            st.warning('This price reference is more than four calendar days old. Refresh the settlement for a current experiment.')
    else:
        st.caption('The initial 500-point prices are invented, not a live quote. Changing entry price redefines '
                   'the simulated investment; changing the current price changes its cumulative gain or loss.')
