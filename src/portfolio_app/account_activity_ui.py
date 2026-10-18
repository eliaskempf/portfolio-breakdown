"""Account journal browsing and explicit reviewed corrections."""
from dataclasses import asdict, replace
from datetime import date

import pandas as pd
import streamlit as st

from portfolio_app.account_activity import (
    AMOUNT_KINDS, CONTRACT_KINDS, activity_rows, correct_entry, correction_route,
    entry_context, entry_label, related_entries,
)
from portfolio_app.holdings import DataError


def _cancel(prefix):
    for key in list(st.session_state):
        if key.startswith(prefix):
            del st.session_state[key]
    st.session_state[prefix+'action'] = 'View entry'


def render_activity(directory, journal, revision, *, key):
    if not journal.events:
        st.caption('No recorded transactions.')
        return
    with st.expander('Recorded transactions'):
        frame = pd.DataFrame(activity_rows(journal))
        with st.popover('Filter transactions'):
            accounts = {a.id: a.name for a in journal.accounts}
            account = st.multiselect('Accounts', list(accounts), format_func=accounts.get, key=key+'accounts')
            names = {row['position_id']: row['Investment / contract'] for row in frame.to_dict('records') if row['position_id']}
            positions = st.multiselect('Investments / contracts', list(names), format_func=names.get, key=key+'positions')
            actions = st.multiselect('Transaction actions', sorted(frame.Action.unique()), key=key+'actions')
            start, end = st.columns(2)
            since = start.date_input('From date', value=None, key=key+'since')
            until = end.date_input('Through date', value=None, key=key+'until')
        if account:
            frame = frame.loc[frame.account_id.isin(account)]
        if positions:
            frame = frame.loc[frame.position_id.isin(positions)]
        if actions:
            frame = frame.loc[frame.Action.isin(actions)]
        if since:
            frame = frame.loc[frame.Date.ge(since.isoformat())]
        if until:
            frame = frame.loc[frame.Date.le(until.isoformat())]
        st.dataframe(frame.drop(columns=['entry_id', 'account_id', 'position_id']).fillna('—'), hide_index=True, width='stretch')
        if frame.empty:
            st.caption('No transactions match these filters.')
            return
        events = {e.id: e for e in journal.events}
        selected = st.selectbox('Transaction details', frame.entry_id.tolist(),
            format_func=lambda value: entry_label(journal, events[value]), key=key+'selected')
        event = events[selected]
        st.caption(entry_label(journal, event))
        links = [e for e in journal.events if e.id == event.replaces or e.replaces == event.id]
        for linked in links:
            st.caption(('Original: ' if linked.id == event.replaces else 'Reversal: ' if linked.kind == 'void' else 'Replacement: ')
                       + entry_label(journal, linked))
        if event.kind == 'fx':
            st.caption(f'Received amount: {event.other_amount} {event.other_currency}')
        if event.other_account_id:
            st.caption('Destination: ' + accounts[event.other_account_id])
        with st.expander('Entry provenance'):
            st.json({k: str(v) if v is not None else None for k, v in asdict(event).items()})
        if route := correction_route(journal, event):
            st.caption(route)
            return
        prefix = key+'edit_'+selected+'_'
        action = st.selectbox('Entry action', ['View entry', 'Correct entry', 'Reverse only'], key=prefix+'action')
        if action == 'View entry':
            st.session_state.pop(prefix+'pending', None)
            return
        st.button('Cancel correction', key=prefix+'cancel', on_click=_cancel, args=(prefix,))
        try:
            changes = {} if action == 'Reverse only' else _correction_fields(journal, event, prefix)
            candidate = correct_entry(journal, selected, changes, reverse=action == 'Reverse only')
            if action == 'Reverse only':
                st.caption('Removes this entry’s accounting effect while keeping its recorded history.')
            else:
                replacement = candidate.events[-1]
                before, after = asdict(event), asdict(replacement)
                labels = {'date': 'Date', 'value_date': 'Cash value date', 'amount': 'Amount', 'price': 'Price',
                          'quantity': 'Quantity', 'note': 'Note', 'currency': 'Currency',
                          'other_currency': 'Received currency', 'other_amount': 'Received amount'}
                rows = [{'Field': label, 'Original': str(before[field]), 'Replacement': str(after[field])}
                        for field, label in labels.items() if before[field] != after[field]]
                for field, label in [('account_id', 'Account'), ('other_account_id', 'Destination account')]:
                    if before[field] != after[field]:
                        rows.append({'Field': label, 'Original': accounts.get(before[field], ''), 'Replacement': accounts.get(after[field], '')})
                if before['position_id'] != after['position_id']:
                    rows.append({'Field': 'Contract', 'Original': entry_context(journal, event)[3],
                                 'Replacement': entry_context(journal, replacement)[3]})
                if not rows:
                    st.caption('Change a recorded fact to review a correction.')
                    return
                st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
                st.caption('Saving replaces the original’s accounting effect in one write. Both records remain in history.')
            from portfolio_app.accounts_ui import _save_review
            _save_review(directory, journal, revision, candidate, prefix,
                         review_label='Review reversal' if action == 'Reverse only' else 'Review correction',
                         save_label='Save reversal' if action == 'Reverse only' else 'Save correction',
                         compare_balances=True)
        except (DataError, ValueError) as exc:
            st.info(str(exc))
            related = related_entries(journal, event)
            if related:
                with st.expander('Later entries to review'):
                    for item in related:
                        st.write(entry_label(journal, item))


