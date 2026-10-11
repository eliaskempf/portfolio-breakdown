"""Manual actual-account workflows. All posting arithmetic belongs to the journal."""
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import streamlit as st

from portfolio_app.holdings import DataError
from portfolio_app.money import format_money


def _imports():
    from portfolio_app import account_journal as ledger
    return ledger


def _key(directory):
    from hashlib import sha256
    return 'accounts_' + sha256(str(directory.resolve()).encode()).hexdigest()[:10] + '_'


def _event(journal, kind, day, **fields):
    ledger = _imports()
    return ledger.Event(id=uuid4().hex, date=day.isoformat() if isinstance(day, date) else day,
                        kind=kind, sequence=max((e.sequence for e in journal.events), default=0) + 1, **fields)


def _money_input(label, key, value='0'):
    from portfolio_app.derivative_types import decimal
    return decimal(st.text_input(label, value=value, key=key), label)


def _save_review(directory, journal, revision, candidate, key, *, holdings_revision=None,
                 review_label='Review changes', save_label='Save reviewed changes'):
    """Require explicit review; preserve the exact reviewed candidate on reruns."""
    ledger = _imports()
    import json
    raw = asdict(candidate)
    raw['events'] = [{k:v for k,v in asdict(e).items() if k != 'id'} for e in candidate.events]
    signature = json.dumps(raw, default=str, sort_keys=True)
    if st.button(review_label, key=key + 'review'):
        state = ledger.replay(candidate)
        st.session_state[key + 'pending'] = (candidate, revision, holdings_revision, signature)
    pending = st.session_state.get(key + 'pending')
    if pending is None:
        return
    proposed, expected, held_revision, reviewed_signature = pending
    if reviewed_signature != signature or revision != expected:
        st.session_state.pop(key + 'pending', None)
        st.caption('Inputs changed. Review the updated entries before saving.')
        return
    st.markdown('**Reviewed changes**')
    events = proposed.events[len(journal.events):]
    if events:
        st.dataframe(pd.DataFrame([dict(Date=e.date, Action=e.kind, Currency=e.currency,
            Amount=str(e.amount), Quantity=e.quantity, Price=str(e.price or ''), Note=e.note) for e in events]), hide_index=True)
    from portfolio_app.investment_ui import investment_review_rows
    investment_changes = investment_review_rows(journal, proposed)
    if investment_changes:
        st.dataframe(pd.DataFrame(investment_changes), hide_index=True)
    state = ledger.replay(proposed)
    st.dataframe(pd.DataFrame([{'Account': a, 'Currency': c, 'Cash after posting': str(v)} for (a,c),v in state.cash.items()]), hide_index=True)
    st.caption('Save posts the reviewed entries above. Review again to include changed inputs.')
    if st.button(save_label, type='primary', key=key + 'save'):
        try:
            from portfolio_app.workspace_lock import workspace_lock
            from portfolio_app.actual_portfolio import freeze_position_keys, validate_links
            from portfolio_app.positions import read_snapshot
            with workspace_lock(directory):
                if held_revision is not None:
                    snapshot = freeze_position_keys(directory, held_revision)
                else:
                    snapshot = read_snapshot(directory / 'holdings.csv')
                validate_links(proposed, snapshot.holdings)
                ledger.save_journal(directory, proposed, expected)
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            st.session_state.pop(key + 'pending', None)
            st.session_state.pop(key + 'revision', None)
            if key.endswith('entry_'):
                for field in list(st.session_state):
                    if field.startswith(key):
                        del st.session_state[field]
            st.session_state['accounts_notice'] = 'Account changes saved.'
            st.session_state['position_edit_dialog'] = False
            st.rerun()


