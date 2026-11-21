"""Compact everyday buy/sell entry shared by new and existing positions."""
from datetime import date
from uuid import uuid4

import streamlit as st

from portfolio_app.interface.shared.currency_display import reporting_currency
from portfolio_app.ledger.account_journal import replay
from portfolio_app.ledger.security_history import Charge, SecurityEvent, duplicate_candidates
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.position_entry import decimal_input
from portfolio_app.portfolio.portfolio_settings import REPORTING_CURRENCIES


def trade_inputs(prefix, journal, *, kind='buy'):
    left, right = st.columns(2)
    stamp = left.date_input('Trade date', value=date.today(), max_value=date.today(), key=prefix+'trade_date')
    currency = right.selectbox('Trade currency', REPORTING_CURRENCIES, accept_new_options=True,
        index=REPORTING_CURRENCIES.index(reporting_currency()), key=prefix+'trade_currency').strip().upper()
    amounts = st.columns(4 if kind == 'sell' else 3)
    left, middle, right = amounts[:3]
    quantity = left.text_input('Quantity bought' if kind == 'buy' else 'Quantity sold', key=prefix+'trade_quantity')
    price = middle.text_input('Unit price', key=prefix+'trade_price')
    fees = right.text_input('Fees', value='0', key=prefix+'trade_fees',
        help='Total fees in the trade currency, entered separately from the unit price.')
    taxes = amounts[3].text_input('Taxes paid / withheld', value='0', key=prefix+'trade_taxes',
        help='Actual taxes deducted from this sale in the trade currency. Enter 0 if none. Recorded separately from fees.') if kind == 'sell' else '0'
    try:
        qty = decimal_input(quantity, 'Quantity')
        unit_price = decimal_input(price, 'Unit price')
        charge = decimal_input(fees, 'Fees')
        tax = decimal_input(taxes, 'Taxes paid / withheld')
        if qty <= 0:
            raise DataError('Enter a quantity greater than zero.')
        identity = st.session_state.setdefault(prefix+'trade_id', uuid4().hex)
        sequence = max([e.sequence for b in journal.securities for e in b.records]+[e.sequence for e in journal.events]+[0])+1
        # Keep explicit zero sale charges so either amount can be corrected later.
        event = SecurityEvent(identity, kind, stamp.isoformat(), quantity=qty, price=unit_price,
            currency=currency, charges=tuple(Charge(amount, currency, kind=charge_kind)
                for amount, charge_kind in ((charge, 'fee'), (tax, 'tax')) if amount or kind == 'sell'), charges_known=True,
            sequence=sequence)
        total = event.gross + charge if kind == 'buy' else event.gross - charge - tax
        st.caption(f'{"Total paid" if kind == "buy" else "Net proceeds"}: {total:,.2f} {currency}')
        return event, None
    except (DataError, ValueError) as exc:
        return None, str(exc)


def render_trade(directory, snapshot, journal, revision, position_id, prefix, *, kind='buy', on_saved=None):
    from portfolio_app.ledger.history_commands import book_for
    from portfolio_app.ledger.trade_commands import prepare_trade, save_trade
    row = snapshot.holdings.loc[snapshot.holdings.position_id.eq(position_id)].iloc[0]
    book = book_for(journal, row)
    expected = st.session_state.setdefault(prefix+'trade_revisions', (snapshot.revision, revision))
    if expected != (snapshot.revision, revision):
        st.warning('History or holdings changed. Close and reopen this form before saving.')
        return
    event, error = trade_inputs(prefix, journal, kind=kind)
    opening_date = ''
    if not book.active:
        confirmed = row.get('holdings_confirmed_on', '')
        if confirmed:
            opening_date = confirmed
            st.caption(f'Existing holding: {row.shares:g} units as of {confirmed}')
        else:
            stamp = st.date_input('Holding as of', value=None, max_value=date.today(), key=prefix+'opening_date',
                help='Date of the existing total, before this trade. The trade adds to or subtracts from that total. Earlier history stays unknown.')
            opening_date = stamp.isoformat() if stamp else ''
    accept_duplicate = False
    if event:
        from dataclasses import replace
        from portfolio_app.ledger.security_history import append_evidence
        candidate = append_evidence(book, (event,))
        candidate = replace(candidate, active_ids=(*book.active_ids, event.id))
        if any(event.id in pair for pair in duplicate_candidates(candidate)):
            accept_duplicate = st.checkbox('This is a separate trade', key=prefix+'duplicate')
        try:
            proposed = prepare_trade(journal, snapshot.holdings, position_id, event,
                opening_date=opening_date, accept_duplicate=accept_duplicate)
            if book.account_id:
                before, after = replay(journal), replay(proposed)
                for key in sorted(set(before.cash) | set(after.cash)):
                    if before.cash.get(key) != after.cash.get(key):
                        value = after.cash.get(key)
                        st.caption(f'Linked cash ({key[1]}): {"Unknown" if key in after.unknown_cash else f"{value:,.2f}"}')
        except (DataError, ValueError) as exc:
            error = str(exc)
    if error and event:
        st.caption(error)
    if st.button('Save purchase' if kind == 'buy' else 'Save sale', type='primary', key=prefix+'save_trade'):
        if error:
            st.error(error)
        else:
            try:
                save_trade(directory, event, position_id=position_id,
                    expected_holdings_revision=expected[0], expected_journal_revision=expected[1],
                    opening_date=opening_date, accept_duplicate=accept_duplicate)
            except (DataError, OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                for key in list(st.session_state):
                    if key.startswith(prefix):
                        del st.session_state[key]
                if on_saved:
                    on_saved()
                st.rerun()
