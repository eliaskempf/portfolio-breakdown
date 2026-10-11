"""Compact futures details and an explicit two-leg roll inside the position dialog."""
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pandas as pd
import streamlit as st

from portfolio_app import account_journal as ledger
from portfolio_app.accounts_ui import _event, _key, _save_review
from portfolio_app.currency_display import reporting_currency
from portfolio_app.futures_investments import investment_positions
from portfolio_app.holdings import DataError
from portfolio_app.money import format_money
from portfolio_app.presentation import performance_metric


def _history(state, members, valued):
    rows = []
    indexed = valued.set_index('position_id') if valued is not None else None
    for identity in members:
        balance = state.positions[identity]
        record = {'Contract': balance.contract.product, 'Maturity': balance.contract.expiry,
            'Contracts': balance.quantity, 'Status': 'Open' if balance.quantity else 'Closed',
            'Settled P&L': str(balance.realized_pnl), 'Fees': str(balance.fees),
            'Entered taxes': str(balance.taxes), 'Currency': balance.contract.currency}
        if indexed is not None and identity in indexed.index:
            row = indexed.loc[identity]
            record['Valuation price'] = row.get('mark_price')
            record['Price source'] = row.get('price_source', '')
        rows.append(record)
    return pd.DataFrame(rows)


def render_roll(directory, journal, revision, members, *, key):
    """User-entered executed fills; never infer a roll price or submit an order."""
    state = ledger.replay(journal)
    active = [identity for identity in members if state.positions[identity].quantity]
    st.markdown('**Roll contracts**')
    st.caption('Record the close of an existing contract and the opening of a new maturity together. '
        'Your investment, allocated capital and accumulated gains stay together. This records trades you have made.')
    if not active:
        st.info('There are no open contracts to roll.')
        return
    identity = (st.selectbox('Contract to roll', active,
        format_func=lambda p: f'{state.positions[p].contract.product} · {state.positions[p].contract.expiry} · {state.positions[p].quantity:+d} contracts',
        key=key+'position') if len(active) > 1 else active[0])
    balance = state.positions[identity]
    contract = balance.contract
    # A different source contract gets its own draft, including the correct maximum quantity.
    key += identity + '_'
    when, quantity = st.columns(2)
    day = when.date_input('Roll date', value=date.today(), max_value=date.today(), key=key+'date')
    count = quantity.number_input('Contracts to roll', min_value=1, max_value=abs(balance.quantity),
        value=abs(balance.quantity), step=1, key=key+'quantity')
    left, right = st.columns(2)
    with left:
        st.markdown('**1. Close the current contract**')
        st.caption(f'{contract.product} · {contract.expiry} · {contract.currency} {contract.multiplier} per point')
        closing_text = st.text_input('Closing execution price', key=key+'close_price')
    with right:
        st.markdown('**2. Open the new maturity**')
        expiry = st.date_input('New contract maturity', value=None, key=key+'expiry')
        opening_text = st.text_input('Opening execution price', key=key+'open_price')
    fees_text = st.text_input(f'Total roll fees ({contract.currency})', value='0', key=key+'fees')
    st.caption('The same number and direction of contracts opens in the new maturity. '
        'The difference between the two execution prices is not an immediate gain or loss.')
    value_day = day
    if st.checkbox('Cash is posted on a later date', key=key+'deferred'):
        value_day = st.date_input('Cash value date', value=day, min_value=day, key=key+'value_date')
    if not closing_text.strip() or not opening_text.strip() or expiry is None:
        st.caption('Enter both executed prices and the exact new maturity to review the roll.')
        return
    try:
        from portfolio_app.derivative_types import decimal
        closing = decimal(closing_text, 'Closing execution price')
        opening = decimal(opening_text, 'Opening execution price')
        fees = decimal(fees_text, 'Roll fees')
        if fees < 0:
            raise DataError('Roll fees must be nonnegative.')
        if expiry.isoformat() <= contract.expiry:
            raise DataError('Choose a new maturity later than the current contract.')
        next_contract = replace(contract, expiry=expiry.isoformat(), last_trade_date='', final_settlement_date='')
        next_id = st.session_state.setdefault(key+'next_'+expiry.isoformat(), uuid4().hex)
        group = st.session_state.setdefault(key+'group_'+expiry.isoformat(), uuid4().hex)
        candidate = ledger.add_contract(journal, next_contract)
        candidate = ledger.add_position(candidate, replace(balance.spec, id=next_id, contract_id=next_contract.id))
        signed = int(count) if balance.quantity > 0 else -int(count)
        base = dict(account_id=balance.spec.account_id, currency=contract.currency,
                    group_id=group, value_date=value_day.isoformat())
        close = _event(candidate, 'trade', day, position_id=identity, quantity=-signed, price=closing,
                       note='Roll: close current maturity', **base)
        new = _event(candidate, 'trade', day, position_id=next_id, quantity=signed, price=opening,
                     note='Roll: open new maturity', **base)
        candidate = ledger.roll(candidate, close=close, opening=replace(new, sequence=close.sequence+1))
        if fees:
            candidate = ledger.append_event(candidate, _event(candidate, 'fee', day,
                position_id=identity, amount=fees, note='Roll transaction fees', **base))
        st.caption(f'{count} contract(s) roll from {contract.expiry} to {expiry.isoformat()}; '
                   f'{abs(balance.quantity)-count} remain in the old maturity.')
        _save_review(directory, journal, revision, candidate, key,
                     review_label='Review roll', save_label='Save roll')
    except (DataError, ValueError) as exc:
        st.info(str(exc))