def _account_choice(directory, journal, snapshot, valued, *, key, day):
    """Return journal candidate and account ID; explicitly adopt or add cash."""
    ledger = _imports()
    names = {a.id: a.name for a in journal.accounts}
    selected = st.selectbox('Account', ['', *names], format_func=lambda x: names.get(x, 'Create account'), key=key+'account')
    candidate = journal
    if selected:
        return candidate, selected
    name = st.text_input('Account name', key=key+'account_name')
    currency = st.selectbox('Opening cash currency', ['EUR','USD','GBP'], key=key+'cash_currency')
    linked = {x.position_id for x in journal.cash_links}
    cash_rows = valued.loc[valued.get('instrument_type', pd.Series('', index=valued.index)).eq('cash') & ~valued.position_id.isin(linked)]
    cash_rows = cash_rows.loc[cash_rows.quote_currency.eq(currency)] if 'quote_currency' in cash_rows else cash_rows.iloc[:0]
    options = {'': 'Enter cash not already included in holdings', **{r.position_id: f'Adopt existing cash: {r["name"]}' for _,r in cash_rows.iterrows()}}
    source = st.selectbox('Cash source', list(options), format_func=options.get, key=key+'cash_source')
    if source:
        existing = cash_rows.loc[cash_rows.position_id.eq(source)].iloc[0]
        amount = Decimal(str(existing.shares)) * Decimal(str(existing.current_price))
        st.caption(f'The existing cash contribution will be replaced, preserving its balance of {amount} {currency}.')
    else:
        amount = _money_input('Cash balance (cash only, not account net liquidation value)', key+'cash')
        st.caption('Use zero if this account has no recorded cash. Do not enter cash already present elsewhere in the portfolio.')
    if not name.strip():
        raise DataError('Enter an account name to continue.')
    account_id = st.session_state.setdefault(key+'new_account_id', uuid4().hex)
    candidate = ledger.add_account(candidate, ledger.Account(account_id, name.strip()))
    candidate = ledger.append_event(candidate, _event(candidate, 'cash_open', day, account_id=account_id, currency=currency, amount=amount))
    if source:
        candidate = replace(candidate, cash_links=(*candidate.cash_links, ledger.CashLink(account_id, currency, source)))
    return candidate, account_id


def render_future_entry(directory, snapshot, *, valued=None, allocation=None):
    ledger = _imports()
    from portfolio_app.derivative_types import ContractSpec
    from portfolio_app.derivative_market import CATALOG, make_contract
    journal, revision = ledger.load_journal(directory)
    key = _key(directory) + 'entry_'
    valued = snapshot.holdings if valued is None else valued
    st.caption('Record an actual position. A futures contract adds exposure; its notional is not deducted from cash.')
    mode = st.radio('Record', ['New opening trade', 'Already-open position snapshot'], horizontal=True, key=key+'mode')
    day = st.date_input('Trade / snapshot date', value=date.today(), max_value=date.today(), key=key+'date')
    product = st.selectbox('Futures product', [*CATALOG, 'Manual contract'], key=key+'product')
    expiry = st.date_input('Exact contract maturity', value=date(date.today().year + 1, 3, 19), key=key+'expiry')
    try:
        if product == 'Manual contract':
            code = st.text_input('Product code', key=key+'code')
            currency = st.selectbox('Settlement currency', ['EUR','USD','GBP'], key=key+'currency')
            multiplier = _money_input('Currency per index point', key+'multiplier', '')
            tick = _money_input('Price tick', key+'tick', '0.01')
            contract = ContractSpec(code, expiry.isoformat(), currency, multiplier, tick, source='Manual specification')
        else:
            contract = make_contract(product, expiry.isoformat())
            st.caption(f'{contract.currency} {contract.multiplier} per point · exact maturity {contract.expiry}')
        quantity = st.number_input('Signed contracts (negative for short)', value=1, step=1, key=key+'quantity')
        price = _money_input('Fill price' if mode == 'New opening trade' else 'Last settled price / clearing basis', key+'price', '')
        fees = _money_input('Opening fees' if mode == 'New opening trade' else 'New fees to record (exclude costs already in opening cash)', key+'fees')
        candidate, account = _account_choice(directory, journal, snapshot, valued, key=key, day=day)
        investment_name = st.text_input('Investment name', value=f'{contract.product} futures', key=key+'investment_name')
        capital = _money_input(f'Initial allocated capital ({contract.currency})', key+'investment_capital')
        st.caption('Attribute part of the account cash to this investment. This is not a new cash deposit or the contract notional. Enter zero to allocate capital later.')
        leaves = allocation.leaves() if allocation else set()
        labels = {b.id: b.name for b in allocation.buckets} if allocation else {}
        bucket = st.selectbox('Category', ['', *sorted(leaves)], format_func=lambda x: labels.get(x, 'Unassigned'), key=key+'bucket')
        proxies = snapshot.holdings.loc[snapshot.holdings.get('instrument_type', pd.Series('', index=snapshot.holdings.index)).eq('etf')]
        names = {r.position_id: r['name'] for _,r in proxies.iterrows()}
        proxy = st.selectbox('Index holdings proxy (optional)', ['', *names], format_func=lambda x: names.get(x, 'No proxy'), key=key+'proxy')
        identity = st.session_state.setdefault(key+'position_id', uuid4().hex)
        candidate = ledger.add_contract(candidate, contract)
        candidate = ledger.add_position(candidate, ledger.PositionSpec(identity, account, contract.id, bucket, proxy))
        candidate = ledger.append_event(candidate, _event(candidate, 'trade' if mode == 'New opening trade' else 'snapshot', day,
            account_id=account, currency=contract.currency, position_id=identity, quantity=int(quantity), price=price))
        if fees:
            candidate = ledger.append_event(candidate, _event(candidate, 'fee', day, account_id=account,
                currency=contract.currency, amount=fees, position_id=identity))
        from portfolio_app.futures_investments import assign_investment
        investment_id = st.session_state.setdefault(key+'investment_id', 'investment:' + uuid4().hex)
        candidate = assign_investment(candidate, ledger.InvestmentSpec(
            investment_id, investment_name.strip(), account, contract.currency, capital, capital), [identity])
        if mode != 'New opening trade':
            st.caption('Opening cash already includes previous settlements. Performance begins at this snapshot; earlier gains stay unknown.')
        _save_review(directory, journal, revision, candidate, key, holdings_revision=snapshot.revision)
    except (DataError, ValueError) as exc:
        st.info(str(exc))


