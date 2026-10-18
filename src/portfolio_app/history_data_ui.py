"""Focused corrections for historical market evidence, separate from transaction review."""
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from uuid import uuid4

import pandas as pd
import streamlit as st

from portfolio_app.account_journal import effective_events
from portfolio_app.history_observations import (
    Observation, active_observations, add_observation, lookup, rate, save_observations,
)
from portfolio_app.holdings import DataError
from portfolio_app.money import format_money
from portfolio_app.personal_performance import external_flows
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import replay_securities, selected_events


@dataclass(frozen=True)
class MissingInput:
    kind: str
    subject: str
    date: str
    currency: str = ''
    target: str = ''


def scope_names(directory, journal, scope):
    """Readable, scope-restricted names; missing display metadata never exposes IDs."""
    names = {identity: f'Holding {index+1}' for index, identity in enumerate(scope.positions)}
    try:
        holdings = read_snapshot(directory / 'holdings.csv').holdings
        names.update({row.position_id: str(row['name']) for _, row in holdings.iterrows()
                      if row.position_id in names and pd.notna(row.get('name')) and row['name']})
    except (DataError, OSError):
        pass
    for book in journal.securities:
        if book.position_id in names:
            names.setdefault(book.instrument_id, names[book.position_id])
    for account in journal.accounts:
        if account.id in scope.accounts:
            names[account.id] = account.name
    contracts = {contract.id: contract for contract in journal.contracts}
    for position in journal.positions:
        if position.account_id in scope.accounts:
            contract = contracts[position.contract_id]
            label = f'{contract.name or contract.product} · {contract.expiry}'
            names[position.id] = label
            names[position.contract_id] = label
    names[scope.key] = 'Selected portfolio' if len(scope.positions)+len(scope.accounts) > 1 else 'Position value'
    return names


def readable_reasons(directory, journal, scope, reasons, *, names=None):
    """Remove storage identities from explanations; preserve the actual limitation."""
    names = dict(names) if names is not None else scope_names(directory, journal, scope)
    for book in journal.securities:
        for event in book.records:
            names[event.id] = f'{event.date or "Undated"} transaction'
    for event in journal.events:
        names[event.id] = f'{event.date} account entry'
    results = []
    for reason in reasons:
        if reason.startswith('Review complete scope'):
            message = 'Confirm completeness after reviewing the selected history.'
        else:
            identity, separator, detail = reason.partition(': ')
            message = f'{names.get(identity, "History")}: {detail}' if separator else reason
            message = message.replace('charges have not been confirmed', 'confirm transaction fees')
            for stored, label in sorted(names.items(), key=lambda pair: -len(pair[0])):
                if len(stored) < 16 and not any(marker in stored for marker in ('-', ':')):
                    continue
                message = re.sub(r'(?<![\w-])'+re.escape(stored)+r'(?![\w-])', lambda _: label, message)
            message = re.sub(r'\bsecurity:[^\s,;]+', 'payment', message)
            message = re.sub(r'\b[a-f0-9]{24,64}\b', 'record', message)
        if message not in results:
            results.append(message)
    return tuple(results)


def _subjects(journal, scope):
    return ({b.position_id for b in journal.securities if b.position_id in scope.positions}
            | {b.instrument_id for b in journal.securities if b.position_id in scope.positions}
            | {p.id for p in journal.positions if p.account_id in scope.accounts}
            | {p.contract_id for p in journal.positions if p.account_id in scope.accounts})


def _currencies(journal, scope, observations, target):
    currencies = {target}
    for book in journal.securities:
        if book.position_id in scope.positions:
            for event in selected_events(book):
                currencies.add(event.currency)
                currencies.update(charge.currency for charge in event.charges)
                currencies.update(cost.get('currency', '') for cost in event.costs)
    currencies.update(e.currency for e in effective_events(journal) if e.account_id in scope.accounts)
    subjects = _subjects(journal, scope)
    currencies.update(o.currency for o in active_observations(observations) if o.subject in subjects)
    currencies.update(c.currency for c in journal.contracts if c.id in subjects)
    return currencies - {''}


