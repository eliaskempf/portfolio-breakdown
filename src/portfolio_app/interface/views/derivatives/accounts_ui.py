"""Manual actual-account workflows. All posting arithmetic belongs to the journal."""
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import streamlit as st

from portfolio_app.portfolio.holdings import DataError
from portfolio_app.interface.shared.money import format_money
from portfolio_app.interface.views.derivatives.future_entry_help import field_help


def _imports():
    from portfolio_app.ledger import account_journal as ledger
    return ledger


def _key(directory):
    from hashlib import sha256
    return 'accounts_' + sha256(str(directory.resolve()).encode()).hexdigest()[:10] + '_'


def _event(journal, kind, day, **fields):
    ledger = _imports()
    return ledger.Event(id=uuid4().hex, date=day.isoformat() if isinstance(day, date) else day,
                        kind=kind, sequence=max((e.sequence for e in journal.events), default=0) + 1, **fields)


def _money_input(label, key, value='0', *, help=None):
    from portfolio_app.derivatives.derivative_types import decimal
    return decimal(st.text_input(label, value=value, key=key, help=help), label)


def _decimal_entry(label, key, placeholder):
    from portfolio_app.derivatives.derivative_types import decimal
    text = st.text_input(label, key=key, placeholder=placeholder).strip()
    return decimal(text, label) if text else None


def _save_review(directory, journal, revision, candidate, key, *, holdings_revision=None,
                 review_label='Review changes', save_label='Save reviewed changes', compare_balances=False,
                 cancel_button=None, destructive=False, on_saved=None):
    """Require explicit review; preserve the exact reviewed candidate on reruns."""
    if candidate == journal:
        st.session_state.pop(key+'pending', None)
        return
    ledger = _imports()
    import json
    raw = asdict(candidate)
    raw['events'] = [{k:v for k,v in asdict(e).items() if k != 'id'} for e in candidate.events]
    signature = json.dumps(raw, default=str, sort_keys=True)
    pending = st.session_state.get(key + 'pending')
    if pending and (pending[1] != revision or pending[3] != signature):
        st.session_state.pop(key + 'pending')
        pending = None
        st.caption('Review expired', help='The inputs or journal changed. Review the current entries before saving.')
    if not pending:
        with st.container(horizontal=True):
            if st.button(review_label, key=key + 'review'):
                pending = (candidate, revision, holdings_revision, signature)
                st.session_state[key + 'pending'] = pending
            if cancel_button and not pending:
                cancel_button()
        if not pending:
            return
    proposed, expected, held_revision, _ = pending
    st.markdown('**Reviewed changes**')
    events = proposed.events[len(journal.events):]
    if events:
        from portfolio_app.ledger.account_activity import ACTIONS, AMOUNT_KINDS
        contracts = {p.id: next(c for c in proposed.contracts if c.id == p.contract_id) for p in proposed.positions}
        st.dataframe(pd.DataFrame([dict(Date=e.date, Action=ACTIONS.get(e.kind, e.kind),
            Contract=(contracts[e.position_id].product + ' ' + contracts[e.position_id].expiry) if e.position_id in contracts else '', Currency=e.currency,
            Amount=str(e.amount) if e.kind in AMOUNT_KINDS else '', Quantity=e.quantity if e.kind in {'snapshot', 'trade'} else '',
            Price=str(e.price or ''), Note=e.note) for e in events]), hide_index=True)
    from portfolio_app.interface.views.derivatives.investment_ui import investment_review_rows
    investment_changes = investment_review_rows(journal, proposed)
    if investment_changes:
        st.dataframe(pd.DataFrame(investment_changes), hide_index=True)
    state = ledger.replay(proposed)
    names = {a.id: a.name for a in proposed.accounts}
    st.dataframe(pd.DataFrame([{'Account': names[a], 'Currency': c, 'Cash after posting': 'Unknown' if (a,c) in state.unknown_cash else str(v)} for (a,c),v in state.cash.items()]), hide_index=True)
    if compare_balances:
        from portfolio_app.ledger.account_activity import accounting_effects
        effects = accounting_effects(journal, proposed)
        if effects:
            st.markdown('**Accounting effects**')
            st.dataframe(pd.DataFrame(effects), hide_index=True, width='stretch')
    with st.container(horizontal=True):
        if cancel_button and destructive:
            cancel_button()
        save = st.button(save_label, type='primary', key=key + 'save', help='Post the reviewed entries together.')
        if cancel_button and not destructive:
            cancel_button()
    if save:
        try:
            from portfolio_app.workspace.workspace_lock import workspace_lock
            from portfolio_app.portfolio.actual_portfolio import freeze_position_keys, validate_links
            from portfolio_app.portfolio.positions import read_snapshot
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
            if key.endswith('cash_'):
                st.session_state.pop(key+'create_new_account_id', None)
            if key.endswith('entry_'):
                for field in list(st.session_state):
                    if field.startswith(key):
                        del st.session_state[field]
            if on_saved:
                on_saved()
            else:
                st.session_state['accounts_notice'] = 'Account changes saved.'
                st.session_state['position_edit_dialog'] = False
            st.rerun()