def render_actual_positions(directory, journal, *, valued=None):
    ledger = _imports()
    state = ledger.replay(journal)
    ungrouped = {identity: balance for identity, balance in state.positions.items() if not balance.spec.investment_id}
    if not ungrouped:
        return
    names = {a.id: a.name for a in journal.accounts}
    st.markdown('**Ungrouped futures contracts**' if journal.investments else '**Futures positions**')
    records = []
    for identity, balance in ungrouped.items():
        records.append({'Position': identity, 'Account': names[balance.spec.account_id], 'Product': balance.contract.product,
            'Maturity': balance.contract.expiry, 'Contracts': balance.quantity, 'Currency': balance.contract.currency,
            'Settled trading P&L since start': str(balance.realized_pnl), 'Fees': str(balance.fees), 'Entered taxes': str(balance.taxes)})
    st.dataframe(pd.DataFrame(records), hide_index=True, column_config={'Position': None})
    st.caption('Manage settlements, closes and rolls in Futures → Positions & transactions.')


def render_accounts(directory, snapshot, valued, *, allocation=None):
    ledger = _imports()
    journal, revision = ledger.load_journal(directory)
    key = _key(directory)
    state = ledger.replay(journal)
    if notice := st.session_state.pop('accounts_notice', None):
        st.success(notice)
    names = {a.id: a.name for a in journal.accounts}
    requested = st.session_state.pop('accounts_requested_position', None)
    if requested is not None:
        from portfolio_app.investment_ui import route_investment_request
        if route_investment_request(journal, requested, key):
            st.session_state[key+'view'] = 'Positions & transactions'
        elif requested in state.positions:
            st.session_state[key+'view'] = 'Positions & transactions'
            st.session_state[key+'transaction_position'] = requested
            from portfolio_app.investment_ui import sync_contract_investment
            sync_contract_investment(journal, key)
        else:
            st.session_state[key+'view'] = 'Cash & financing'
    view = st.segmented_control('Account workspace', ['Positions & transactions', 'Cash & financing', 'Collateral', 'Marks & FX', 'Studies'],
        default='Positions & transactions', key=key+'view')
    if view == 'Studies':
        from portfolio_app.generalized_studies_ui import render_generalized_studies
        from portfolio_app.account_observations import load_observations, current_marks
        from portfolio_app.currency_display import reporting_currency
        marks, _, _ = load_observations(directory)
        marks = current_marks(journal, marks)
        positions = [dict(id=p.spec.id, name=f'{p.contract.product} {p.contract.expiry}', contract=p.contract,
            quantity=p.quantity, last_price=marks[p.spec.id].price if p.spec.id in marks else p.last_price) for p in state.positions.values()]
        render_generalized_studies(directory, positions=positions, reporting_currency=reporting_currency())
        return
    if view == 'Marks & FX':
        render_marks(directory, journal)
        return
    if not journal.accounts:
        st.info('Add a futures position from Positions → Add position → Future, or create an account below.')
    if view == 'Cash & financing':
        render_cash(directory, journal, revision, snapshot, valued)
        return
    if view == 'Collateral':
        from portfolio_app.account_margin_ui import render_account_margin
        from portfolio_app.account_observations import load_observations
        from portfolio_app.currency_display import reporting_currency
        render_account_margin(directory, journal, revision, snapshot.holdings, valued, reporting_currency(), load_observations(directory)[1])
        return
    from portfolio_app.investment_ui import render_investments
    render_investments(directory, journal, revision, valued, key=key)
    if not journal.investments:
        render_actual_positions(directory, journal, valued=valued)
    if state.positions:
        render_transaction(directory, journal, revision)
    with st.expander('Recorded transactions'):
        st.dataframe(pd.DataFrame([asdict(e) for e in journal.events]).astype(str), hide_index=True)
    if journal.events:
        with st.expander('Correct a recorded transaction'):
            identifiers = [e.id for e in journal.events if e.kind != 'void']
            labels = {e.id: f'{e.date} · {e.kind} · {e.note or e.id[:8]}' for e in journal.events}
            selected = st.selectbox('Entry to reverse', identifiers, format_func=labels.get, key=key+'reverse')
            st.caption('A reversal preserves history. Dependent entries may need reversal first; re-enter corrected facts afterward.')
            try:
                candidate = ledger.append_event(journal, _event(journal, 'void', date.today(), replaces=selected))
                _save_review(directory, journal, revision, candidate, key+'void_')
            except DataError as exc:
                st.info(str(exc))


