"""Manual collateral ownership and requirement coverage for actual accounts."""
from dataclasses import asdict, replace
from datetime import date
from hashlib import sha256
from uuid import uuid4

import pandas as pd
import streamlit as st

from portfolio_app.account_journal import Event, MarginRequirement, replay, save_journal
from portfolio_app.account_margin import (
    adopt_liability, bind_holding, margin_coverage, reserve_collateral, validate_reservations,
)
from portfolio_app.derivative_types import CURRENCIES, decimal
from portfolio_app.holdings import DataError
from portfolio_app.money import format_money


def _amount(label, key, default='0'):
    return decimal(st.text_input(label, value=default, key=key), label)


def _review(directory, original, revision, candidate, holdings_revision, key):
    if st.button('Review collateral changes', key=key+'review'):
        replay(candidate)
        st.session_state[key+'pending'] = candidate, revision, holdings_revision
    pending = st.session_state.get(key+'pending')
    if pending is None:
        return
    proposed, expected, expected_holdings = pending
    st.markdown('**Reviewed account changes**')
    changed = [asdict(item) for item in proposed.events if item not in original.events]
    changed += [dict(Action='Account holding binding', **asdict(item))
                for item in proposed.holding_links if item not in original.holding_links]
    changed += [dict(Action='Margin requirement', **asdict(item))
                for item in proposed.margin_requirements if item not in original.margin_requirements]
    st.dataframe(pd.DataFrame(changed).astype(str), hide_index=True)
    st.caption('Save records the reviewed changes. Review again after changing inputs.')
    if st.button('Save reviewed collateral changes', type='primary', key=key+'save'):
        try:
            from portfolio_app.actual_portfolio import freeze_position_keys, validate_links
            from portfolio_app.workspace_lock import workspace_lock
            with workspace_lock(directory):
                snapshot = freeze_position_keys(directory, expected_holdings)
                validate_links(proposed, snapshot.holdings)
                validate_reservations(proposed, snapshot.holdings)
                save_journal(directory, proposed, expected)
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            st.session_state.pop(key+'pending', None)
            st.session_state['accounts_notice'] = 'Collateral and account requirements saved.'
            st.rerun()