def _account_choice(directory, journal, snapshot, valued, *, key, day, allow_funding=True, creating=False):
    """Return journal candidate and account ID; explicitly adopt or add cash."""
    ledger = _imports()
    candidate = journal
    if not creating:
        names = {a.id: a.name for a in journal.accounts if not a.cash_type}
        selected = st.selectbox('Account', ['', *names], format_func=lambda x: names.get(x, 'Create account'),
                                key=key+'account', help=field_help('Account'))
        if selected:
            return candidate, selected
    name = st.text_input('Account name', key=key+'account_name', help=field_help('Account name'))
    currency = st.selectbox('Opening cash currency', ['EUR','USD','GBP'], key=key+'cash_currency', help=field_help('Opening cash currency'))
    linked = {x.position_id for x in journal.cash_links}
    cash_rows = valued.loc[valued.get('instrument_type', pd.Series('', index=valued.index)).eq('cash') & valued.position_id.isin(set(snapshot.holdings.position_id) - linked)]
    cash_rows = cash_rows.loc[cash_rows.quote_currency.eq(currency)] if 'quote_currency' in cash_rows else cash_rows.iloc[:0]
    options = {'': 'Enter cash not already included in holdings', **{r.position_id: f'Adopt existing cash: {r["name"]}' for _,r in cash_rows.iterrows()}}
    source = st.selectbox('Cash source', list(options), format_func=options.get, key=key+'cash_source', help=field_help('Cash source'))
    if source:
        existing = cash_rows.loc[cash_rows.position_id.eq(source)].iloc[0]
        amount = Decimal(str(existing.shares)) * Decimal(str(existing.current_price))
        st.caption(f'The existing cash contribution will be replaced, preserving its balance of {amount} {currency}.')
    else:
        start_mode = (st.radio('Account start', ['Existing balance', 'Newly funded account'], horizontal=True,
            key=key+'start_mode', help='Existing balance starts from a snapshot. Newly funded declares the account empty before its first contribution; the funding source must be outside this tracked portfolio.')
            if allow_funding else 'Existing balance')
        if start_mode == 'Newly funded account':
            funding_day = st.date_input('Funding date', value=day, max_value=day, key=key+'funding_date')
            amount = _money_input(f'Initial contribution ({currency})', key+'initial_contribution', '',
                help='Actual positive funding from outside the tracked portfolio. Tracked account transfers must use Transfer instead.')
            if amount <= 0:
                raise DataError('Enter a positive initial contribution.')
        else:
            amount = _money_input('Cash balance (cash only, not account net liquidation value)', key+'cash', help=field_help('Cash balance (cash only, not account net liquidation value)'))
    if not name.strip():
        raise DataError('Enter an account name to continue.')
    account_id = st.session_state.setdefault(key+'new_account_id', uuid4().hex)
    candidate = ledger.add_account(candidate, ledger.Account(account_id, name.strip()))
    if not source and start_mode == 'Newly funded account':
        candidate = ledger.append_event(candidate, _event(candidate, 'cash_open', funding_day,
            account_id=account_id, currency=currency, amount=Decimal(0), source='confirmed_zero_start'))
        candidate = ledger.append_event(candidate, _event(candidate, 'cash_flow', funding_day,
            account_id=account_id, currency=currency, amount=amount))
    else:
        candidate = ledger.append_event(candidate, _event(candidate, 'cash_open', day, account_id=account_id, currency=currency, amount=amount))
    if source:
        candidate = replace(candidate, cash_links=(*candidate.cash_links, ledger.CashLink(account_id, currency, source)))
    return candidate, account_id