def render_transaction(directory, journal, revision):
    ledger = _imports()
    from portfolio_app.derivative_types import ContractSpec
    state = ledger.replay(journal)
    key = _key(directory)+'transaction_'
    from portfolio_app.investment_ui import sync_contract_investment
    identity = st.selectbox('Contract position', list(state.positions),
        format_func=lambda p: f'{state.positions[p].contract.product} {state.positions[p].contract.expiry} · {state.positions[p].spec.account_id[:8]}',
        key=key+'position', on_change=sync_contract_investment, args=(journal, _key(directory)))
    balance = state.positions[identity]
    if journal.investments and not balance.spec.investment_id:
        st.caption('This contract is not yet part of a named investment. Use the grouping control above to attribute capital and include its history.')
    contract, account = balance.contract, balance.spec.account_id
    action = st.selectbox('Action', ['Record settlement', 'Final settlement / expiry', 'Increase / reduce position', 'Close position', 'Roll position', 'Fee', 'Tax payment / withholding', 'Receive pending cash'], key=key+'action')
    day = st.date_input('Event date', max_value=date.today(), key=key+'date')
    value_day = st.date_input('Cash value date', value=day, key=key+'value_date')
    try:
        candidate = journal
        base = dict(account_id=account, currency=contract.currency, position_id=identity, value_date=value_day.isoformat())
        if action == 'Receive pending cash':
            choices = [p.id for p in state.pending.values() if p.account_id == account]
            if not choices:
                st.info('No pending cash for this account.')
                return
            pending = st.selectbox('Cash claim', choices, key=key+'pending_id')
            candidate = ledger.append_event(candidate, _event(candidate, 'receive', day, account_id=account, reference=pending))
        elif action in {'Fee', 'Tax payment / withholding'}:
            amount = _money_input(f'Amount ({contract.currency})', key+'amount')
            candidate = ledger.append_event(candidate, _event(candidate, 'fee' if action == 'Fee' else 'tax', day, amount=amount, **base))
            if action != 'Fee':
                st.caption('Optional payment entered by you; no tax calculation or liability estimate.')
        else:
            price = _money_input('Settlement price' if action in {'Record settlement', 'Final settlement / expiry'} else 'Execution price', key+'price', '')
            if action in {'Record settlement', 'Final settlement / expiry'}:
                candidate = ledger.append_event(candidate, _event(candidate, 'settlement' if action == 'Record settlement' else 'final_settlement', day, price=price, **base))
            else:
                quantity = -balance.quantity if action in {'Close position','Roll position'} else int(st.number_input('Signed trade quantity', value=1, step=1, key=key+'quantity'))
                close_event = _event(candidate, 'trade', day, quantity=quantity, price=price, **base)
                if action != 'Roll position':
                    candidate = ledger.append_event(candidate, close_event)
                if action == 'Roll position':
                    expiry = st.date_input('New contract maturity', value=date(date.today().year+1,3,19), key=key+'next_expiry')
                    new_price = _money_input('New contract execution price', key+'next_price', '')
                    new_quantity = int(st.number_input('New signed contracts', value=balance.quantity, step=1, key=key+'next_quantity'))
                    next_contract = replace(contract, expiry=expiry.isoformat(), last_trade_date='', final_settlement_date='')
                    next_id = st.session_state.setdefault(key+'next_position_id_' + identity + '_' + expiry.isoformat(), uuid4().hex)
                    candidate = ledger.add_contract(candidate, next_contract)
                    candidate = ledger.add_position(candidate, ledger.PositionSpec(next_id, account, next_contract.id,
                        balance.spec.bucket_id, balance.spec.proxy_position_id, investment_id=balance.spec.investment_id))
                    opening_event = _event(candidate, 'trade', day, account_id=account, currency=contract.currency, position_id=next_id, quantity=new_quantity, price=new_price)
                    group = st.session_state.setdefault(key+'roll_group_' + identity + '_' + expiry.isoformat(), uuid4().hex)
                    candidate = ledger.roll(candidate, close=replace(close_event,group_id=group), opening=replace(opening_event,group_id=group,sequence=close_event.sequence+1))
                fees = _money_input(f'Total transaction fees ({contract.currency})', key+'fees')
                if fees:
                    candidate = ledger.append_event(candidate, _event(candidate, 'fee', day, amount=fees, **base))
        _save_review(directory, journal, revision, candidate, key)
    except (DataError, ValueError) as exc:
        st.info(str(exc))


