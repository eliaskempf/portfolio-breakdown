"""Account journal browsing and explicit reviewed corrections."""
from dataclasses import asdict, replace
from datetime import date

import pandas as pd
import streamlit as st

from portfolio_app.interface.shared.list_ui import ListColumn, frame_rows, render_list
from portfolio_app.interface.views.positions.position_list import list_context
from portfolio_app.ledger.account_activity import (
    AMOUNT_KINDS, CONTRACT_KINDS, activity_rows, correct_entry, correction_route,
    entry_context, entry_label, related_entries,
)
from portfolio_app.portfolio.holdings import DataError


def _cancel(prefix):
    for key in list(st.session_state):
        if key.startswith(prefix):
            del st.session_state[key]
    st.session_state[prefix+'action'] = 'View entry'


def _return_to_list(key, prefix):
    _cancel(prefix)
    st.session_state.pop(key+'selected', None)


def _render_transactions(directory, journal, revision, key):
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
    if frame.empty:
        st.caption('No matching transactions')
        return
    frame = frame.drop(columns=['account_id', 'position_id'])
    columns = [ListColumn(name, name, numeric=name in {'Quantity', 'Price', 'Amount'}, display=name,
                          secondary='Note' if name == 'Action' else '')
               for name in frame if name not in {'entry_id', 'Note'}]

    def open_row(event):
        st.session_state[key+'selected'] = event['id']

    render_list(frame_rows(frame, id_column='entry_id'), columns, key=key+'list',
                context=list_context(directory)+'-transactions', revision=revision,
                default_sort='Date', title='Recorded transactions', on_open=open_row)


def render_activity(directory, journal, revision, *, key):
    if not journal.events:
        st.caption('No recorded transactions')
        return
    panel = st.expander('Recorded transactions', key=key+'panel', on_change='rerun')
    if not panel.open:
        return
    with panel:
        selected = st.session_state.get(key+'selected')
        event = next((e for e in journal.events if e.id == selected), None) if selected else None
        if event is None:
            _render_transactions(directory, journal, revision, key)
            return
        # Keep list filters while their widgets are detached in the detail view.
        for field in ('accounts', 'positions', 'actions', 'since', 'until'):
            if key+field in st.session_state:
                st.session_state[key+field] = st.session_state[key+field]
        accounts = {a.id: a.name for a in journal.accounts}
        prefix = key+'edit_'+selected+'_'
        st.button('Back to transactions', key=key+'back', type='tertiary', icon=':material/arrow_back:',
                  on_click=_return_to_list, args=(key, prefix))
        st.markdown('**Transaction details**')
        st.text(entry_label(journal, event))
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
            with st.popover('Correction unavailable'):
                st.write(route)
            return
        action = st.session_state.get(prefix+'action', 'View entry')
        if action == 'View entry':
            with st.container(horizontal=True):
                st.button('Correct entry', key=prefix+'correct', on_click=st.session_state.update,
                          args=({prefix+'action': 'Correct entry'},))
                st.button('Reverse only', key=prefix+'reverse', help='Review removal of this entry’s accounting effect.',
                          on_click=st.session_state.update, args=({prefix+'action': 'Reverse only'},))
            return
        def cancel_button():
            st.button('Cancel reversal' if action == 'Reverse only' else 'Cancel correction',
                      key=prefix+'cancel', on_click=_cancel, args=(prefix,))

        try:
            changes = {} if action == 'Reverse only' else _correction_fields(journal, event, prefix)
            candidate = correct_entry(journal, selected, changes, reverse=action == 'Reverse only')
            if action == 'Correct entry':
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
                    st.session_state.pop(prefix+'pending', None)
                    with st.container(horizontal=True):
                        st.button('Review correction', disabled=True, key=prefix+'unchanged', help='Change a recorded fact first.')
                        cancel_button()
                    return
                st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
            from portfolio_app.interface.views.derivatives.accounts_ui import _save_review
            _save_review(directory, journal, revision, candidate, prefix,
                         review_label='Review reversal' if action == 'Reverse only' else 'Review correction',
                         save_label='Save reversal' if action == 'Reverse only' else 'Save correction',
                         compare_balances=True, cancel_button=cancel_button, destructive=action == 'Reverse only')
        except (DataError, ValueError) as exc:
            st.session_state.pop(prefix+'pending', None)
            st.info(str(exc))
            cancel_button()
            related = related_entries(journal, event)
            if related:
                with st.expander('Later entries to review'):
                    for item in related:
                        st.write(entry_label(journal, item))


def _correction_fields(journal, event, key):
    from portfolio_app.interface.views.derivatives.accounts_ui import _money_input
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
