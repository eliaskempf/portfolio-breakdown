"""Cash accounts in the shared position dialog."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import streamlit as st

from portfolio_app.derivatives.derivative_types import CURRENCIES
from portfolio_app.interface.shared.money import format_money
from portfolio_app.ledger import account_journal as ledger
from portfolio_app.ledger.bank_accounts import bank_opening, cash_statement, save_bank_account
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.strategic import category_labels
from portfolio_app.workspace.storage import revision


def _category(config, current, prefix):
    categories = category_labels(config) if config else {}
    matches = [b.id for b in config.buckets if b.name.strip().casefold() == 'cash'] if config else []
    create = ('new', 'Cash')
    choices = {'': 'Unassigned', **categories}
    if not matches:
        choices[create] = 'Create “Cash” category'
    default = matches[0] if len(matches) == 1 else ''
    if current is not None:
        default = current if current in categories else ''
    selected = st.selectbox('Category', list(choices), index=list(choices).index(default),
                            format_func=choices.get, key=prefix+'category')
    return ('' if selected == create else selected), selected == create


def render_bank_form(directory, snapshot, config, valued, *, account=None):
    from portfolio_app.interface.views.positions.position_ui import _clear_editor
    journal, stamp = ledger.load_journal(directory)
    prefix = 'position_edit_fields_bank_' + (account.id if account else 'new') + '_'
    expected = st.session_state.setdefault(prefix+'expected', (
        snapshot.revision, stamp, revision(directory/'allocation.yaml')))
    name = st.text_input('Account name', value=account.name if account else '', key=prefix+'name')
    kinds = ['Checking', 'Savings', 'Term deposit']
    kind = st.selectbox('Account type', kinds, index=kinds.index(account.cash_type) if account else 0,
                        key=prefix+'type')
    maturity = None
    if kind == 'Term deposit':
        maturity = st.date_input('Maturity date (optional)', value=date.fromisoformat(account.maturity)
                                 if account and account.maturity else None, key=prefix+'maturity')
    if config is None:
        from portfolio_app.portfolio.allocation import migration_preview
        config, _ = migration_preview(snapshot.holdings)
        if config.buckets:
            st.caption('Selecting a category saves the existing sleeve groups as categories. Member targets can be set in Rebalance.')
    category, create = _category(config, account.bucket_id if account else None, prefix)
    source, opening = '', None
    event = bank_opening(journal, account) if account else None
    if account:
        unit = account.denomination
    else:
        from portfolio_app.interface.shared.currency_display import reporting_currency
        unit = st.selectbox('Account currency', CURRENCIES, index=CURRENCIES.index(reporting_currency()),
                            key=prefix+'currency')
    day = st.date_input('Opening balance date', value=date.fromisoformat(event.date) if event else date.today(),
                        min_value=date.min, max_value=date.today(), key=prefix+'day',
                        help='Portfolio performance includes this cash balance from this date.')
    if not account:
        used = {link.position_id for link in (*journal.cash_links, *journal.holding_links)}
        if valued is not None and 'quote_currency' in valued and 'instrument_type' in valued:
            rows = valued.loc[valued.position_id.isin(set(snapshot.holdings.position_id) - used)
                              & valued.instrument_type.eq('cash') & valued.quote_currency.eq(unit)
                              & valued.current_price.notna()]
            if not rows.empty:
                names = dict(zip(rows.position_id, rows['name']))
                source = st.selectbox('Opening balance source', ['', *names], format_func=lambda identity:
                                      'Enter balance' if not identity else f'Adopt {names[identity]}',
                                      key=prefix+'source_'+unit)
                if source:
                    row = rows.set_index('position_id').loc[source]
                    opening = Decimal(str(row.shares)) * Decimal(str(row.current_price))
                    st.metric('Opening balance', format_money(opening, unit))
    adopted = account and any(link.account_id == account.id for link in journal.cash_links)
    if adopted and event:
        st.metric('Opening balance', format_money(event.amount, unit), help='Taken from the adopted cash holding.')
    elif not source:
        opening = st.number_input(f'Opening balance ({unit})', value=float(event.amount) if event else None,
                                  step=0.01, key=prefix+'opening',
                                  help='Existing cash on this date. Opening balances do not generate income.')
    with st.container(horizontal=True):
        save = st.button('Save cash account', type='primary')
        cancel = st.button('Cancel')
    if cancel:
        _clear_editor()
        st.rerun()
    if save:
        try:
            if not adopted and opening is None:
                raise DataError('Enter an opening balance, including zero.')
            candidate = ledger.Account(account.id if account else uuid4().hex, name.strip(), 'bank',
                                       kind, unit, category, maturity.isoformat() if maturity else '')
            save_bank_account(directory, candidate, opening=opening, day=day.isoformat() if day else None,
                              adopt=source, create_category=create, holdings_revision=expected[0],
                              journal_revision=expected[1], allocation_revision=expected[2])
        except (DataError, OSError) as exc:
            st.error(str(exc))
            return
        selected = st.session_state.get('position_edit_selected')
        _clear_editor()
        st.session_state['onboarding_step'] = 'done'
        if account:
            from portfolio_app.interface.views.positions.position_ui import request_position
            request_position(selected, local=False)
        else:
            st.session_state['position_saved_notice'] = f'Saved {candidate.name}.'
        st.rerun()


def render_bank_detail(directory, snapshot, config, valued, account, *, editing=False):
    if valued is not None and 'bucket_id' in valued:
        selected = valued.loc[valued.position_id.eq(st.session_state.get('position_edit_selected'))]
        if not selected.empty:
            account = replace(account, bucket_id=selected.iloc[0].bucket_id)
    if editing:
        render_bank_form(directory, snapshot, config, valued, account=account)
        return
    journal, stamp = ledger.load_journal(directory)
    from portfolio_app.interface.shared.input_cache import cached_input
    state = cached_input('bank_detail_state', (str(directory), stamp), lambda: ledger.replay(journal))
    key = 'position_edit_bank_' + account.id + '_'
    unit = account.denomination
    unknown = (account.id, unit) in state.unknown_cash
    missing_opening = unknown and bank_opening(journal, account) is None
    balance = state.cash.get((account.id, unit), Decimal(0))
    st.metric('Cash balance', 'Unknown' if unknown else format_money(balance, unit))
    label = (category_labels(config) if config else {}).get(account.bucket_id, 'Unassigned')
    st.caption(' · '.join(filter(None, [account.cash_type, unit, label,
                           'Matures ' + account.maturity if account.maturity else ''])))
    selected = st.session_state.get(key+'selected')
    if selected:
        event = next((e for e in journal.events if e.id == selected), None)
        if event:
            from portfolio_app.interface.views.history.account_activity_ui import render_transaction_detail
            render_transaction_detail(directory, journal, stamp, event, key=key, return_on_save=True)
        else:
            st.caption('Review this posting in the linked security’s transaction history.')
            st.button('Back to transactions', on_click=st.session_state.pop, args=(key+'selected', None))
        return
    if missing_opening:
        st.info('Enter the opening balance in Edit cash account.')
    if st.session_state.get(key+'record') and not missing_opening:
        _transaction(directory, journal, stamp, account, state, key)
        return
    with st.container(horizontal=True):
        if st.button('Record transaction', key=key+'add', disabled=missing_opening):
            st.session_state[key+'record'] = True
            st.rerun()
        if st.button('Edit cash account', key=key+'edit'):
            st.session_state['position_edit_action'] = 'Edit position'
            st.rerun()
    claims = [p for p in state.pending.values() if p.account_id == account.id]
    if claims:
        replacements = {e.replaces: e.id for e in journal.events if e.replaces}
        for claim in claims:
            identity = claim.id
            while identity in replacements:
                identity = replacements[identity]
            st.button(f'Pending {format_money(claim.amount, claim.currency)} · due {claim.due_date}',
                      key=key+'claim_'+claim.id, type='tertiary', on_click=st.session_state.update,
                      args=({key+'selected': identity},))
    if missing_opening:
        return
    rows = cash_statement(state, journal, account)
    if rows:
        from portfolio_app.interface.shared.list_ui import ListColumn, frame_rows, render_list
        from portfolio_app.interface.views.positions.position_list import list_context
        columns = [ListColumn('Date', 'Date'), ListColumn('Transaction', 'Transaction', secondary='Note'),
                   ListColumn('Amount', f'Amount ({unit})', numeric=True, signed=True),
                   ListColumn('Balance', f'Balance ({unit})', numeric=True)]
        def open_transaction(row):
            st.session_state[key+'selected'] = row['id']
        render_list(frame_rows(pd.DataFrame(rows), id_column='entry_id'), columns, key=key+'statement',
                    title='Cash account statement', context=list_context(directory)+key, revision=stamp,
                    default_sort='Date', on_open=open_transaction)


def _transaction(directory, journal, stamp, account, state, key):
    prefix = key+'transaction_'
    expected = st.session_state.setdefault(prefix+'revision', stamp)
    options = ['Deposit', 'Withdrawal', 'Transfer', 'Interest', 'Fee', 'Tax']
    claims = {p.id: p for p in state.pending.values() if p.account_id == account.id}
    if claims:
        options.append('Receive pending cash')
    action = st.selectbox('Transaction', options, key=prefix+'kind')
    day = st.date_input('Transaction date', max_value=date.today(), key=prefix+'day')
    destinations = {a.id: a.name for a in journal.accounts if a.id != account.id
                    and (not a.cash_type or a.denomination == account.denomination)}
    fields = {}
    amount = None
    if action == 'Receive pending cash':
        fields['reference'] = st.selectbox('Pending payment', list(claims), format_func=lambda i:
            f'{claims[i].due_date} · {format_money(claims[i].amount, claims[i].currency)}', key=prefix+'claim')
    else:
        amount = st.number_input(f'Amount ({account.denomination})', step=0.01, value=None,
                                 key=prefix+'amount', help='Use a negative amount for interest charged.' if action == 'Interest' else None)
        if action == 'Transfer':
            fields['other_account_id'] = st.selectbox('Destination account', list(destinations), format_func=destinations.get,
                                                      key=prefix+'destination')
            if not destinations:
                st.caption('Add another account in this currency to record a transfer.')
    note = st.text_input('Description (optional)', key=prefix+'note')
    kinds = {'Deposit': 'cash_flow', 'Withdrawal': 'cash_flow', 'Transfer': 'transfer',
             'Interest': 'interest', 'Fee': 'fee', 'Tax': 'tax', 'Receive pending cash': 'receive'}
    candidate, error = None, ''
    try:
        if expected != stamp:
            raise DataError('The journal changed. Cancel and reopen the transaction to review the current balance.')
        if action != 'Receive pending cash' and (not amount or (amount < 0 and action != 'Interest')):
            raise DataError('Enter a nonzero interest amount.' if action == 'Interest' else 'Enter a positive amount.')
        if action == 'Transfer' and not fields['other_account_id']:
            raise DataError('Choose a destination account.')
        event = ledger.Event(uuid4().hex, day.isoformat(), kinds[action], account.id, account.denomination,
                             -amount if action == 'Withdrawal' else amount or 0, note=note,
                             sequence=max((e.sequence for e in journal.events), default=0)+1, **fields)
        candidate = replace(journal, events=(*journal.events, event))
        cash_key = (account.id, account.denomination)
        delta = -event.amount if action in ('Fee', 'Tax', 'Transfer') else event.amount
        if action == 'Receive pending cash':
            delta = claims[event.reference].amount
        result = 'Unknown' if cash_key in state.unknown_cash else format_money(state.cash[cash_key] + delta, account.denomination)
        st.caption(f'Balance after posting · {result}')
    except DataError as exc:
        candidate, error = None, str(exc)
    with st.container(horizontal=True):
        save = st.button('Save transaction', type='primary', key=prefix+'save')
        cancel = st.button('Cancel', key=prefix+'cancel')
    if save:
        if error:
            st.error(error)
            return
        try:
            ledger.save_journal(directory, candidate, expected)
        except (DataError, OSError) as exc:
            st.error(str(exc))
            return
    if cancel or save:
        for field in list(st.session_state):
            if field.startswith(prefix):
                del st.session_state[field]
        st.session_state.pop(key+'record', None)
        st.rerun()