def render_future_entry(directory, snapshot, *, valued=None, allocation=None):
    ledger = _imports()
    from portfolio_app.derivatives.derivative_types import ContractSpec
    from portfolio_app.derivatives.derivative_market import CATALOG, make_contract
    journal, revision = ledger.load_journal(directory)
    key = _key(directory) + 'entry_'
    valued = snapshot.holdings if valued is None else valued
    from portfolio_app.interface.views.derivatives.future_entry_help import install_label_help
    install_label_help()
    mode = st.radio('Record', ['New opening trade', 'Already-open position snapshot'], horizontal=True, key=key+'mode', help=field_help('Record'))
    product = st.selectbox('Futures product', [*CATALOG, 'Manual contract'],
        format_func=lambda code: f'{code} · {CATALOG[code].name}' if code in CATALOG else code, key=key+'product', help=field_help('Futures product'))
    day = st.date_input('Trade / snapshot date', value=date.today(), max_value=date.today(), key=key+'date', help=field_help('Trade / snapshot date'))
    from portfolio_app.interface.views.derivatives.contract_maturity_ui import maturity_input
    expiry = maturity_input(product, key=key+'expiry_')
    if expiry is None:
        return
    try:
        if product == 'Manual contract':
            code = st.text_input('Product code', key=key+'code', help=field_help('Product code'))
            currency = st.selectbox('Settlement currency', ['EUR','USD','GBP'], key=key+'currency', help=field_help('Settlement currency'))
            multiplier = _money_input('Currency per index point', key+'multiplier', '', help=field_help('Currency per index point'))
            tick = _money_input('Price tick', key+'tick', '0.01', help=field_help('Price tick'))
            contract = ContractSpec(code, expiry.isoformat(), currency, multiplier, tick, source='Manual specification')
        else:
            contract = make_contract(product, expiry.isoformat())
            st.caption(f'{contract.currency} {contract.multiplier} per point · exact maturity {contract.expiry}')
        quantity = st.number_input('Signed contracts (negative for short)', value=1, step=1, key=key+'quantity', help=field_help('Signed contracts (negative for short)'))
        price = _money_input('Fill price' if mode == 'New opening trade' else 'Last settled price / clearing basis', key+'price', '', help=field_help('Fill price' if mode == 'New opening trade' else 'Last settled price / clearing basis'))
        fees = _money_input('Opening fees' if mode == 'New opening trade' else 'New fees to record (exclude costs already in opening cash)', key+'fees', help=field_help('Opening fees' if mode == 'New opening trade' else 'New fees to record (exclude costs already in opening cash)'))
        candidate, account = _account_choice(directory, journal, snapshot, valued, key=key, day=day,
                                            allow_funding=mode == 'New opening trade')
        investment_name = st.text_input('Investment name', value=f'{contract.product} futures', key=key+'investment_name', help=field_help('Investment name'))
        capital = _money_input(f'Initial allocated capital ({contract.currency})', key+'investment_capital', help=field_help('Initial allocated capital'))
        categories = allocation.descendants() if allocation else set()
        labels = {b.id: b.name for b in allocation.buckets} if allocation else {}
        bucket = st.selectbox('Category', ['', *sorted(categories)], format_func=lambda x: labels.get(x, 'Unassigned'), key=key+'bucket', help=field_help('Category'))
        proxies = snapshot.holdings.loc[snapshot.holdings.get('instrument_type', pd.Series('', index=snapshot.holdings.index)).eq('etf')]
        names = {r.position_id: r['name'] for _,r in proxies.iterrows()}
        proxy = st.selectbox('Index holdings proxy (optional)', ['', *names], format_func=lambda x: names.get(x, 'No proxy'), key=key+'proxy', help=field_help('Index holdings proxy (optional)'))
        identity = st.session_state.setdefault(key+'position_id', uuid4().hex)
        candidate = ledger.add_contract(candidate, contract)
        candidate = ledger.add_position(candidate, ledger.PositionSpec(identity, account, contract.id, bucket, proxy))
        candidate = ledger.append_event(candidate, _event(candidate, 'trade' if mode == 'New opening trade' else 'snapshot', day,
            account_id=account, currency=contract.currency, position_id=identity, quantity=int(quantity), price=price))
        if fees:
            candidate = ledger.append_event(candidate, _event(candidate, 'fee', day, account_id=account,
                currency=contract.currency, amount=fees, position_id=identity))
        from portfolio_app.derivatives.futures_investments import assign_investment
        investment_id = st.session_state.setdefault(key+'investment_id', 'investment:' + uuid4().hex)
        candidate = assign_investment(candidate, ledger.InvestmentSpec(
            investment_id, investment_name.strip(), account, contract.currency, capital, capital), [identity])
        if mode != 'New opening trade':
            st.caption('Opening cash already includes previous settlements. Performance begins at this snapshot; earlier gains stay unknown.')
        _save_review(directory, journal, revision, candidate, key, holdings_revision=snapshot.revision)
    except (DataError, ValueError) as exc:
        st.info(str(exc))