def render_investment_detail(directory, journal, revision, valued, identity, *, allocation=None, editing=False):
    """Rendered inside the same large Position details dialog as ordinary holdings."""
    state = ledger.replay(journal)
    investment = next((item for item in journal.investments if item.id == identity), None)
    if investment is None and identity in state.positions:
        owner = state.positions[identity].spec.investment_id
        investment = next((item for item in journal.investments if item.id == owner), None)
    members = [p.id for p in journal.positions if investment and p.investment_id == investment.id]
    if investment is None:
        members = [identity] if identity in state.positions else []
    if not members:
        st.warning('This futures position no longer exists.')
        return
    identity = investment.id if investment else identity
    key = _key(directory)+'detail_'+identity+'_'
    balances = [state.positions[p] for p in members]
    account = next(a.name for a in journal.accounts if a.id == balances[0].spec.account_id)
    names = {b.id:b.name for b in allocation.buckets} if allocation else {}
    category = names.get(balances[0].spec.bucket_id, 'Unassigned')
    st.subheader(investment.name if investment else f'{balances[0].contract.product} futures')
    st.caption(f'{account} · {category} · {balances[0].contract.currency} settlement')
    modes = ['Contract history', 'Roll contracts', 'Record transaction'] + (['Edit allocation'] if investment else [])
    if st.session_state.pop('position_edit_detail_requested', False) or key+'view' not in st.session_state:
        st.session_state[key+'view'] = 'Edit allocation' if editing and investment else 'Contract history'
    if valued is not None and st.session_state.get(key+'view') in (None, 'Contract history'):
        display = investment_positions(valued,journal) if investment else valued
        matches = display.loc[display.position_id.eq(identity)]
        if not matches.empty:
            row = matches.iloc[0]
            currency = reporting_currency()
            capital, gain, returns = st.columns(3)
            capital.metric('Capital value' if investment else 'Unsettled value', format_money(row.current_value_reporting,currency))
            with gain:
                performance_metric('Net gain since recorded start', format_money(row.unrealized_gain_reporting,currency,signed=True),
                    row.unrealized_gain_reporting,key=key+'gain')
            with returns:
                amount = row.return_pct
                performance_metric('Return on initial capital', f'{amount:+.2f}%' if pd.notna(amount) else '—',
                    amount,key=key+'return')
            exposure, contracts = st.columns(2)
            exposure.metric('Market exposure', format_money(row.signed_notional_reporting,currency),
                help='Signed current contract notional. This measures market exposure, not money paid for the contracts.')
            contracts.metric('Open contracts', f'{sum(b.quantity for b in balances):+d}',
                help='Signed contracts across the investment’s open maturities; positive is long and negative is short.')
            if investment:
                with st.expander('Capital and performance details'):
                    st.caption(row.performance_note)
                    st.write(f'Initial capital: {format_money(investment.reference_capital,investment.currency)}')
                    st.write(f'Net additional allocation: {format_money(investment.allocated_capital-investment.reference_capital,investment.currency,signed=True)}')
                    st.caption('Account cash attributed to this investment is counted once in portfolio value.')
    view = st.segmented_control('Futures position view', modes, key=key+'view',
        default=st.session_state[key+'view'], label_visibility='collapsed') or 'Contract history'
    if view == 'Roll contracts':
        render_roll(directory,journal,revision,members,key=key+'roll_')
    elif view == 'Record transaction':
        from portfolio_app.accounts_ui import render_transaction
        render_transaction(directory,journal,revision,position_ids=members,key_prefix=key+'transaction_')
    elif view == 'Edit allocation':
        from portfolio_app.investment_ui import _edit_investment
        _edit_investment(directory,journal,revision,investment,key,show_toggle=False)
    else:
        st.markdown('**Contract history**')
        history = _history(state,members,valued)
        columns = ['Contract', 'Maturity', 'Contracts', 'Status']
        if 'Valuation price' in history:
            columns.append('Valuation price')
        st.dataframe(history[columns],hide_index=True,width='stretch')
        with st.expander('Contract accounting and price sources'):
            st.dataframe(history.drop(columns=['Contracts', 'Status']),hide_index=True,width='stretch')
        st.caption('Closed maturities remain part of this investment. Choose Roll contracts to move all or part of an open position to its next maturity.')
        if not investment:
            st.caption('This contract has no capital allocation yet. Group its history as an investment in Accounts & futures to show allocated capital and cumulative performance.')
