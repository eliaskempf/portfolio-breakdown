"""Persistent futures investment presentation over the native account journal."""
from dataclasses import replace
from uuid import uuid4

import pandas as pd
import streamlit as st

from portfolio_app.holdings import DataError
from portfolio_app.money import format_money


def investment_review_rows(before, after):
    """Describe allocation and membership changes alongside reviewed postings."""
    previous = {item.id: item for item in before.investments}
    records = []
    for item in after.investments:
        old = previous.get(item.id)
        old_positions = {p.id for p in before.positions if p.investment_id == item.id}
        positions = {p.id for p in after.positions if p.investment_id == item.id}
        if old == item and old_positions == positions:
            continue
        records.append({'Investment': item.name, 'Currency': item.currency,
            'Initial capital': str(item.reference_capital),
            'Previous allocated principal': str(old.allocated_capital) if old else 'New investment',
            'Allocated principal after change': str(item.allocated_capital),
            'Contract records': len(positions), 'Cash posting': 'None; existing cash is attributed'})
    return records


def _preferred_position(journal, investment_id):
    from portfolio_app.account_journal import replay
    state = replay(journal)
    positions = [p.id for p in journal.positions if p.investment_id == investment_id]
    return next((identity for identity in reversed(positions) if state.positions[identity].quantity),
                positions[-1] if positions else None)


def route_investment_request(journal, identity, key):
    """Open the logical investment and its latest active contract together."""
    if identity not in {item.id for item in journal.investments}:
        return False
    st.session_state[key+'investment'] = identity
    position = _preferred_position(journal, identity)
    if position:
        st.session_state[key+'transaction_position'] = position
    return True


def sync_contract_investment(journal, key):
    """Keep investment details in step with an explicitly selected contract."""
    identity = st.session_state.get(key+'transaction_position')
    position = next((item for item in journal.positions if item.id == identity), None)
    if position is not None and position.investment_id:
        st.session_state[key+'investment'] = position.investment_id
        st.session_state[key+'previous_investment'] = position.investment_id


def _investment_history(journal, investment):
    from portfolio_app.account_journal import replay
    state = replay(journal)
    rows = []
    for position in journal.positions:
        if position.investment_id != investment.id:
            continue
        balance = state.positions[position.id]
        rows.append({'Product': balance.contract.product, 'Maturity': balance.contract.expiry,
            'Contracts': balance.quantity, 'Status': 'Open' if balance.quantity else 'Closed',
            'Settled trading P&L': str(balance.realized_pnl), 'Fees': str(balance.fees),
            'Entered taxes': str(balance.taxes), 'Currency': balance.contract.currency})
    st.markdown('**Contract history**')
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def _edit_investment(directory, journal, revision, investment, key, *, show_toggle=True):
    from portfolio_app.accounts_ui import _money_input, _save_review
    from portfolio_app.futures_investments import assign_investment
    edit_key = key + 'edit_' + investment.id + '_'
    if show_toggle and not st.toggle('Edit investment allocation', key=edit_key+'open'):
        return
    name = st.text_input('Investment name', value=investment.name, key=edit_key+'name')
    capital = _money_input(f'Allocated principal ({investment.currency})', edit_key+'capital', str(investment.allocated_capital))
    st.caption('Enter total contributed principal, excluding investment gains or losses. Changing this attribution moves capital between the investment and unallocated account cash; it does not post a deposit or withdrawal. Initial capital remains fixed.')
    try:
        candidate = assign_investment(journal, replace(investment, name=name.strip(), allocated_capital=capital),
                                      [p.id for p in journal.positions if p.investment_id == investment.id])
        _save_review(directory, journal, revision, candidate, edit_key,
                     review_label='Review investment changes', save_label='Save investment changes')
    except (DataError, ValueError) as exc:
        st.info(str(exc))


def _create_investment(directory, journal, revision, key):
    from portfolio_app.account_journal import InvestmentSpec
    from portfolio_app.accounts_ui import _money_input, _save_review
    from portfolio_app.futures_investments import assign_investment, roll_position_ids
    positions = [p for p in journal.positions if not p.investment_id]
    if not positions:
        return
    create_key = key + 'create_investment_'
    if not st.toggle('Group an existing contract history as an investment', key=create_key+'open'):
        return
    contracts = {c.id: c for c in journal.contracts}
    lookup = {p.id: p for p in positions}
    identity = st.selectbox('Contract history to group', list(lookup),
        format_func=lambda p: f'{contracts[lookup[p].contract_id].product} {contracts[lookup[p].contract_id].expiry}',
        key=create_key+'position')
    position = lookup[identity]
    contract = contracts[position.contract_id]
    name = st.text_input('Investment name', value=f'{contract.product} futures', key=create_key+'name')
    capital = _money_input(f'Initial allocated capital at recorded start ({contract.currency})', create_key+'capital')
    st.caption('Use the capital allocated at the start of the recorded contract history, before its recorded profits, losses and fees. Linked rolls are included automatically. For a snapshot, earlier performance remains unknown.')
    try:
        identifiers = roll_position_ids(journal, identity)
        investment_id = st.session_state.setdefault(create_key+'id_' + identity, 'investment:' + uuid4().hex)
        investment = InvestmentSpec(investment_id, name.strip(), position.account_id, contract.currency, capital, capital)
        candidate = assign_investment(journal, investment, identifiers)
        _save_review(directory, journal, revision, candidate, create_key,
                     review_label='Review investment changes', save_label='Save investment changes')
    except (DataError, ValueError) as exc:
        st.info(str(exc))


def render_investments(directory, journal, revision, valued, *, key):
    """Show attributed capital and lifetime recorded performance, then exact history."""
    from portfolio_app.currency_display import reporting_currency
    from portfolio_app.futures_investments import investment_positions
    if journal.investments:
        names = {item.id: item.name for item in journal.investments}
        identity = st.selectbox('Futures investment', list(names), format_func=names.get, key=key+'investment')
        if st.session_state.get(key+'previous_investment') != identity:
            position = _preferred_position(journal, identity)
            if position:
                st.session_state[key+'transaction_position'] = position
            st.session_state[key+'previous_investment'] = identity
        investment = next(item for item in journal.investments if item.id == identity)
        if valued is not None:
            projected = investment_positions(valued, journal)
            rows = projected.loc[projected.position_id.eq(identity)]
            if not rows.empty:
                row = rows.iloc[0]
                reporting = reporting_currency()
                capital, gain, exposure = st.columns(3)
                capital.metric('Investment capital value', format_money(row.current_value_reporting, reporting))
                gain.metric('Net P&L since recorded start', format_money(row.unrealized_gain_reporting, reporting, signed=True))
                exposure.metric('Signed contract exposure', format_money(row.signed_notional_reporting, reporting))
                st.caption(f'Initial capital {format_money(investment.reference_capital, investment.currency)} · '
                    f'Net funding {format_money(investment.allocated_capital-investment.reference_capital, investment.currency, signed=True)}. '
                    'Capital includes recorded net gains and losses. The attributed cash is counted once.')
                if not row.get('history_complete', True):
                    st.caption('History begins with a position snapshot. Earlier gains, losses and costs remain unknown.')
                if reporting != investment.currency:
                    st.caption('Native capital and recorded P&L are translated at current FX. Historical funding FX performance is not reconstructed.')
        _investment_history(journal, investment)
        _edit_investment(directory, journal, revision, investment, key)
    _create_investment(directory, journal, revision, key)
