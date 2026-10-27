"""Compact import mappings using the application's public listing search."""
from concurrent.futures import ThreadPoolExecutor
from time import monotonic

import streamlit as st

from portfolio_app.instruments import Instrument, InstrumentSearch, catalog_search, normalized_query, result_groups
from portfolio_app.import_state import review_control
from portfolio_app.search_widget import render_search_box
from portfolio_app.transaction_csv import digest
from portfolio_app.transaction_matching import holding_matches, suggested_listing, instrument_fields, holding_label, listing_label


def _fetch(query, cache_dir, demo):
    if demo:
        return catalog_search(query), ''
    try:
        provider = InstrumentSearch(cache_dir)
        results = provider.search(query)
        return results, provider.last_error
    except Exception:
        return catalog_search(query), 'Live search unavailable. Local matches are shown.'


def searches(queries, directory, demo):
    cache = st.session_state.setdefault('import_tx_listing_cache', {})
    missing = [q for q in dict.fromkeys(queries) if (q, demo) not in cache or monotonic()-cache[q, demo][0] > 300]
    if missing:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = pool.map(lambda q: _fetch(q, directory/'.cache'/'yahoo', demo), missing)
            for query, (listings, error) in zip(missing, results):
                cache[query, demo] = (monotonic(), listings, error)
    return {q: cache[q, demo][1:] for q in queries}


def _control(widget, *args, key, **kwargs):
    return review_control(widget, *args, key=key, **kwargs)


def _suggest_default(key, suggestion):
    """Refresh an automatic suggestion without overwriting an edited value."""
    controls = st.session_state.setdefault('import_controls', {})
    previous = st.session_state.get(key+'_suggestion', suggestion)
    value = st.session_state.get(key, controls.get(key, previous))
    if suggestion != previous and value == previous:
        st.session_state[key] = suggestion
        controls[key] = suggestion
    st.session_state[key+'_suggestion'] = suggestion