def render_account_margin(directory, journal, revision, holdings, valued, reporting_currency, fx_rates=None):
    """Display coverage without predicting broker margin calls or execution."""
    from portfolio_app.positions import read_snapshot
    key = 'margin_'+sha256(str(directory.resolve()).encode()).hexdigest()[:10]+'_'
    if st.session_state.get(key+'revision') != revision:
        for stored in list(st.session_state):
            if stored.startswith(key) and stored.endswith('pending'):
                st.session_state.pop(stored, None)
        st.session_state[key+'revision'] = revision
    if not journal.accounts:
        st.info('Create an account in Cash & financing before recording collateral.')
        return
    snapshot = read_snapshot(directory/'holdings.csv')
    if not snapshot.holdings.equals(holdings):
        st.info('Holdings changed. Reload before changing account bindings or collateral.')
        return
    if fx_rates is None:
        from portfolio_app.account_observations import load_observations
        _, fx_rates, _ = load_observations(directory)
    names = {account.id: account.name for account in journal.accounts}
    account = st.selectbox('Collateral account', list(names), format_func=names.get, key=key+'account')
    denomination = st.selectbox('Coverage currency', CURRENCIES, index=CURRENCIES.index(reporting_currency), key=key+'currency')
    st.caption('Coverage compares explicitly pledged assets after entered haircuts with your recorded requirements. '
               'It is not broker buying power. Foreign collateral does not pay settlement-currency cash obligations automatically.')
    try:
        coverage = margin_coverage(journal, holdings, valued, account, denomination,
                                   reporting_currency=reporting_currency, fx_rates=fx_rates)
        for issue in coverage.issues:
            st.warning(issue)
        columns = st.columns(3)
        for column, label, amount in zip(columns,
            ('Eligible collateral', 'Initial requirement headroom', 'Maintenance requirement headroom'),
            (coverage.eligible_collateral, coverage.initial_headroom, coverage.maintenance_headroom)):
            column.metric(label, format_money(float(amount), denomination) if amount is not None else '—')
        if coverage.initial_requirement is None:
            st.caption('Headroom needs complete FX and a recorded margin requirement.')
        liquidity = pd.DataFrame([{'Currency': denomination, 'Settled cash': str(coverage.settled_cash),
            'Cash less reservations': str(coverage.unreserved_cash), 'Pending receipts / payments': str(coverage.pending_cash),
            'Explicit loans': str(coverage.loans)}])
        st.dataframe(liquidity, hide_index=True)
        st.caption('Pending cash is excluded from collateral. Loans remain separate recorded liabilities; '
                   'entered requirements must reflect the obligations you intend to compare.')
        if coverage.collateral:
            st.dataframe(pd.DataFrame([{'Reservation': row.id, 'Holding': row.holding_id or 'Cash',
                'Quantity / native cash': str(row.reserved), 'Haircut (%)': float(row.haircut*100),
                f'Eligible ({denomination})': float(row.eligible_value) if row.eligible_value is not None else None,
                'Status': row.issue or 'Known'} for row in coverage.collateral]), hide_index=True)
    except DataError as exc:
        st.warning(str(exc))
    requirements = [item for item in journal.margin_requirements if item.account_id == account]
    if requirements:
        st.dataframe(pd.DataFrame([asdict(item) for item in requirements]).astype(str), hide_index=True)
    action = st.selectbox('Collateral action', ('Reserve collateral', 'Release reservation', 'Link holding to account',
        'Record margin requirement', 'Adopt existing liability'), key=key+'action')
    action_key = key+account+'_'+str(('Reserve collateral', 'Release reservation', 'Link holding to account',
        'Record margin requirement', 'Adopt existing liability').index(action))+'_'
    day = st.date_input('Collateral / requirement date', max_value=date.today(), key=action_key+'date')
    try:
        candidate = journal
        if action == 'Adopt existing liability':
            from portfolio_app.leverage_settings import load_leverage_settings
            settings, _ = load_leverage_settings(directory)
            available = {item.id: item for item in settings.liabilities if item.id not in journal.adopted_liability_ids}
            if not available:
                st.info('No unadopted legacy liabilities remain.')
                return
            selected = st.selectbox('Existing liability', list(available), format_func=lambda x: available[x].name, key=action_key+'legacy')
            st.caption('Adoption records its existing EUR debt without adding cash. The original settings remain preserved and are excluded from duplicate totals.')
            candidate = adopt_liability(journal, available[selected], account, day.isoformat(), uuid4().hex)
        elif action == 'Record margin requirement':
            unit = st.selectbox('Requirement currency', CURRENCIES, key=action_key+'unit')
            identity = f'account-total:{account}:{unit}'
            existing = next((r for r in journal.margin_requirements if r.id == identity), None)
            initial = _amount('Initial margin requirement', action_key+unit+'initial', str(existing.initial) if existing else '0')
            maintenance = _amount('Maintenance margin requirement', action_key+unit+'maintenance', str(existing.maintenance) if existing else '0')
            source = st.text_input('Requirement source / note', value='Manual account requirement', key=action_key+'source')
            requirement = MarginRequirement(identity, account, unit, initial, maintenance, day.isoformat(), source)
            candidate = replace(journal, margin_requirements=tuple(item for item in journal.margin_requirements if item.id != identity)+(requirement,))
            st.caption('This saves the account total for the selected currency, replacing the total previously entered here.')
        elif action == 'Release reservation':
            reservations = {item.id: item for item in replay(journal).reservations.values() if item.account_id == account}
            if not reservations:
                st.info('No reservations to release in this account.')
                return
            selected = st.selectbox('Reservation to release', list(reservations), key=action_key+'release')
            candidate = reserve_collateral(journal, Event(uuid4().hex, day.isoformat(), 'release',
                account_id=account, collateral_id=selected,
                sequence=max((e.sequence for e in journal.events), default=0)+1), holdings)
        else:
            security = action == 'Link holding to account' or st.radio('Collateral asset', ('Cash', 'Security / asset'), key=action_key+'asset') == 'Security / asset'
            holding_id = ''
            unit = denomination
            if security:
                rows = {row['position_id']: row for row in holdings.to_dict('records')}
                bound = {item.position_id: item.account_id for item in journal.holding_links}
                adopted_cash = {item.position_id for item in journal.cash_links}
                eligible = [pid for pid, row in rows.items() if row.get('instrument_type') != 'cash'
                    and pid not in adopted_cash and (pid not in bound or bound[pid] == account)]
                if not eligible:
                    st.info('No unassigned or account-owned security holdings are available.')
                    return
                holding_id = st.selectbox('Collateral holding', eligible, format_func=lambda x: rows[x]['name'], key=action_key+'holding')
                candidate = bind_holding(journal, holdings, holding_id, account)
            if action != 'Link holding to account':
                if security:
                    amount = _amount('Shares / units to reserve', action_key+'quantity')
                else:
                    unit = st.selectbox('Cash collateral currency', CURRENCIES, key=action_key+'cash_currency')
                    amount = _amount('Cash amount to reserve', action_key+'amount')
                haircut = _amount('Collateral haircut (%)', action_key+'haircut')/100
                if amount <= 0:
                    raise DataError('Enter a positive collateral amount or quantity.')
                candidate = reserve_collateral(candidate, Event(uuid4().hex, day.isoformat(), 'reserve',
                    account_id=account, currency=unit, amount=amount, holding_id=holding_id, haircut=haircut,
                    collateral_id=uuid4().hex, sequence=max((e.sequence for e in journal.events), default=0)+1), holdings)
        _review(directory, journal, revision, candidate, snapshot.revision, action_key)
    except (DataError, ValueError) as exc:
        st.info(str(exc))
