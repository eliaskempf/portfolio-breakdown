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
from portfolio_app.investment_history import pnl_breakdown, settlement_history
from portfolio_app.money import format_money
from portfolio_app.presentation import performance_metric


def _history(state, members, valued):
    rows = []
    indexed = valued.set_index('position_id') if valued is not None else None
    for identity in members:
        balance = state.positions[identity]
        record = {'Contract': balance.contract.product, 'Maturity': balance.contract.expiry,
            'Contracts': balance.quantity, 'Status': 'Open' if balance.quantity else 'Closed',
            'Settled trading P&L': str(balance.realized_pnl), 'Fees': str(balance.fees),
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
    st.caption('Record an executed close and opening in a new maturity together.')
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
        from portfolio_app.contract_maturity_ui import maturity_input
        expiry = maturity_input(contract.product if contract.exchange == 'EUREX' else None,
            label='New contract maturity', key=key+'expiry_', after=contract.expiry)
        opening_text = st.text_input('Opening execution price', key=key+'open_price')
    fees_text = st.text_input(f'Total roll fees ({contract.currency})', value='0', key=key+'fees')
    with st.expander('How rolling is recorded'):
        st.caption('The same quantity and direction opens in the new maturity. The expiry price gap is not an immediate gain or loss. '
            'Your investment, allocated capital and accumulated P&L remain together.')
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
    views = ['Chart', 'Settlements', 'Contract history']
    if st.session_state.pop('position_edit_detail_requested', False):
        st.session_state[key+'action'] = 'Edit allocation' if editing and investment else None
        st.session_state[key+'selected_view'] = 'Chart'
        st.session_state[key+'view'] = 'Chart'
    if st.session_state.get(key+'view') not in views:
        st.session_state[key+'view'] = st.session_state.get(key+'selected_view', 'Chart')
    with st.container(horizontal=True):
        st.button('Edit position', icon=':material/edit:', key=key+'edit_position',
            help='Edit the investment name and allocated principal.',
            on_click=st.session_state.__setitem__, args=(key+'action', 'Edit allocation' if investment else 'Record transaction'))
        st.button('Record transaction', key=key+'record',
            on_click=st.session_state.__setitem__, args=(key+'action', 'Record transaction'))
        st.button('Roll contracts', key=key+'roll', disabled=not any(b.quantity for b in balances),
            on_click=st.session_state.__setitem__, args=(key+'action', 'Roll contracts'))
    st.subheader(investment.name if investment else f'{balances[0].contract.product} futures')
    active = [b for b in balances if b.quantity]
    contract_label = (f'{active[0].quantity:+d} {active[0].contract.product} · {active[0].contract.expiry}'
                      if len(active) == 1 else f'{len(active)} open maturities' if active else 'Closed')
    st.caption(f'{contract_label} · {account}')
    action = st.session_state.get(key+'action')
    if action:
        st.button('Back to position', icon=':material/arrow_back:', key=key+'back',
            on_click=st.session_state.__setitem__, args=(key+'action', None))
        if action == 'Roll contracts':
            render_roll(directory,journal,revision,members,key=key+'roll_')
        elif action == 'Retrieve settlements':
            from portfolio_app.settlement_backfill_ui import render_settlement_backfill
            render_settlement_backfill(directory,journal,revision,members,key=key+'backfill_')
        elif action == 'Record transaction':
            from portfolio_app.accounts_ui import render_transaction
            render_transaction(directory,journal,revision,position_ids=members,key_prefix=key+'transaction_')
        elif action == 'Edit allocation' and investment:
            from portfolio_app.investment_ui import _edit_investment
            _edit_investment(directory,journal,revision,investment,key,show_toggle=False)
        return
    row = None
    currency = reporting_currency()
    if valued is not None:
        display = investment_positions(valued,journal) if investment else valued
        matches = display.loc[display.position_id.eq(identity)]
        if not matches.empty:
            row = matches.iloc[0]
            from portfolio_app.freshness_ui import render_freshness
            realized, unrealized = pnl_breakdown(journal, members, valued)
            def render_metrics(current):
                row = current.iloc[0]
                capital, gain, exposure = st.columns(3)
                capital.metric('Capital value' if investment else 'Unsettled value', format_money(row.current_value_reporting,currency),
                    help=row.get('valuation_detail', ''))
                if row.get('valuation_label', ''):
                    capital.caption(row.valuation_label, help=row.get('valuation_detail', ''))
                with gain:
                    performance_metric('Net P&L', format_money(row.unrealized_gain_reporting,currency,signed=True),
                        row.unrealized_gain_reporting,key=key+'gain', qualified=row.get('valuation_qualified', False),
                        help=row.get('valuation_detail', ''))
                    st.caption(f'Unrealized {format_money(unrealized,currency,signed=True)} · '
                               f'Realized {format_money(realized,currency,signed=True)}')
                exposure.metric('Market exposure', format_money(row.signed_notional_reporting,currency),
                    help='Signed contract notional, separate from allocated capital.')
            render_freshness(matches, render_metrics)
    view = st.segmented_control('Futures position view', views, key=key+'view',
        default=st.session_state[key+'view'], label_visibility='collapsed') or 'Chart'
    st.session_state[key+'selected_view'] = view
    if view == 'Chart':
        from portfolio_app.futures_chart_ui import render_futures_chart
        render_futures_chart(directory, [balance.contract for balance in balances], key=key+'chart_')
    elif view == 'Settlements':
        from portfolio_app.settlement_archive_ui import render_history_checks
        render_history_checks(directory,journal,members,key=key+'archive_')
        st.button('Retrieve settlements', icon=':material/download:', key=key+'retrieve_settlements',
            help='Retrieve missing daily prices for this exact contract and review their cash postings.',
            on_click=st.session_state.__setitem__, args=(key+'action', 'Retrieve settlements'))
        history = settlement_history(journal, members)
        if history.empty:
            st.info('No settlements recorded yet.')
        else:
            columns = ['Date', 'Contract', 'Maturity', 'Contracts', 'Settlement price', 'Settlement P&L', 'Currency', 'Cash status']
            st.dataframe(history[columns],hide_index=True,width='stretch')
            with st.expander('Posting details'):
                st.dataframe(history,hide_index=True,width='stretch')
                st.caption('Effective recorded daily and final settlements. Cash posts only when recorded; '
                    'a value date alone does not post a receipt. Corrections retain the original journal facts. '
                    'Closing-trade P&L and downloaded price observations are separate from this settlement history.')
    else:
        history = _history(state,members,valued)
        columns = ['Contract', 'Maturity', 'Contracts', 'Status']
        if 'Valuation price' in history:
            columns.append('Valuation price')
        st.dataframe(history[columns],hide_index=True,width='stretch')
        with st.expander('Contract accounting and price sources'):
            st.dataframe(history.drop(columns=['Contracts', 'Status']),hide_index=True,width='stretch')
        from portfolio_app.history_coverage_ui import render_account_coverage_review
        render_account_coverage_review(directory, journal, key=key+'coverage_',
            account_id=balances[0].spec.account_id)
    with st.expander('Position details'):
        st.caption(f'{category} · {balances[0].contract.currency} settlement')
        if row is not None:
            left, right = st.columns(2)
            left.metric('Realized P&L', format_money(realized,currency,signed=True))
            right.metric('Unrealized P&L', format_money(unrealized,currency,signed=True))
            st.caption('Realized: closed quantities, matched FIFO. Unrealized: open quantities since entry, '
                'including daily settlements. Both exclude costs; pre-snapshot performance is unknown.')
            st.write('Fees, interest & entered taxes: '+format_money(
                row.unrealized_gain_reporting-realized-unrealized,currency,signed=True))
            amount = row.return_pct
            st.write('Return on initial capital: '+(f'{amount:+.2f}%' if pd.notna(amount) else '—'))
            if investment:
                st.write(f'Initial capital: {format_money(investment.reference_capital,investment.currency)}')
                st.write(f'Net additional allocation: {format_money(investment.allocated_capital-investment.reference_capital,investment.currency,signed=True)}')
                st.caption(row.performance_note)
                st.caption('Attributed account cash is counted once in portfolio value.')
        if not investment:
            st.caption('Group this contract history as an investment in Accounts & futures to assign capital and cumulative performance.')