def render_actual_positions(directory, journal, *, valued=None, state=None):
    if not journal.positions:
        return
    ledger = _imports()
    state = ledger.replay(journal) if state is None else state
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
        from portfolio_app.interface.views.derivatives.investment_ui import route_investment_request
        if route_investment_request(journal, requested, key):
            st.session_state[key+'view'] = 'Positions & transactions'
        elif requested in state.positions:
            st.session_state[key+'view'] = 'Positions & transactions'
            st.session_state[key+'transaction_position'] = requested
            from portfolio_app.interface.views.derivatives.investment_ui import sync_contract_investment
            sync_contract_investment(journal, key)
        else:
            st.session_state[key+'view'] = 'Cash & financing'
            st.session_state[key+'cash_action'] = None
            # The valuation pipeline already resolves cash links, loans and pending claims.
            # Use its account/currency fields, including for opaque generated row IDs.
            if requested and 'account_id' in valued:
                match = valued.loc[valued.position_id.eq(requested)]
                if not match.empty and match.iloc[0].account_id in names:
                    st.session_state[key+'cash_account'] = match.iloc[0].account_id
                    st.session_state[key+'cash_currency'] = match.iloc[0].quote_currency
    if origin := st.session_state.get('accounts_return_tab'):
        st.button(f'Back to {origin}', key=key+'return', icon=':material/arrow_back:',
                  on_click=st.session_state.update, kwargs={'main_tabs': origin})
    st.session_state.setdefault(key+'view', 'Positions & transactions')
    view = st.segmented_control('Account workspace', ['Positions & transactions', 'Cash & financing', 'Collateral', 'Marks & FX', 'Settlement history', 'Studies'],
        required=True, key=key+'view')
    if view == 'Studies':
        from portfolio_app.interface.views.studies.generalized_studies_ui import render_generalized_studies
        from portfolio_app.derivatives.futures_valuation import load_valuations
        from portfolio_app.interface.shared.currency_display import reporting_currency
        valuations, _ = load_valuations(directory, journal)
        marks = {pid: value.observation for pid, value in valuations.items() if value.observation}
        positions = [dict(id=p.spec.id, name=f'{p.contract.product} {p.contract.expiry}', contract=p.contract,
            quantity=p.quantity, last_price=marks[p.spec.id].price if p.spec.id in marks else p.last_price) for p in state.positions.values()]
        render_generalized_studies(directory, positions=positions, reporting_currency=reporting_currency())
        return
    if view == 'Settlement history':
        from portfolio_app.interface.views.derivatives.settlement_archive_ui import render_history_checks
        render_history_checks(directory,journal,list(state.positions),key=key+'history_')
        return
    if view == 'Marks & FX':
        render_marks(directory, journal)
        return
    if not journal.has_broker_workspace and view != 'Cash & financing':
        st.button('Create account', key=key+'start_account', on_click=st.session_state.update,
                  kwargs={key+'view': 'Cash & financing', key+'cash_action': 'Create account'})
        return
    if view == 'Cash & financing':
        render_cash(directory, journal, revision, snapshot, valued, state=state)
        return
    if view == 'Collateral':
        from portfolio_app.interface.views.derivatives.account_margin_ui import render_account_margin
        from portfolio_app.ledger.account_observations import load_observations
        from portfolio_app.interface.shared.currency_display import reporting_currency
        render_account_margin(directory, journal, revision, snapshot.holdings, valued, reporting_currency(), load_observations(directory)[1])
        return
    from portfolio_app.interface.views.derivatives.investment_ui import render_investments
    render_investments(directory, journal, revision, valued, key=key)
    if not journal.investments:
        render_actual_positions(directory, journal, valued=valued)
    if state.positions:
        render_transaction(directory, journal, revision)
    elif len(journal.accounts) == 1:
        account = journal.accounts[0]
        _account_alert(account.id, account.name, key+'monitoring')
    from portfolio_app.interface.views.history.account_activity_ui import render_activity
    render_activity(directory, journal, revision, key=key+'journal_')