def missing_inputs(view, journal, scope, observations, target):
    """Only explicit, dated missing prices/rates become correction tasks."""
    reasons = [reason for _, values in view.missing for reason in values]
    for result in (view.result, view.all_result):
        for field in ('value_reasons', 'flow_reasons', 'capital_reasons',
                      'money_weighted_reasons', 'time_weighted_reasons'):
            reasons.extend(getattr(result, field))
    subjects = _subjects(journal, scope)
    missing = set()
    for reason in reasons:
        identity, separator, detail = reason.partition(': ')
        if not separator:
            continue
        price = re.fullmatch(r'missing (?:price|exact-contract mark) on (\d{4}-\d{2}-\d{2})', detail)
        if price and identity in subjects:
            known = [o for o in active_observations(observations) if o.kind == 'price' and o.subject == identity]
            currency = known[-1].currency if known else ''
            contract = next((c for c in journal.contracts if c.id == identity or any(
                p.id == identity and p.contract_id == c.id for p in journal.positions)), None)
            missing.add(MissingInput('price', identity, price[1], currency or (contract.currency if contract else '')))
        fx = re.search(r'([A-Z]{3})/([A-Z]{3}) FX on (\d{4}-\d{2}-\d{2})$', detail)
        if fx and rate(observations, fx[1], fx[2], fx[3]) is None:
            missing.add(MissingInput('fx', fx[1]+'/'+fx[2], fx[3], fx[1], fx[2]))
    # Flow diagnostics deliberately do not guess whether a missing cash amount is FX.
    # Offer a rate only when the amount and its actual currency are known.
    for cash in replay_securities(journal.securities).cash:
        if (cash.position_id in scope.positions) == (cash.account_id in scope.accounts and not cash.external):
            continue
        if (cash.amount is not None and cash.amount != 0 and view.all_start <= cash.date <= view.today
                and rate(observations, cash.currency, target, cash.date) is None):
            missing.add(MissingInput('fx', cash.currency+'/'+target, cash.date, cash.currency, target))
    for event in effective_events(journal):
        if not view.all_start <= event.date <= view.today:
            continue
        if event.account_id in scope.accounts and event.amount and event.currency:
            if rate(observations, event.currency, target, event.date) is None:
                missing.add(MissingInput('fx', event.currency+'/'+target, event.date, event.currency, target))
        if event.other_account_id in scope.accounts and event.other_amount and event.other_currency:
            if rate(observations, event.other_currency, target, event.date) is None:
                missing.add(MissingInput('fx', event.other_currency+'/'+target, event.date, event.other_currency, target))
    return tuple(sorted(missing, key=lambda item: (item.date, item.kind, item.subject)))


def scoped_observations(journal, scope, observations, target):
    subjects = _subjects(journal, scope)
    currencies = _currencies(journal, scope, observations, target)
    return tuple(o for o in active_observations(observations) if
                 o.kind == 'price' and o.subject in subjects
                 or o.kind == 'value' and o.subject == scope.key
                 or o.kind == 'fx' and o.currency in currencies and o.target in currencies)


def save_manual_observation(directory, observations, revision, observation, *, replace_existing=False):
    previous = next((o for o in active_observations(observations) if o.key == observation.key), None)
    if previous and not replace_existing:
        raise DataError('A value is already saved for this date. Use Replace saved value to correct it.')
    if previous:
        observation = replace(observation, replaces=previous.id)
    updated = add_observation(observations, observation)
    save_observations(directory, updated, revision)
    return updated


def _amount(text):
    try:
        value = Decimal(text.strip().replace(',', '.'))
        if not value.is_finite():
            raise ValueError
        return value
    except (InvalidOperation, ValueError):
        raise DataError('Enter a finite number without thousands separators.') from None


def _save_form(directory, observations, revision, *, kind, subject, stamp, currency,
               target='', side='close', boundary='', key):
    existing_key = (kind, subject, stamp, currency, target, side, boundary)
    previous = next((o for o in active_observations(observations) if o.key == existing_key), None)
    label = f'1 {currency} equals ({target})' if kind == 'fx' else f'{"Price" if kind == "price" else "Total value"} ({currency or "currency required"})'
    amount = st.text_input(label, key=key+'amount')
    if previous:
        formatted = str(previous.amount) if kind == 'fx' else format_money(previous.amount, previous.currency)
        st.caption(f'Saved: {formatted} · The original stays in the correction history.')
    if st.button('Replace saved value' if previous else 'Save value', key=key+'save'):
        try:
            observation = Observation(uuid4().hex, kind, subject, stamp, _amount(amount), currency,
                                      target, side, boundary)
            save_manual_observation(directory, observations, revision, observation, replace_existing=previous is not None)
            st.rerun()
        except (DataError, ValueError, OSError) as exc:
            st.error(str(exc))


def _market_form(directory, journal, scope, observations, revision, names, target, key, *, issue=None, kind='price', end):
    kind = issue.kind if issue else kind
    identities = [b.position_id for b in journal.securities if b.position_id in scope.positions]
    identities += [p.id for p in journal.positions if p.account_id in scope.accounts]
    if kind == 'price' and not identities:
        st.caption('No position history in this selection.')
        return
    subject = issue.subject if issue else ''
    if kind == 'price':
        if issue:
            st.caption(names.get(subject, 'Selected holding'))
        else:
            subject = identities[0] if len(identities) == 1 else st.selectbox('Holding', identities, format_func=names.get, key=key+'subject')
    stamp = st.date_input('Date', value=date.fromisoformat(issue.date if issue else end), max_value=date.today(), key=key+'date').isoformat()
    previous = lookup(observations, 'price', subject, stamp) if kind == 'price' else None
    if kind == 'price' and previous is None:
        aliases = {b.position_id: b.instrument_id for b in journal.securities}
        aliases.update({p.id: p.contract_id for p in journal.positions})
        alias = aliases.get(subject)
        previous = lookup(observations, 'price', alias, stamp) if alias else None
        if previous is not None:
            subject = previous.subject
    currency = issue.currency if issue else previous.currency if previous else ''
    currency = st.text_input('Price currency' if kind == 'price' else 'From currency', value=currency, key=key+'currency').strip().upper()
    converted = st.text_input('To currency', value=issue.target if issue else target, key=key+'target').strip().upper() if kind == 'fx' else ''
    if kind == 'fx':
        subject = currency+'/'+converted
    _save_form(directory, observations, revision, kind=kind, subject=subject, stamp=stamp,
               currency=currency, target=converted, key=key)


