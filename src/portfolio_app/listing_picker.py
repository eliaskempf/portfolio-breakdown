"""One inline listing control with a lazy, searchable popup."""
from dataclasses import replace
from uuid import uuid4

import streamlit as st

from portfolio_app.instruments import InstrumentSearch, normalized_query, result_groups
from portfolio_app.search_widget import render_search_box
from portfolio_app.transaction_matching import exact_listings, instrument_fields, listing_currency, listing_label


def render_listing_picker(asset, listings, default, *, key, directory, currency, demo, searches, allow_clear):
    controls = st.session_state.setdefault('import_controls', {})
    ticker = st.session_state.setdefault(key, controls.get(key, default))
    controls[key] = ticker
    popup_key, search_key = key+'_picker', key+'_search'

    def toggle():
        if st.session_state[popup_key]:
            st.session_state.pop(search_key, None)
            st.session_state[search_key+'_generation'] = uuid4().hex

    chosen = listings.get(ticker)
    popup = st.popover(listing_label(chosen) if chosen else 'No price listing',
                       help='Price listing · '+asset, width='stretch', wrap=False,
                       key=popup_key, on_change=toggle)
    # Reserve the popup's size before lazy content arrives so placement can flip
    # above a field near the bottom of the viewport.
    with popup:
        panel = st.container(height=280, border=False)
    if not popup.open:
        return ticker
    with panel:
        state = st.session_state.get(search_key, {})
        query = normalized_query(state.get('query', ''))
        lookup = query or asset
        results, error = searches([lookup], directory, demo)[lookup] if len(lookup) >= 2 else ([], '')
        candidates = {**(listings if not query else {}), **{x.ticker:x for x in results}}
        # Exact crypto identities can have a reporting-currency pair omitted by broad search.
        preferred = sorted({x.ticker.rsplit('-', 1)[0]+'-'+currency
                            for x in exact_listings(lookup, list(candidates.values()), kind='CRYPTOCURRENCY')}
                           - candidates.keys())
        for extra, issue in searches(preferred, directory, demo).values():
            candidates.update({x.ticker:x for x in extra})
            error = error or issue
        ordered = sorted(candidates.values(), key=lambda x: (listing_currency(x) != currency, x.ticker))
        groups = result_groups([replace(x, currency=listing_currency(x), name=instrument_fields(x, asset)['name']) for x in ordered])

        def choose():
            choice = st.session_state.get(search_key, {}).get('selected') or {}
            if normalized_query(choice.get('query', '')) != query:
                return
            selected = choice.get('ticker')
            listing = candidates.get(selected)
            if listing is None and not (selected == '' and allow_clear):
                return
            if listing:
                if not demo:
                    listing = InstrumentSearch(directory/'.cache'/'yahoo').details(listing)
                st.session_state.setdefault('import_tx_chosen_listings', {}).setdefault(asset, {})[selected] = listing
            st.session_state[key] = controls[key] = selected
            st.session_state[popup_key] = False

        message = error or ('No matching listings.' if len(lookup) >= 2 and not candidates else '')
        render_search_box(query, groups, message, ticker, key=search_key, compact=True,
                          allow_clear=allow_clear, on_selected_change=choose)
        if error and st.button('Retry search', key=search_key+'_retry'):
            for term in (lookup, *preferred):
                st.session_state['import_tx_listing_cache'].pop((term, demo), None)
            st.rerun()
    return ticker