def _account_alert(account, name, key):
    from portfolio_app.interface.views.monitoring.monitoring_ui import request_monitoring
    st.button('Create account alert', key=key, help=f'Create a monitoring rule for {name}.',
              on_click=request_monitoring, kwargs={'account': account})


def _contract_label(balance, accounts):
    return f'{balance.contract.product} {balance.contract.expiry} · {accounts[balance.spec.account_id]}'


def _claim_label(claim):
    return f'{claim.kind} · {claim.due_date} · {claim.amount} {claim.currency}'


def render_transaction(directory, journal, revision, *, position_ids=None, key_prefix=None):
    ledger = _imports()
    from portfolio_app.derivatives.derivative_types import ContractSpec
    state = ledger.replay(journal)
    key = key_prefix or _key(directory)+'transaction_'
    choices = list(state.positions) if position_ids is None else list(position_ids)
    if not choices:
        return
    from portfolio_app.interface.views.derivatives.investment_ui import sync_contract_investment
    names = {a.id: a.name for a in journal.accounts}
    if key_prefix is not None and key+'position' not in st.session_state:
        st.session_state[key+'position'] = next((p for p in reversed(choices) if state.positions[p].quantity), choices[-1])
    identity = st.selectbox('Contract position', choices,
        format_func=lambda p: _contract_label(state.positions[p], names),
        key=key+'position', on_change=sync_contract_investment if key_prefix is None else None, args=(journal, _key(directory)) if key_prefix is None else None)
    balance = state.positions[identity]
    if journal.investments and not balance.spec.investment_id:
        st.caption('This contract is not yet part of a named investment. Use the grouping control above to attribute capital and include its history.')
    contract, account = balance.contract, balance.spec.account_id
    if key_prefix is None:
        _account_alert(account, names[account], key+'monitoring')
    actions = ['Record settlement', 'Final settlement / expiry', 'Increase / reduce position', 'Close position', 'Roll position', 'Fee', 'Tax payment / withholding', 'Receive pending cash']
    if key_prefix is not None:
        actions.remove('Roll position')
    action = st.selectbox('Action', actions, key=key+'action')
    day = st.date_input('Event date', max_value=date.today(), key=key+'date')
    try:
        candidate = journal
        if action == 'Receive pending cash':
            choices = [p.id for p in state.pending.values() if p.account_id == account
                       and (position_ids is None or p.position_id in position_ids)]
            if not choices:
                st.info('No pending cash for this account.')
                return
            pending = st.selectbox('Cash claim', choices,
                format_func=lambda p: _claim_label(state.pending[p]),
                key=key+'pending_id')
            candidate = ledger.append_event(candidate, _event(candidate, 'receive', day, account_id=account, reference=pending))
            _save_review(directory, journal, revision, candidate, key)
            return
        value_day = st.date_input('Cash value date', value=day, key=key+'value_date')
        base = dict(account_id=account, currency=contract.currency, position_id=identity, value_date=value_day.isoformat())
        if action in {'Fee', 'Tax payment / withholding'}:
            amount = _money_input(f'Amount ({contract.currency})', key+'amount')
            candidate = ledger.append_event(candidate, _event(candidate, 'fee' if action == 'Fee' else 'tax', day, amount=amount, **base))
            if action != 'Fee':
                st.caption('Optional payment entered by you; no tax calculation or liability estimate.')
        else:
            price_label = 'Settlement price' if action in {'Record settlement', 'Final settlement / expiry'} else 'Execution price'
            price = _decimal_entry(price_label, key+'price', 'Enter price in points')
            if price is None:
                return
            if action in {'Record settlement', 'Final settlement / expiry'}:
                candidate = ledger.append_event(candidate, _event(candidate, 'settlement' if action == 'Record settlement' else 'final_settlement', day, price=price, **base))
            else:
                quantity = -balance.quantity if action in {'Close position','Roll position'} else int(st.number_input('Signed trade quantity', value=1, step=1, key=key+'quantity'))
                close_event = _event(candidate, 'trade', day, quantity=quantity, price=price, **base)
                if action != 'Roll position':
                    candidate = ledger.append_event(candidate, close_event)
                if action == 'Roll position':
                    from portfolio_app.interface.views.derivatives.contract_maturity_ui import maturity_input
                    expiry = maturity_input(contract.product if contract.exchange == 'EUREX' else None,
                        label='New contract maturity', key=key+'next_expiry_', after=contract.expiry)
                    if expiry is None:
                        return
                    if expiry.isoformat() <= contract.expiry:
                        raise DataError('Choose a new maturity later than the current contract.')
                    new_price = _decimal_entry('New contract execution price', key+'next_price', 'Enter price in points')
                    if new_price is None:
                        return
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


