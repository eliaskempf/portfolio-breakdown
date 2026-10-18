"""Manual exposure metadata for held funds; price-return histories stay untouched."""
from dataclasses import replace
from datetime import date
from hashlib import sha256

import pandas as pd
import streamlit as st

from portfolio_app.holdings import DataError
from portfolio_app.leverage_settings import FundFactor, load_leverage_settings, save_leverage_settings
from portfolio_app.positions import read_snapshot
from portfolio_app.workspace_lock import workspace_lock


def update_fund_factor(directory, position_id, factor, *, settings_revision, holdings_revision, freeze_keys=None):
    """Save one selected fund without discarding other factors, debt or policies.

    Stable identities must be frozen before writing references. The injected
    function is for synthetic tests; the application uses its shared identity
    service. Its import is lazy so read-only rendering has no ledger dependency.
    """
    with workspace_lock(directory):
        settings, current_revision = load_leverage_settings(directory)
        snapshot = read_snapshot(directory / 'holdings.csv')
        if current_revision != settings_revision or snapshot.revision != holdings_revision:
            raise DataError('Holdings or fund settings changed. Reload before saving.')
        if factor is not None:
            selected = snapshot.holdings.loc[snapshot.holdings.position_id.eq(position_id)]
            if len(selected) != 1 or selected.iloc[0].get('instrument_type') != 'etf':
                raise DataError('Select a currently held ETF position.')
            if factor.position_id != position_id:
                raise DataError('Factor belongs to a different position.')
            if factor.proxy_position_id:
                proxy = snapshot.holdings.loc[snapshot.holdings.position_id.eq(factor.proxy_position_id)]
                factors = {x.position_id: x for x in settings.fund_factors}
                if len(proxy) != 1 or proxy.iloc[0].get('instrument_type') != 'etf' or factor.proxy_position_id in factors:
                    raise DataError('Choose a separate ETF without an embedded-leverage factor as reference proxy.')
            if freeze_keys is None:
                from portfolio_app.actual_portfolio import freeze_position_keys
                freeze_keys = freeze_position_keys
            freeze_keys(directory, holdings_revision)
        updated = tuple(x for x in settings.fund_factors if x.position_id != position_id)
        if factor is not None:
            updated += (factor,)
        save_leverage_settings(directory, replace(settings, fund_factors=updated,
                               enabled=True if updated else settings.enabled), settings_revision)


def render_leveraged_funds(directory, valued):
    st.subheader('Leveraged funds')
    with st.expander('About exposure factors'):
        st.write('Record a dated signed exposure factor for a held ETF. For example, 2 means twice its current value; '
                 '−1 means inverse exposure. The ETF retains its actual market value and observed returns. '
                 'A daily reset factor does not promise the same multiple over several days.')
    try:
        settings, revision = load_leverage_settings(directory)
        snapshot = read_snapshot(directory / 'holdings.csv')
    except (DataError, OSError) as exc:
        st.error(str(exc))
        return
    funds = valued.loc[valued.get('instrument_type', pd.Series('', index=valued.index)).eq('etf')].copy()
    funds = funds.loc[funds.position_id.isin(snapshot.holdings.position_id)] if 'position_id' in funds else funds
    records = {x.position_id: x for x in settings.fund_factors}
    names = {row['position_id']: row['name'] + (f" · {row['account']}" if row.get('account') else '') for row in funds.to_dict('records')}
    unavailable = {pid: value for pid, value in records.items() if pid not in names}
    if unavailable:
        with st.expander('Unavailable saved funds', expanded=True):
            st.caption('These saved factors no longer match a held ETF. They are retained until explicitly removed.')
            for index, (pid, value) in enumerate(unavailable.items(), 1):
                st.write(f"Saved fund {index} · {value.benchmark or 'No benchmark recorded'} · {value.as_of or 'Undated'} · "
                         + ('Unknown factor' if value.factor is None else f'{value.factor:g}×'))
                with st.expander(f'Saved fund {index} provenance'):
                    st.json(value.__dict__)
                if st.button(f'Remove unavailable fund {index}', key='leveraged_funds_remove_'+pid):
                    try:
                        update_fund_factor(directory, pid, None, settings_revision=revision, holdings_revision=snapshot.revision)
                    except (DataError, OSError) as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
    if funds.empty:
        st.caption('Add an ETF to Holdings to record its exposure factor.')
        return
    key = 'leveraged_funds_' + sha256(str(directory.resolve()).encode()).hexdigest()[:12]
    position_id = st.selectbox('ETF position', list(names), format_func=names.get, key=key + '_position')
    existing = records.get(position_id)
    form_key = key + '_' + position_id + '_' + str(revision)
    with st.form(form_key):
        known = st.radio('Exposure factor', ['Known', 'Unknown'], index=0 if existing is None or existing.factor is not None else 1)
        number = st.text_input('Signed factor', value=str(existing.factor) if existing and existing.factor is not None else '',
                               placeholder='e.g. 2 or -1')
        stamp = st.date_input('Factor as of', value=date.fromisoformat(existing.as_of) if existing and existing.as_of else date.today(), max_value=date.today())
        method = st.text_input('Source or method', value=existing.method if existing else '',
                               help='Issuer factsheet or your documented estimate. A known factor requires a source.')
        benchmark = st.text_input('Reference benchmark (optional)', value=existing.benchmark if existing else '')
        reset = st.selectbox('Reset convention', ['daily', 'other', 'unknown'],
                             index=['daily', 'other', 'unknown'].index(existing.reset_convention if existing else 'daily'))
        proxy_options = [''] + [pid for pid in names if pid != position_id and pid not in records]
        proxy = existing.proxy_position_id if existing else ''
        if proxy and proxy not in proxy_options:
            proxy_options.append(proxy)
        proxy_id = st.selectbox('Unlevered reference ETF (optional)', proxy_options, index=proxy_options.index(proxy),
                                format_func=lambda value: names.get(value, 'Unavailable saved proxy · choose a held ETF') if value else 'None')
        st.caption('A reference ETF documents the benchmark approximation. Its holdings are not treated as this fund’s owned basket. '
                   'Unknown means exposure remains incomplete; it is not assumed to be 1×.')
        save = st.form_submit_button('Save fund factor')
        remove = st.form_submit_button('Remove recorded factor', disabled=existing is None)
    if save or remove:
        try:
            factor = None if remove else FundFactor(position_id, float(number) if known == 'Known' else None,
                method.strip(), stamp.isoformat(), benchmark.strip(), proxy_id, reset)
            update_fund_factor(directory, position_id, factor, settings_revision=revision,
                               holdings_revision=snapshot.revision)
        except (DataError, ValueError, OSError) as exc:
            st.error(str(exc))
        else:
            st.rerun()
