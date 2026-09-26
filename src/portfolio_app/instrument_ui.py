"""Session-local search with grouped listings and explicit selection."""

from pathlib import Path
from time import monotonic

import pandas as pd
import streamlit as st

from portfolio_app.instruments import InstrumentSearch, catalog_search, normalized_query, result_groups
from portfolio_app.search_widget import SEARCH_KEY, render_search_box


def render_instrument_search(prefix: str, holdings: pd.DataFrame, cache_dir: Path, *, disabled: bool = False) -> None:
    state = st.session_state.get(SEARCH_KEY, {})
    query = normalized_query(str(state.get("query") or ""))
    kind = st.radio("Search for", ["All", "Equities", "ETFs", "Crypto"], horizontal=True, key="position_edit_search_kind", label_visibility="collapsed")
    cache = st.session_state.setdefault("position_edit_search_cache", {})
    cache_key = (query, disabled)
    results, error = [], ""
    if len(query) >= 2:
        entry = cache.get(cache_key)
        if entry is None or monotonic() - entry[0] > 300:
            try:
                if disabled:
                    results = catalog_search(query)
                else:
                    provider = InstrumentSearch(cache_dir)
                    results = provider.search(query)
                    error = getattr(provider, "last_error", "")
            except Exception:
                results = catalog_search(query)
                error = "Live search is unavailable. Try again or enter the instrument manually."
            if len(cache) >= 40:
                del cache[next(iter(cache))]
            cache[cache_key] = (monotonic(), results, error)
        else:
            _, results, error = entry
    results = [item for item in results if kind == "All" or item.kind == {"Equities": "EQUITY", "ETFs": "ETF", "Crypto": "CRYPTOCURRENCY"}.get(kind)]
    if kind == "Crypto":
        results.sort(key=lambda item: (not item.ticker.endswith('-EUR'), item.ticker))
    groups = result_groups(results)
    if len(query) < 2:
        message = "Search by name, theme, ticker or ISIN. Choose an exchange listing to fill the form."
    elif error:
        message = error
    elif not groups:
        message = "No matching investments. Try another name or ticker, or enter the details below."
    else:
        message = f"{len(groups)} matching investment(s) · Select the exchange and currency you use."
    selected = render_search_box(query, groups, message, st.session_state.get("position_edit_search_applied", ""))
    if error and st.button("Retry search"):
        cache.pop(cache_key, None)
        st.rerun()
    st.caption("Offline catalog only in demo mode." if disabled else "Live suggestions appear as you type. Only search text and selected instrument identifiers are sent to market-data providers.")
    if selected and isinstance(selected, dict) and normalized_query(str(selected.get("query", ""))) == query:
        listing = next((item for item in results if item.ticker == selected.get("ticker")), None)
        if listing is not None:
            if not disabled:
                try:
                    with st.spinner("Getting listing details…"):
                        listing = InstrumentSearch(cache_dir).details(listing)
                except Exception:
                    pass
            matches = holdings.loc[holdings["ticker"] == listing.ticker]
            if not matches.empty:
                st.session_state["position_edit_pending_instrument"] = matches.iloc[0]["id"]
                st.rerun()
            for field in ("name", "ticker", "isin"):
                st.session_state[prefix + field] = getattr(listing, field)
            st.session_state[prefix + 'instrument_type'] = {'EQUITY': 'equity', 'ETF': 'etf', 'CRYPTOCURRENCY': 'crypto'}.get(listing.kind, 'unknown')
            st.session_state["position_edit_search_applied"] = listing.ticker
            notice = f"Selected {listing.ticker} · {listing.exchange}."
            if listing.currency:
                notice += f" Trading currency: {listing.currency}."
            if not listing.isin:
                notice += " ISIN unavailable; you can enter it manually."
            st.session_state["position_edit_search_notice"] = notice
    if notice := st.session_state.get("position_edit_search_notice"):
        st.success(notice)
