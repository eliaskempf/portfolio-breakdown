"""Manual source discovery and normalized CSV fallback for held funds."""
from datetime import date
from hashlib import sha256

import streamlit as st

from portfolio_app.etf_setup import prepare_draft, save_draft


def render_setup(data_dir, holdings, funds, *, demo=False):
    rows = {str(row['id']): row for row in holdings.to_dict('records')
            if row.get('shares', 0) > 0 and (row.get('instrument_type', '') not in {'equity', 'crypto', 'cash', 'physical', 'bond', 'etc'})}
    if not rows:
        return
    with st.expander('Set up a breakdown'):
        asset = st.selectbox('Fund position', list(rows), format_func=lambda key: rows[key]['name'], key='etf_setup_asset')
        row = rows[asset]
        mode = st.radio('Breakdown source', ['Official product page', 'Normalized holdings CSV'], key='etf_setup_mode')
        product_url, content, stamp, kind = '', None, date.today(), 'equity'
        identifier = dict(row)
        if mode == 'Official product page':
            product_url = st.text_input('Official iShares, Xtrackers, Amundi, Vanguard or State Street/SPDR product URL', key='etf_setup_url')
        else:
            st.caption('Physical holdings only. Required columns: constituent_id, name, ticker, isin, weight. '
                       'Weights are whole-fund fractions. Optional fields: instrument_type, issuer, country, market_currency, maturity (YYYY-MM-DD), credit_rating.')
            identifier['isin'] = st.text_input('Fund ISIN', value=row.get('isin', ''), key=f'etf_setup_isin_{asset}').strip().upper()
            if row.get('isin') and identifier['isin'] != row['isin']:
                st.warning('The snapshot ISIN must match this position.')
                return
            upload = st.file_uploader('Normalized constituent CSV', type=['csv'], key='etf_setup_csv')
            content = upload.getvalue() if upload else None
            stamp = st.date_input('Holdings date', max_value=date.today(), key='etf_setup_date')
            kind = st.selectbox('Physical fund asset class', ['equity', 'fixed_income', 'money_market'], key='etf_setup_class')
        token = sha256(repr((str(data_dir.resolve()), identifier, mode, product_url, content, stamp, kind)).encode()).hexdigest()
        if st.button('Preview breakdown', disabled=demo or (not product_url if mode == 'Official product page' else content is None)):
            try:
                with st.spinner('Reading and validating fund holdings…'):
                    draft = prepare_draft(data_dir, identifier, product_url=product_url, content=content, as_of=stamp, asset_class=kind)
                st.session_state['etf_setup_preview'] = (token, draft)
            except Exception as exc:
                st.warning(str(exc))
        preview = st.session_state.get('etf_setup_preview')
        if preview and preview[0] == token:
            draft = preview[1]
            source = draft.source
            st.markdown(f'**{draft.fund.name} · {draft.fund.isin}**')
            st.caption(f'{draft.stamp} · {len(draft.frame):,} components · {draft.frame.weight.sum():.2%} represented · '
                       f"{source.breakdown_basis if source else 'physical holdings'}")
            st.caption(draft.notes)
            st.dataframe(draft.frame, hide_index=True, height=280)
            # A supplied URL cannot silently bind a different WKN or ticker-only position.
            effective_wkn = source.wkn if source else draft.fund.wkn
            linked = bool(row.get('isin') == draft.fund.isin or row.get('wkn') and row['wkn'] == effective_wkn
                          or row.get('ticker') and source and row['ticker'] in source.tickers)
            if not linked:
                st.info('Connect this position to the matching ISIN in Positions → Connect live prices. Saving a snapshot does not change position identity or pricing.')
            if st.button('Save breakdown', disabled=demo):
                try:
                    save_draft(data_dir, draft)
                    del st.session_state['etf_setup_preview']
                    st.rerun()
                except Exception as exc:
                    st.warning(str(exc))