def render_cash(directory, journal, revision, snapshot, valued, *, state=None):
    ledger = _imports()
    key = _key(directory)+'cash_'
    state = state if state is not None else ledger.replay(journal)
    names = {a.id: a.name for a in journal.accounts}
    brokers = [a.id for a in journal.accounts if not a.cash_type]
    account = st.selectbox('Cash account', brokers, format_func=names.get, key=key+'account') if brokers else None
    if account:
        cash = [{'Currency': c, 'Cash': 'Unknown' if (a, c) in state.unknown_cash else str(v)}
                for (a, c), v in state.cash.items() if a == account]
        if cash:
            st.dataframe(pd.DataFrame(cash), hide_index=True)
        else:
            st.caption('No cash balances recorded.')
        loans = [{'Currency': c, 'Loan': loan, 'Debt': str(v)}
                 for (a, c, loan), v in state.loans.items() if a == account]
        if loans:
            st.dataframe(pd.DataFrame(loans), hide_index=True)
        claims = [{'Currency': claim.currency, 'Pending': str(claim.amount), 'Due': claim.due_date}
                  for claim in state.pending.values() if claim.account_id == account]
        if claims:
            st.dataframe(pd.DataFrame(claims), hide_index=True)
        _account_alert(account, names[account], key+'monitoring')
    actions = ['Create account', 'Opening currency balance', 'Reconcile cash balance', 'Deposit / withdrawal',
               'Transfer', 'Receive pending cash', 'Currency conversion', 'Record existing debt',
               'Borrow', 'Repay', 'Interest', 'Fee', 'Tax payment']
    action = st.selectbox('Cash action', actions, index=None, placeholder='Choose an action',
                          key=key+'action') if account else 'Create account'
    if not action:
        return
    day = st.date_input('Balance / financing date', max_value=date.today(), key=key+'date')
    try:
        if action == 'Create account':
            candidate, _ = _account_choice(directory, journal, snapshot, valued, key=key+'create_', day=day, creating=True)
            _save_review(directory, journal, revision, candidate, key, holdings_revision=snapshot.revision)
            return
        if not account:
            return
        if action == 'Receive pending cash':
            choices = [p.id for p in state.pending.values() if p.account_id == account]
            if not choices:
                st.info('No pending cash for this account.')
                return
            pending = st.selectbox('Pending transfer or claim', choices, format_func=lambda p: _claim_label(state.pending[p]), key=key+'pending')
            candidate = ledger.append_event(journal, _event(journal, 'receive', day, account_id=account, reference=pending))
            _save_review(directory,journal,revision,candidate,key)
            return
        if action == 'Transfer' and len(names) == 1:
            st.info('Create another account before recording an internal transfer.')
            return
        currency = st.selectbox('Currency', ['EUR','USD','GBP'], key=key+'currency')
        amount = _money_input('Amount (signed for deposits, withdrawals and interest)', key+'amount')
        note = st.text_input('Description', key=key+'note')
        base = dict(account_id=account, currency=currency, amount=amount, note=note)
        kind = {'Opening currency balance':'cash_open', 'Reconcile cash balance':'cash_adjust', 'Deposit / withdrawal':'cash_flow',
            'Transfer':'transfer','Currency conversion':'fx','Record existing debt':'debt_open','Borrow':'borrow','Repay':'repay',
            'Interest':'interest','Fee':'fee','Tax payment':'tax'}[action]
        if action == 'Reconcile cash balance':
            dated_state = ledger.replay(journal, as_of=day.isoformat())
            base['amount'] = amount - dated_state.cash.get((account,currency), Decimal(0))
            base['note'] = 'Balance reconciliation: ' + note
            st.caption(f'Adjustment to reach entered cash balance: {base["amount"]} {currency}. This is not classified as trading profit.')
        if kind in {'borrow','repay','debt_open'}:
            base['reference'] = st.text_input('Loan identifier', key=key+'loan')
        if kind == 'transfer':
            others = [a.id for a in journal.accounts if a.id != account and (not a.cash_type or a.denomination == currency)]
            if not others:
                st.info('Add a destination account in this currency.')
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
    from portfolio_app.ledger.account_observations import bind_mark, load_observations, save_observations
    from portfolio_app.derivatives.derivative_types import PriceObservation
    from portfolio_app.derivatives.derivative_market import fetch_settlement
    ledger = _imports()
    state = ledger.replay(journal)
    marks, rates, revision = load_observations(directory)
    key = _key(directory)+'marks_'
    if state.positions:
        names = {account.id: account.name for account in journal.accounts}
        identity = st.selectbox('Position to value', list(state.positions),
            format_func=lambda p: _contract_label(state.positions[p], names), key=key+'position')
        contract = state.positions[identity].contract
        day = st.date_input('Observation business date', max_value=date.today(), key=key+'date')
        if st.button('Fetch and save settlement mark', key=key+'fetch', help='Updates the valuation mark for this contract. Cash postings are recorded separately.'):
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
            price = _decimal_entry('Manual mark price', key+'price', 'Enter price in points')
            kind = st.selectbox('Mark kind', ['manual','daily_settlement','final_settlement'], format_func=lambda kind: kind.replace('_', ' ').title(), key=key+'kind')
            source = st.text_input('Mark source', value='Manual observation', key=key+'source')
            if st.button('Save mark only', disabled=price is None, key=key+'save'):
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
            rate = _decimal_entry('To-currency units per one from-currency unit', key+'fx_rate', 'Enter exchange rate')
            if st.button('Save FX observation', disabled=rate is None, key=key+'fx_save'):
                if rate <= 0:
                    raise DataError('FX rate must be positive.')
                rates[source,target] = dict(source_currency=source,target_currency=target,rate=str(rate),date=day.isoformat(),source='Manual FX observation')
                save_observations(directory,marks,rates,revision)
                st.rerun()
        except (DataError,ValueError) as exc:
            st.caption(str(exc))
        st.dataframe(pd.DataFrame(rates.values()),hide_index=True)