def _advanced(directory, journal, scope, observations, revision, view, target, names, key):
    action = st.selectbox('Correction', ['Total valuation', 'Value around a cash flow', 'Cash adjustment meaning'], key=key+'action')
    if action == 'Cash adjustment meaning':
        adjustments = [e for e in effective_events(journal) if e.kind == 'cash_adjust'
                       and e.account_id in scope.accounts and view.all_start <= e.date <= view.today]
        if not adjustments:
            st.caption('No cash adjustments in this selection.')
            return
        choices = dict(observations.classifications)
        labels = {'boundary':'Unresolved balance correction', 'external':'Contribution or withdrawal',
                  'income':'Investment income', 'expense':'Investment expense'}
        for event in adjustments:
            choices[event.id] = st.selectbox(f'{event.date} · {names.get(event.account_id, "Account")} · {format_money(event.amount, event.currency)}',
                list(labels), index=list(labels).index(choices.get(event.id, 'boundary')), format_func=labels.get, key=key+'class_'+event.id)
        if st.button('Save classifications', key=key+'class_save'):
            try:
                save_observations(directory, replace(observations, classifications=tuple(choices.items()), reviews=()), revision)
                st.rerun()
            except (DataError, ValueError, OSError) as exc:
                st.error(str(exc))
        return
    boundary, side = '', 'close'
    if action == 'Value around a cash flow':
        flows, _ = external_flows(journal, scope, observations, view.all_start, view.today, target)
        if not flows:
            st.caption('No contributions or withdrawals in this interval.')
            return
        labels = {f.id: f'{f.date} · {format_money(f.amount, target)}' for f in flows}
        boundary = st.selectbox('Contribution or withdrawal', list(labels), format_func=labels.get, key=key+'flow')
        stamp = next(f.date for f in flows if f.id == boundary)
        side = st.radio('Value measured', ['before', 'after'], format_func=lambda s: s.title()+' the cash flow', horizontal=True, key=key+'side')
        st.caption('Use the value of the entire selection immediately before or after this cash flow.')
    else:
        stamp = st.date_input('Date', value=date.fromisoformat(view.all_end), max_value=date.today(), key=key+'date').isoformat()
        st.caption('Value of the entire selection at the end of the day. This overrides its calculated value for this date.')
    currency = st.text_input('Currency', value=target, key=key+'currency').strip().upper()
    _save_form(directory, observations, revision, kind='value', subject=scope.key, stamp=stamp,
               currency=currency, side=side, boundary=boundary, key=key)


def render_history_data(directory, journal, scope, observations, revision, view, target, key):
    panel = st.expander('Correct market data', key=key+'market_data', on_change='rerun')
    if not panel.open:
        return
    with panel:
        names = scope_names(directory, journal, scope)
        issues = missing_inputs(view, journal, scope, observations, target)
        options = (['Missing prices or rates'] if issues else []) + ['Correct a price', 'Correct an exchange rate', 'Advanced corrections']
        action = st.selectbox('Correction', options, key=key+'data_action', label_visibility='collapsed')
        if action == 'Missing prices or rates':
            labels = {i: f'{issue.date} · '+(f'{issue.currency}/{issue.target} exchange rate' if issue.kind == 'fx'
                       else f'{names.get(issue.subject, "Holding")} price') for i, issue in enumerate(issues)}
            chosen = st.selectbox('Missing input', list(labels), format_func=labels.get, key=key+'missing')
            issue = issues[chosen]
            _market_form(directory, journal, scope, observations, revision, names, target,
                         key+f'missing_{issue.kind}_{issue.subject}_{issue.date}_', issue=issue, end=view.all_end)
        elif action == 'Advanced corrections':
            _advanced(directory, journal, scope, observations, revision, view, target, names, key+'advanced_')
        else:
            kind = 'price' if action == 'Correct a price' else 'fx'
            _market_form(directory, journal, scope, observations, revision, names, target, key+kind+'_', kind=kind, end=view.all_end)
        saved = st.expander('Saved market data', key=key+'saved_data', on_change='rerun')
        if saved.open:
            with saved:
                values = scoped_observations(journal, scope, observations, target)
                st.dataframe(pd.DataFrame([{'Date':o.date,
                    'Data': 'Exchange rate' if o.kind == 'fx' else names.get(o.subject, 'Holding'),
                    'Value':str(o.amount) if o.kind == 'fx' else format_money(o.amount, o.currency),
                    'Currency':f'{o.currency}/{o.target}' if o.kind == 'fx' else o.currency,
                    'Timing':{'close':'End of day', 'before':'Before cash flow', 'after':'After cash flow'}[o.side],
                    'Source':'Manual' if o.source == 'manual' else 'Market data'} for o in values]),
                    hide_index=True, width='stretch')