def _correction_fields(journal, event, key):
    from portfolio_app.accounts_ui import _money_input
    changes = {}
    left, right = st.columns(2)
    changes['date'] = left.date_input('Corrected date', value=date.fromisoformat(event.date),
                                    max_value=date.today(), key=key+'date').isoformat()
    if event.kind in CONTRACT_KINDS | {'fee', 'tax', 'interest', 'transfer', 'fx'}:
        day = right.date_input('Corrected cash value date',
            value=date.fromisoformat(event.value_date) if event.value_date else None, key=key+'value_date',
            help='Blank posts on the event date. A later date creates a pending claim requiring a recorded receipt.')
        changes['value_date'] = day.isoformat() if day else ''
    accounts = {a.id: a.name for a in journal.accounts}
    account, _, currency, _ = entry_context(journal, event)
    # Receipts retain their claim; contract facts retain their declared account.
    if event.position_id:
        choices = [p.id for p in journal.positions if p.account_id == account]
        changes['position_id'] = st.selectbox('Corrected contract', choices, index=choices.index(event.position_id),
            format_func=lambda pid: entry_context(journal, replace(event, position_id=pid))[3], key=key+'position')
        currency = next(c.currency for c in journal.contracts if c.id == next(p.contract_id for p in journal.positions if p.id == changes['position_id']))
        changes['currency'] = currency if event.kind in CONTRACT_KINDS else st.text_input(
            'Corrected currency', value=event.currency or currency, key=key+'currency').strip().upper()
    elif event.kind != 'receive':
        changes['account_id'] = st.selectbox('Corrected account', list(accounts), index=list(accounts).index(account),
                                           format_func=accounts.get, key=key+'account')
        changes['currency'] = st.text_input('Corrected currency', value=currency, key=key+'currency').strip().upper()
    if event.kind in {'snapshot', 'trade'}:
        changes['quantity'] = int(st.number_input('Corrected signed contracts', value=event.quantity, step=1, key=key+'quantity'))
    if event.kind in CONTRACT_KINDS:
        changes['price'] = _money_input('Corrected price', key+'price', str(event.price))
    if event.kind in AMOUNT_KINDS:
        changes['amount'] = _money_input('Corrected amount', key+'amount', str(event.amount))
    if event.kind in {'transfer', 'fx'}:
        destination = event.other_account_id or account
        changes['other_account_id'] = st.selectbox('Corrected destination account', list(accounts), index=list(accounts).index(destination),
                                                 format_func=accounts.get, key=key+'destination')
    if event.kind == 'fx':
        changes['other_currency'] = st.text_input('Corrected received currency', value=event.other_currency, key=key+'other_currency').strip().upper()
        changes['other_amount'] = _money_input('Corrected received amount', key+'other_amount', str(event.other_amount))
    changes['note'] = st.text_input('Correction note', value=event.note, key=key+'note')
    return changes