def render_cash(directory, journal, revision, snapshot, valued):
    ledger = _imports()
    key = _key(directory)+'cash_'
    state = ledger.replay(journal)
    names = {a.id: a.name for a in journal.accounts}
    st.dataframe(pd.DataFrame([{'Account': names[a], 'Currency':c, 'Cash':str(v)} for (a,c),v in state.cash.items()]), hide_index=True)
    if state.loans:
        st.dataframe(pd.DataFrame([{'Account': names[a], 'Currency':c, 'Loan':l, 'Debt':str(v)} for (a,c,l),v in state.loans.items()]), hide_index=True)
    day = st.date_input('Balance / financing date', max_value=date.today(), key=key+'date')
    action = st.selectbox('Cash action', ['Create account', 'Opening currency balance', 'Reconcile cash balance', 'Deposit / withdrawal', 'Transfer', 'Receive pending cash', 'Currency conversion', 'Record existing debt', 'Borrow', 'Repay', 'Interest', 'Fee', 'Tax payment'], key=key+'action')
    try:
        if action == 'Create account':
            candidate, _ = _account_choice(directory, journal, snapshot, valued, key=key+'create_', day=day)
            _save_review(directory, journal, revision, candidate, key, holdings_revision=snapshot.revision)
            return
        if not names:
            return
        account = st.selectbox('Cash account', list(names), format_func=names.get, key=key+'account')
        currency = st.selectbox('Currency', ['EUR','USD','GBP'], key=key+'currency')
        if action == 'Receive pending cash':
            choices = [p.id for p in state.pending.values() if p.account_id == account]
            if not choices:
                st.info('No pending cash for this account.')
                return
            pending = st.selectbox('Pending transfer or claim', choices, key=key+'pending')
            candidate = ledger.append_event(journal, _event(journal, 'receive', day, account_id=account, reference=pending))
            _save_review(directory,journal,revision,candidate,key)
            return
        amount = _money_input('Amount (signed for deposits, withdrawals and interest)', key+'amount')
        note = st.text_input('Description', key=key+'note')
        base = dict(account_id=account, currency=currency, amount=amount, note=note)
        kind = {'Opening currency balance':'cash_open', 'Reconcile cash balance':'cash_adjust', 'Deposit / withdrawal':'cash_adjust',
            'Transfer':'transfer','Currency conversion':'fx','Record existing debt':'debt_open','Borrow':'borrow','Repay':'repay',
            'Interest':'interest','Fee':'fee','Tax payment':'tax'}[action]
        if action == 'Reconcile cash balance':
            base['amount'] = amount - state.cash.get((account,currency), Decimal(0))
            base['note'] = 'Balance reconciliation: ' + note
            st.caption(f'Adjustment to reach entered cash balance: {base["amount"]} {currency}. This is not classified as trading profit.')
        if kind in {'borrow','repay','debt_open'}:
            base['reference'] = st.text_input('Loan identifier', key=key+'loan')
        if kind == 'transfer':
            others = [a for a in names if a != account]
            if not others:
                st.info('Create another account before recording an internal transfer.')
                return
            base['other_account_id'] = st.selectbox('Destination account', others, format_func=names.get, key=key+'destination')
            base['value_date'] = st.date_input('Transfer receipt date', value=day, key=key+'receipt').isoformat()
        if kind == 'fx':
            base['other_currency'] = st.selectbox('Currency received', [c for c in ['EUR','USD','GBP'] if c != currency], key=key+'received_currency')
            base['other_amount'] = _money_input('Actual amount received', key+'received_amount')
            st.caption('Enter actual currency legs. Record any separate conversion fee as a fee entry.')
        candidate = ledger.append_event(journal, _event(journal, kind, day, **base))
        _save_review(directory, journal, revision, candidate, key)
    except (DataError, ValueError) as exc:
        st.info(str(exc))