def render_mapping(directory, snapshot, journal, rows, token, *, currency='EUR', demo=False):
    pairs = sorted({p for r in rows if not r.issues for p in r.pairs})
    assets = sorted({asset for _, asset in pairs})
    source = rows[0].facts['source_id']
    holdings = snapshot.holdings
    names = {r.position_id: holding_label(r) for _, r in holdings.iterrows()}
    by_position = holdings.set_index('position_id')
    retained = {(r.facts['source_id'], a, asset): pos for r in journal.transaction_imports for a, asset, pos in r.positions}
    matches = {a: holding_matches(a, holdings) for a in assets}
    crypto = not holdings.empty and holdings.instrument_type.eq('crypto').all()
    kind = 'CRYPTOCURRENCY' if crypto else ''
    lookup = [a for a in assets if (not matches[a] or (len(matches[a]) == 1 and not by_position.loc[matches[a][0], 'ticker'])) and not any((source, account, a) in retained for account, symbol in pairs if symbol == a)]
    with st.spinner('Finding price listings…', show_time=False):
        initial = searches(lookup, directory, demo)
        preferred = [a+'-'+currency for a in lookup if crypto and not any(x.ticker == a.upper()+'-'+currency for x in initial[a][0])]
        extra = searches(preferred, directory, demo)
    custom = st.session_state.setdefault('import_tx_chosen_listings', {})
    mapping, additions, listing_changes, selected_listings, new_details = {}, [], {}, {}, []
    prefix = 'import_tx_'+token
    st.markdown('**Assets**')
    left, middle, right, _ = st.columns([1, 4.4, 3, 1])
    left.caption('CSV asset'); middle.caption('Holding'); right.caption('Price listing')
    for asset in assets:
        key = prefix+digest(asset)[:12]+'_'
        asset_pairs = [(a, symbol) for a, symbol in pairs if symbol == asset]
        prior = {pair: retained[(source, *pair)] for pair in asset_pairs if (source, *pair) in retained}
        retained_positions = set(prior.values())
        asset_col, holding_col, listing_col, search_col = st.columns([1, 4.4, 3, 1], vertical_alignment='center')
        asset_col.markdown('**'+asset+'**')
        if retained_positions - names.keys():
            holding_col.caption('A previously matched holding is missing. Review the saved import.')
            continue
        if len(prior) == len(asset_pairs) and len(retained_positions) > 1:
            mapping.update(prior)
            holding_col.caption(', '.join(names[p] for p in sorted(retained_positions)))
            listing_col.caption('Existing listings')
            continue
        candidates = matches[asset]
        default = next(iter(retained_positions)) if len(retained_positions) == 1 else candidates[0] if len(candidates) == 1 else None if candidates else ''
        options = [None, '', *names] if default is None else ['', *names]
        current_choice = st.session_state.get(key+'position', st.session_state.get('import_controls', {}).get(key+'position', default))
        choice_col, name_col = holding_col.columns([1, 1.5]) if current_choice == '' else (holding_col, holding_col)
        selected = _control(choice_col.selectbox, 'Holding · '+asset, options,
            index=options.index(default), format_func=lambda p: 'Choose a holding' if p is None else 'New position' if p == '' else names[p],
            filter_mode=None, disabled=bool(retained_positions), label_visibility='collapsed', key=key+'position')
        if selected is None:
            listing_col.caption('Multiple holdings match' if len(candidates) > 1 else 'Choose an existing holding')
            continue
        existing = by_position.loc[selected] if selected else None
        available = [*initial.get(asset, ([], ''))[0], *extra.get(asset+'-'+currency, ([], ''))[0], *custom.get(asset, {}).values()]
        current = str(existing.ticker) if existing is not None else ''
        if current:
            available.append(Instrument(current, existing['name'], kind={'crypto':'CRYPTOCURRENCY','etf':'ETF','etc':'ETC'}.get(existing.instrument_type,'EQUITY'), isin=existing['isin']))
        by_ticker = {x.ticker: x for x in available}
        proposed = suggested_listing(asset, list(by_ticker.values()), currency, kind=kind)
        if existing is not None and existing['isin'] and proposed and proposed.isin != existing['isin']:
            proposed = None
        default_ticker = current or (proposed.ticker if proposed else '')
        ticker_options = ([] if current else ['']) + sorted(by_ticker, key=lambda t: (t != default_ticker, not t.endswith('-'+currency), t))
        ticker_key = key+'listing_'+(selected or 'new')
        if pending := st.session_state.pop(key+'pending_listing', None):
            st.session_state[ticker_key] = pending
            st.session_state.setdefault('import_controls', {})[ticker_key] = pending
        ticker = _control(listing_col.selectbox, 'Price listing · '+asset, ticker_options,
            index=ticker_options.index(default_ticker), label_visibility='collapsed', key=ticker_key,
            filter_mode=None, format_func=lambda t, listings=by_ticker: listing_label(listings[t]) if t else 'No price listing')
        listing = by_ticker.get(ticker)
        if listing:
            custom.setdefault(asset, {})[ticker] = listing
        selected_listings[asset] = ticker
        if search_col.button('Search', help='Search listings for '+asset, key=key+'search'):
            st.session_state[prefix+'search_asset'] = asset
        fields = instrument_fields(listing, asset)
        if existing is None:
            _suggest_default(key+'name', fields['name'])
            fields['name'] = _control(name_col.text_input, 'Name · '+asset, value=fields['name'], key=key+'name',
                label_visibility='collapsed', help='Name to save for this new holding. Editing it does not change the price listing.')
            selected = 'csv-position-'+digest((source, asset))[:24]
            addition = dict(position_key=selected, id='csv-asset-'+digest((source,asset))[:24], account='Portfolio', **fields)
            additions.append(addition)
            new_details.append((asset, key, addition))
        elif listing and ticker != current:
            listing_changes[existing.id] = listing
        for pair in asset_pairs:
            mapping[pair] = prior.get(pair, selected)
    if new_details:
        with st.expander('Asset types'):
            for asset, key, addition in new_details:
                kinds = ['crypto','equity','etf','etc','unknown']
                _suggest_default(key+'kind', addition['instrument_type'])
                addition['instrument_type'] = _control(st.selectbox, 'Type · '+asset, kinds,
                    index=kinds.index(addition['instrument_type']), filter_mode=None, key=key+'kind')
    if asset := st.session_state.get(prefix+'search_asset'):
        key = prefix+digest(asset)[:12]+'_'
        with st.container(border=True):
            header, close = st.columns([8,1])
            header.markdown('**Find price listing · '+asset+'**')
            if close.button('Close', key=key+'close_search'):
                st.session_state.pop(prefix+'search_asset', None)
                st.rerun()
            widget_key = key+'live_search'
            state = st.session_state.get(widget_key, {})
            query = normalized_query(state.get('query', st.session_state.get(widget_key+'_query', asset)))
            st.session_state[widget_key+'_query'] = query
            results, error = searches([query], directory, demo)[query] if len(query) >= 2 else ([], '')
            choice = render_search_box(query, result_groups(results), error or 'Choose a listing to connect prices.', selected_listings.get(asset, ''), key=widget_key)
            if error and st.button('Retry search', key=key+'retry'):
                st.session_state['import_tx_listing_cache'].pop((query,demo),None)
                st.rerun()
            if choice and normalized_query(choice.get('query','')) == query:
                listing = next((x for x in results if x.ticker == choice.get('ticker')), None)
                if listing:
                    if not demo:
                        listing = InstrumentSearch(directory/'.cache'/'yahoo').details(listing)
                    custom.setdefault(asset,{})[listing.ticker] = listing
                    st.session_state[key+'pending_listing'] = listing.ticker
                    st.session_state.pop(prefix+'search_asset',None)
                    st.rerun()
    errors = [error for _, error in initial.values() if error]
    if errors:
        st.caption('Some live suggestions are unavailable. Use the search button to retry.')
    return mapping, additions, listing_changes
