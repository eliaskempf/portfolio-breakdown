"""Named legacy EUR liabilities, with one-record revision-checked saves."""
from dataclasses import replace
from uuid import uuid4

import pandas as pd
import streamlit as st

from portfolio_app.holdings import DataError
from portfolio_app.leverage_settings import MarginLiability, load_leverage_settings, save_leverage_settings


def update_liability(directory, identity, liability, revision, *, future=None):
    from portfolio_app.account_journal import load_journal
    from portfolio_app.workspace_lock import workspace_lock
    with workspace_lock(directory):
        settings, current = load_leverage_settings(directory, future=future)
        if current != revision:
            raise DataError('Liabilities changed. Reload before saving.')
        journal, _ = load_journal(directory)
        if identity in journal.adopted_liability_ids:
            raise DataError('This liability is managed under Futures → Cash & financing.')
        if liability is not None and liability.id != identity:
            raise DataError('The liability identity must remain unchanged.')
        updated = tuple(item for item in settings.liabilities if item.id != identity)
        if liability is not None:
            updated += (liability,)
        save_leverage_settings(directory, replace(settings, liabilities=updated,
                               enabled=True if updated else settings.enabled), revision)


def render_liabilities(directory, *, future=None):
    from portfolio_app.account_journal import load_journal
    settings, revision = load_leverage_settings(directory, future=future)
    journal, _ = load_journal(directory)
    adopted = set(journal.adopted_liability_ids)
    st.markdown('**Recorded liabilities**')
    if settings.liabilities:
        st.dataframe(pd.DataFrame([{'Liability': item.name, 'Account': item.account,
            'Balance (EUR)': 'Unknown' if item.balance_eur is None else f'{item.balance_eur:,.2f}',
            'Managed in': 'Cash & financing' if item.id in adopted else 'Liabilities'} for item in settings.liabilities]), hide_index=True)
    records = {item.id: item for item in settings.liabilities}
    key = 'exposure_liability_'
    selected = st.selectbox('Liability', ['', *records], format_func=lambda value:
        'Add liability' if not value else records[value].name + (f' · {records[value].account}' if records[value].account else ''), key=key+'selected')
    if selected in adopted:
        st.caption('Already adopted into the account journal. Update its loan under Futures → Cash & financing.')
        return
    existing = records.get(selected)
    form_key = key+selected+'_'+str(revision)
    with st.form(form_key):
        name = st.text_input('Liability name', value=existing.name if existing else '')
        account = st.text_input('Account or lender', value=existing.account if existing else '')
        balance = st.text_input('Outstanding balance (EUR)', value=str(existing.balance_eur) if existing and existing.balance_eur is not None else '',
            help='Blank means unknown. Enter 0 for a known zero balance. Record debt once, separately from owned assets.')
        save = st.form_submit_button('Save liability')
        confirm = st.checkbox('Remove this liability', disabled=existing is None)
        remove = st.form_submit_button('Remove liability', disabled=existing is None)
    if save or remove:
        try:
            if remove and not confirm:
                raise DataError('Select Remove this liability before removing it.')
            identity = selected or uuid4().hex
            liability = None if remove else MarginLiability(identity, name.strip(), float(balance) if balance.strip() else None, account.strip())
            update_liability(directory, identity, liability, revision, future=future)
        except (DataError, OSError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.rerun()