def render_marks(directory, journal):
    from portfolio_app.account_observations import bind_mark, load_observations, save_observations
    from portfolio_app.derivative_types import PriceObservation
    from portfolio_app.derivative_market import fetch_settlement
    ledger = _imports()
    state = ledger.replay(journal)
    marks, rates, revision = load_observations(directory)
    key = _key(directory)+'marks_'
    if state.positions:
        identity = st.selectbox('Position to value', list(state.positions), key=key+'position')
        contract = state.positions[identity].contract
        day = st.date_input('Observation business date', max_value=date.today(), key=key+'date')
        if st.button('Fetch exact-contract settlement', key=key+'fetch'):
            try:
                observation = fetch_settlement(contract, day.isoformat())
                marks[identity] = bind_mark(journal, identity, observation)
                save_observations(directory,marks,rates,revision)
                st.rerun()
            except (DataError,OSError,ValueError) as exc:
                st.error(str(exc))
        if identity in marks:
            st.caption(f'{marks[identity].price} points · {marks[identity].date} · {marks[identity].source}')
        try:
            price = _money_input('Manual mark price',key+'price','')
            kind = st.selectbox('Mark kind',['manual','daily_settlement','final_settlement'],key=key+'kind')
            source = st.text_input('Mark source', value='Manual observation', key=key+'source')
            if st.button('Save mark only', key=key+'save'):
                marks[identity] = bind_mark(journal, identity, PriceObservation(contract.id,day.isoformat(),price,kind=kind,source=source))
                save_observations(directory,marks,rates,revision)
                st.rerun()
        except (DataError,ValueError) as exc:
            st.caption(str(exc))
        st.caption('Marks change valuation only. Review settlement under Positions & transactions to record cash.')
    with st.expander('Manual FX observations'):
        source = st.selectbox('From currency',['EUR','USD','GBP'],key=key+'fx_from')
        target = st.selectbox('To currency',[c for c in ['EUR','USD','GBP'] if c != source],key=key+'fx_to')
        day = st.date_input('FX observation date',max_value=date.today(),key=key+'fx_date')
        try:
            rate = _money_input('To-currency units per one from-currency unit',key+'fx_rate','')
            if st.button('Save FX observation',key=key+'fx_save'):
                if rate <= 0:
                    raise DataError('FX rate must be positive.')
                rates[source,target] = dict(source_currency=source,target_currency=target,rate=str(rate),date=day.isoformat(),source='Manual FX observation')
                save_observations(directory,marks,rates,revision)
                st.rerun()
        except (DataError,ValueError) as exc:
            st.caption(str(exc))
        st.dataframe(pd.DataFrame(rates.values()),hide_index=True)
