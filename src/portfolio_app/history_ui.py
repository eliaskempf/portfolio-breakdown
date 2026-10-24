"""Optional history editors; financial transformations live in pure modules."""
from portfolio_app.chart_hover import chart_hover, money_history_axes
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from uuid import uuid4, uuid5, NAMESPACE_URL

import pandas as pd
import streamlit as st

from portfolio_app.account_journal import load_journal
from portfolio_app.cost_basis import active_components
from portfolio_app.holdings import DataError
from portfolio_app.history_commands import book_for, preview_activation, save_history, reconstruction_candidate
from portfolio_app.history_observations import (
    HistoryScope, Observation, add_observation,
    load_observations, save_observations,
)
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import (
    Charge, SecurityEvent, SecurityLot, append_evidence,
    selected_events,
)


def _decimal(value, *, optional=False):
    if value is None or str(value).strip() in ('', 'None', 'nan'):
        return None if optional else Decimal(0)
    try:
        result = Decimal(str(value).replace(',', '.'))
        if not result.is_finite():
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise DataError('Enter a finite decimal number without thousands separators.') from None


def _money(value, currency=''):
    if value is None:
        return 'Unknown'
    return f'{value:.2f}' + (' '+currency if currency else '')


def _key(directory, identity):
    return 'personal_history_' + sha256((str(directory.resolve())+identity).encode()).hexdigest()[:12] + '_'


def prepared_history_view(directory, journal, scope, observations, revision, period='1Y', *, target=None, include_points=True, calculation=None):
    """Reuse a chart across dialog interactions until its actual inputs change."""
    from portfolio_app.currency_display import reporting_currency
    from portfolio_app.history_view import history_view
    from portfolio_app.input_cache import cached_input
    from portfolio_app.security_history import history_fingerprint
    target = target or reporting_currency()
    stamp = date.today()
    signature = str(directory.resolve()), history_fingerprint(journal), revision, scope, period, target, stamp, include_points
    return cached_input('history_view_'+scope.key+('_chart' if include_points else '_summary'), signature,
        lambda: history_view(journal, scope, observations, period, today=stamp, target=target, include_points=include_points, calculation=calculation))


def _record_id(key, record):
    return uuid5(NAMESPACE_URL, key+json.dumps(record, default=str, sort_keys=True)).hex


def _show_error(exc):
    st.error(str(exc))


def _history_action(key, action):
    if action in ('buy', 'sell', 'add'):
        # A new entry starts clean; returning to the list never changes saved evidence.
        for field in list(st.session_state):
            if field.startswith(key+'new_'):
                del st.session_state[field]
        st.session_state[key+'new_kind'] = action if action in ('buy', 'sell') else 'buy'
        action = 'add'
    st.session_state[key+'screen'] = action


def _save_evidence(directory, candidate, journal_revision, holdings_revision, *, screen=None):
    """Save before rendering the next dialog view, without a full-app rerun."""
    key = _key(directory, candidate.position_id)
    try:
        save_history(directory, candidate, expected_journal_revision=journal_revision,
                     expected_holdings_revision=holdings_revision)
    except (DataError, ValueError, OSError) as exc:
        st.session_state[key+'save_error'] = str(exc)
        return
    st.session_state.pop(key+'save_error', None)
    if screen is not None:
        _history_action(key, screen)


def _capture_submit_revision(key, journal_revision, holdings_revision):
    # Preserve the version the user saw, while fields come from the submission.
    st.session_state[key+'submitted_revision'] = (journal_revision, holdings_revision)


def _visible_records(book):
    from portfolio_app.security_history import current_records
    return list(current_records(book))


def _transaction_table(records, book):
    columns = ['Date', 'Transaction', 'Shares', 'Trade value', 'Fees', 'Status']
    entries = []
    for event in records:
        charges = ' + '.join(_money(c.amount, c.currency) for c in event.charges)
        entries.append(dict(zip(columns, [event.date or 'Unknown', _KIND_LABELS.get(event.kind,event.kind.title()),
            str(event.quantity) if event.quantity else '—', _money(event.gross, event.currency),
            charges or (_money(Decimal(0), event.currency) if event.charges_known else 'Unknown' if event.kind in ('buy','sell','distribution') else '—'),
            'Applied' if event.id in book.active_ids else 'Changes saved; not applied' if event.replaces else 'Saved; not applied'])))
    return pd.DataFrame(entries, columns=columns)


def _request_removal(key, identities, revision):
    st.session_state[key+'removal'] = (tuple(identities), revision)
    _history_action(key, 'remove')


def _remove_transactions(directory, journal, revision, snapshot, book, row, key):
    from portfolio_app.history_commands import remove_transactions
    identities, selected_revision = st.session_state.get(key+'removal', ((), None))
    if selected_revision != revision:
        st.warning('History changed. Return to the list and select the transactions again.')
        return
    selected = [event for event in _visible_records(book) if event.id in identities]
    st.markdown(f'**Remove {len(selected)} '+('transaction?' if len(selected) == 1 else 'transactions?')+'**')
    st.dataframe(_transaction_table(selected, book), hide_index=True, width='stretch')
    candidate = remove_transactions(book, identities)
    if candidate.active_ids != book.active_ids:
        _activation(directory, journal, revision, snapshot, candidate, row, key+'remove_',
                    resolution='accept', show_review=True, action_label='Remove transactions')
    else:
        st.caption('These entries are not applied. Your portfolio balances stay unchanged.')
        st.button('Remove transactions', type='primary', key=key+'confirm_remove', on_click=_save_evidence,
                  args=(directory, candidate, revision, snapshot.revision), kwargs={'screen': 'list'})


def render_position_history(directory, row):
    """Transactions can be entered before a starting balance or reconstruction is known."""
    if row.get('instrument_type') == 'cash':
        st.caption('Cash history is available in Accounts & futures.')
        return
    try:
        journal, revision = load_journal(directory)
        snapshot = read_snapshot(directory / 'holdings.csv')
        raw = snapshot.holdings.loc[snapshot.holdings.position_id == row.position_id]
        if raw.empty:
            return
        book = book_for(journal, raw.iloc[0])
        key = _key(directory, row.position_id)
        if error := st.session_state.pop(key+'save_error', None):
            st.error(error)
        screen = st.session_state.get(key+'screen', 'list')
        # Separate subviews must replace their whole DOM subtree. Reusing
        # unkeyed horizontal blocks can briefly mix list and review actions.
        with st.container(key=key+'screen_'+screen):
            if screen != 'list':
                st.button('Back to transactions', icon=':material/arrow_back:', key=key+'back',
                    on_click=_history_action, args=(key, 'list'))
            if screen == 'opening':
                _opening_form(directory, journal, revision, snapshot, book, row, key)
            elif screen == 'add':
                _add_transaction(directory, journal, revision, snapshot, book, key)
            elif screen == 'import_pdf':
                from portfolio_app.transaction_import_ui import render_pdf_import
                render_pdf_import(directory, row, book, journal, revision, snapshot, key)
            elif screen == 'edit':
                _edit_transaction(directory, journal, revision, snapshot, book, key)
            elif screen == 'remove':
                _remove_transactions(directory, journal, revision, snapshot, book, row, key)
            elif screen == 'replace':
                _replace_balance(directory, journal, revision, snapshot, book, row, key)
            elif screen in ('fix', 'review'):
                _review_history(directory, journal, revision, snapshot, book, row, key)
            else:
                records = _visible_records(book)
                with st.container(horizontal=True):
                    st.button('Add buy', icon=':material/add:', key=key+'buy', on_click=_history_action, args=(key,'buy'))
                    st.button('Add sell', icon=':material/remove:', key=key+'sell', on_click=_history_action, args=(key,'sell'))
                    st.button('Import PDFs', key=key+'import_pdf', on_click=_history_action, args=(key,'import_pdf'))
                    st.button('Other transaction', key=key+'add', on_click=_history_action, args=(key,'add'))
                if records:
                    from portfolio_app.transaction_list_ui import render_transaction_list
                    def edit(identity):
                        st.session_state[key+'edit_record'] = identity
                        _history_action(key, 'edit')
                    render_transaction_list(records, _transaction_table(records, book), key=key+'transactions_'+str(revision),
                        revision=revision, on_edit=edit, on_remove=lambda ids: _request_removal(key, ids, revision))
                if not records:
                    st.caption('No transactions yet. Add buys and sells, then review them against your portfolio.')
                elif any(e.id not in book.active_ids for e in records):
                    st.caption('Saved transactions do not change your holding until you review and apply them.')
                with st.container(horizontal=True):
                    if records:
                        st.button('Review against portfolio', type='primary',
                            key=key+'review_list', on_click=_history_action, args=(key,'review'))
                    st.button('Add starting balance', type='tertiary', key=key+'starting',
                        help='Optional: the holding before the transactions you have records for.', on_click=_history_action, args=(key,'opening'))
                    if book.active and records:
                        st.button('Replace balance', type='tertiary', key=key+'replace', on_click=_history_action, args=(key,'replace'))
    except (DataError, ValueError, OSError) as exc:
        _show_error(exc)


def _review_history(directory, journal, revision, snapshot, book, row, key):
    st.markdown('**Review against portfolio**')
    records = _visible_records(book)
    by_id = {e.id:e for e in records}
    superseded = {e.replaces for e in records if e.replaces}
    review = st.container()
    with st.expander('Review options'):
        chosen = st.multiselect('Included transactions', list(by_id), default=[i for i in by_id if i not in superseded],
            format_func=lambda i: _event_label(by_id[i]), key=key+'included_'+str(revision))
        st.button('Add starting balance', type='tertiary', key=key+'review_start',
            help='Use this if you already held shares before your first recorded transaction.',
            on_click=_history_action, args=(key,'opening'))
        if journal.accounts:
            names = {'':'Do not change cash balances', **{a.id:a.name for a in journal.accounts}}
            account = st.selectbox('Linked cash account', list(names), index=list(names).index(book.account_id),
                format_func=names.get, disabled=book.active, key=key+'review_account',
                help='Link only when these payments should update this account. Payments already covered by its starting balance are not posted again.')
            book = replace(book, account_id=account)
    with review:
        openings = [by_id[i] for i in chosen if by_id[i].kind == 'opening']
        if not openings or all(e.source == 'confirmed_zero_start' for e in openings):
            st.caption('From your first purchase · Starts with zero shares. Already owned shares? Add a starting balance in Review options.')
        else:
            st.caption('Starting balance: '+_event_label(max(openings, key=lambda e: (e.date,e.sequence))))
        if chosen:
            try:
                candidate = reconstruction_candidate(book, chosen, starts_at_zero=not openings)
                _activation(directory, journal, revision, snapshot, candidate, row, key+'fix_', resolution='accept', show_review=True)
            except (DataError, ValueError) as exc:
                st.warning(str(exc))
                st.button('Keep portfolio unchanged', key=key+'keep', on_click=_history_action, args=(key,'list'))
        else:
            st.caption('Select transactions in Review options to compare them with your portfolio.')
    panel = st.expander('Edit transactions, starting lots and fees', key=key+'advanced', on_change='rerun')
    if panel.open:
        with panel:
            _fix_history(directory, journal, revision, snapshot, book, row, key+'advanced_')


_KIND_LABELS = {'opening':'Starting balance', 'buy':'Buy', 'sell':'Sell', 'distribution':'Distribution',
    'fee':'Fee', 'tax':'Tax', 'split':'Share split', 'transfer':'Transfer between holdings',
    'transfer_in':'Transfer in', 'transfer_out':'Transfer out', 'cost':'Remaining acquisition cost', 'note':'Unresolved event'}


def _event_label(event):
    if event.source == 'confirmed_zero_start':
        return 'From first purchase (zero starting shares)'
    return f'{event.date or "Undated"} · {_KIND_LABELS.get(event.kind,event.kind.title())} · {event.quantity:g} units'


def _opening_form(directory, journal, revision, snapshot, book, row, key):
    st.markdown('**Add starting balance**')
    st.caption('Optional: what you held at the end of this date, before your recorded transactions.')
    key += 'opening_'
    left, right = st.columns(2)
    stamp = left.date_input('Balance date', max_value=date.today(), key=key+'date')
    quantity = right.text_input('Units held', value=str(row.shares), key=key+'quantity')
    left, right = st.columns([3,1])
    total = row.shares*row.acquisition_price if pd.notna(row.acquisition_price) else None
    cost = left.text_input('Total acquisition cost', value=str(total) if total is not None else '',
        help='What you paid for this holding, including purchase fees. Leave blank if unknown.', key=key+'cost')
    currency = right.text_input('Currency', value=row.get('acquisition_currency') or 'EUR', key=key+'currency').upper()
    if not book.active and journal.accounts:
        names = {'':'Cash not tracked', **{a.id:a.name for a in journal.accounts}}
        account = st.selectbox('Cash account', list(names), index=list(names).index(book.account_id), format_func=names.get, key=key+'account')
        book = replace(book, account_id=account)
    try:
        fields = dict(kind='opening', date=stamp.isoformat(), quantity=_decimal(quantity), amount=_decimal(cost, optional=True), currency=currency)
        if fields['quantity'] == _decimal(row.shares) and fields['amount'] == (_decimal(total) if total is not None else None) and currency == row.get('acquisition_currency'):
            fields['costs'] = tuple(active_components(row))
        event = SecurityEvent(_record_id(key+str(revision), fields), **fields)
        candidate = append_evidence(book, (event,))
        st.button('Save starting balance', key=key+'evidence', on_click=_save_evidence,
            args=(directory, candidate, revision, snapshot.revision), kwargs={'screen':'list'})
    except (DataError, ValueError) as exc:
        st.caption(str(exc))


def _event_rows(book):
    replaced = {e.replaces for e in book.records if e.replaces}
    result = []
    for e in book.records:
        if e.kind == 'void' or (e.id in replaced and e.id not in book.active_ids):
            continue
        result.append(dict(record=e.id, Use=e.id in book.active_ids, Kind=e.kind, Date=e.date,
            Order=e.sequence, Quantity=str(e.quantity), Price='' if e.price is None else str(e.price),
            Gross='' if e.amount is None else str(e.amount), Currency=e.currency,
            Fees='' if not e.charges else str(e.charges[0].amount) if e.charges[0].amount is not None else '',
            Fee_currency=e.charges[0].currency if e.charges else '', Fee_date=e.charges[0].date if e.charges else '',
            Charges_known=e.charges_known, Destination=e.other_position_id, Note=e.note))
    return result


def _holding_choices(snapshot, book):
    choices = {'': 'Choose a holding'}
    for index, row in enumerate(snapshot.holdings.itertuples(), 1):
        if row.position_id != book.position_id:
            choices[row.position_id] = f'{index}. {row.name}'
    for event in book.records:
        if event.other_position_id and event.other_position_id not in choices:
            choices[event.other_position_id] = f'Unavailable holding {len(choices)}'
    return choices


def _fix_history(directory, journal, revision, snapshot, book, row, key):
    account_names = {'': 'Position only / cash not tracked', **{a.id: a.name for a in journal.accounts}}
    account = st.selectbox('Cash account', list(account_names), index=list(account_names).index(book.account_id),
        format_func=account_names.get, disabled=book.active, key=key+'account')
    book = replace(book, account_id=account)
    st.caption('Use selects the reconstruction to activate. Saving evidence alone leaves balances unchanged.')
    rows = _event_rows(book)
    if not rows:
        rows = [dict(record='', Use=True, Kind='opening', Date='', Order=0, Quantity=str(row.shares),
            Price='', Gross=str(row.shares*row.acquisition_price) if pd.notna(row.acquisition_price) else '',
            Currency=row.get('acquisition_currency') or 'EUR', Fees='', Fee_currency='', Fee_date='',
            Charges_known=False, Destination='', Note='')]
    destinations = _holding_choices(snapshot, book)
    destination_ids = {label: identity for identity, label in destinations.items()}
    for entry in rows:
        entry['Destination'] = destinations.get(entry['Destination'], 'Choose a holding')
    edited = st.data_editor(pd.DataFrame(rows), num_rows='dynamic', hide_index=True,
        key=key+'evidence_'+str(revision), column_config={
            'record': None, 'Kind': st.column_config.SelectboxColumn(options=['opening','buy','sell','distribution','split','transfer','transfer_in','transfer_out','fee','tax','cost','note']),
            'Date': st.column_config.TextColumn(help='YYYY-MM-DD; blank evidence is retained but cannot be activated.'),
            'Gross': st.column_config.TextColumn(help='Opening/remaining total cost, or gross trade/income amount before fees.'),
            'Quantity': st.column_config.TextColumn(help='Units; for a split, new units per old unit.'),
            'Fees': st.column_config.TextColumn(help='Enter 0 for no fee; a blank amount is unknown.'),
            'Charges_known': st.column_config.CheckboxColumn('Fees confirmed', help='All fees for this transaction are entered, including an explicit zero if there were none.'),
            'Destination': st.column_config.SelectboxColumn('Destination holding', options=list(destination_ids))})
    with st.expander('Starting lots, fees and taxes'):
        st.caption('Opening lots are optional. Supply actual lot costs and ordering for FIFO after partial sales. Separate fees and taxes can also be entered as dated fee/tax rows.')
        opening_id = st.selectbox('Opening record', ['']+[e.id for e in book.records if e.kind == 'opening'],
            format_func=lambda x: 'Save the opening evidence first' if not x else next(e.date or 'Undated opening' for e in book.records if e.id == x), key=key+'lot_open')
        existing = next((e for e in book.records if e.id == opening_id), None)
        lot_rows = [dict(Date=l.date, Quantity=str(l.quantity), Cost=l.costs[0]['amount'] if l.costs else '',
            Currency=l.costs[0]['currency'] if l.costs else '') for l in existing.lots] if existing else []
        lot_table = st.data_editor(pd.DataFrame(lot_rows, columns=['Date','Quantity','Cost','Currency']),
            num_rows='dynamic', key=key+'lots_'+str(revision), hide_index=True)
        charge_id = st.selectbox('Fees and taxes for transaction', ['']+[e.id for e in book.records],
            format_func=lambda x: 'Choose a saved record' if not x else next((e.date or 'Undated')+' · '+e.kind for e in book.records if e.id == x), key=key+'charges_record')
        charged = next((e for e in book.records if e.id == charge_id), None)
        charge_table = st.data_editor(pd.DataFrame([dict(Amount='' if c.amount is None else str(c.amount), Currency=c.currency,
            Date=c.date, Kind=c.kind, External=c.external) for c in charged.charges] if charged else [],
            columns=['Amount','Currency','Date','Kind','External']), num_rows='dynamic', hide_index=True, key=key+'charges_'+str(revision),
            column_config={'Kind':st.column_config.SelectboxColumn(options=['fee','tax']), 'External':st.column_config.CheckboxColumn()})
    try:
        selected, additions = [], []
        known = {e.id: e for e in book.records}
        for record in edited.to_dict('records'):
            def text(field):
                value = record.get(field)
                return '' if value is None or pd.isna(value) else str(value).strip()
            if not text('Kind'):
                continue
            old = known.get(text('record'))
            fees = _decimal(text('Fees'), optional=True)
            charges = (Charge(fees, text('Fee_currency') or text('Currency'), text('Fee_date')),) if text('Fees') else ()
            # Preserve multi-charge/FX evidence when its simplified table fields are unchanged.
            if old and (str(old.charges[0].amount) if old.charges and old.charges[0].amount is not None else '') == text('Fees') and old.charges:
                if text('Fee_currency') == old.charges[0].currency and text('Fee_date') == old.charges[0].date:
                    charges = old.charges
            fields = dict(kind=text('Kind'), date=text('Date'), sequence=int(_decimal(text('Order'))),
                quantity=_decimal(text('Quantity')), price=_decimal(text('Price'), optional=True), amount=_decimal(text('Gross'), optional=True),
                currency=text('Currency').upper(), charges=charges, charges_known=bool(record.get('Charges_known') or False),
                other_position_id=destination_ids.get(text('Destination'), ''), note=text('Note'))
            if old:
                same_cost = (fields['quantity'], fields['amount'], fields['currency']) == (old.quantity, old.amount, old.currency)
                fields.update(lots=old.lots if same_cost else (), costs=old.costs if same_cost else (), value_date=old.value_date, source=old.source)
            elif fields['kind'] == 'opening' and fields['quantity'] == _decimal(row.shares) and fields['currency'] == row.get('acquisition_currency'):
                current_cost = _decimal(row.shares)*_decimal(row.acquisition_price) if pd.notna(row.acquisition_price) else None
                if fields['amount'] == current_cost:
                    fields['costs'] = tuple(active_components(row))
            if old and old.id == opening_id and not lot_table.empty:
                from portfolio_app.cost_basis import component
                fields['lots'] = tuple(SecurityLot(_record_id(key, lot), _decimal(lot['Quantity']),
                    (component(_decimal(lot['Quantity']), _decimal(lot['Cost'], optional=True), str(lot['Currency']).upper(), str(lot['Date'] or '')),),
                    True, str(lot['Date'] or '')) for lot in lot_table.to_dict('records') if str(lot.get('Quantity') or '').strip())
            if old and old.id == charge_id:
                fields['charges'] = tuple(Charge(_decimal(c['Amount'], optional=True), str(c['Currency']).upper(), str(c.get('Date') or ''),
                    str(c.get('Kind') or 'fee'), bool(c.get('External') or False)) for c in charge_table.to_dict('records') if c.get('Currency'))
            identity = old.id if old else _record_id(key+str(revision), fields)
            candidate = SecurityEvent(identity, **fields, replaces=old.replaces if old else '')
            if old and candidate != old:
                candidate = replace(candidate, id=_record_id(key+str(revision), fields), replaces=old.id)
            if candidate != old:
                additions.append(candidate)
            if bool(record.get('Use')):
                selected.append(candidate.id)
        candidate = append_evidence(book, additions)
        st.button('Save evidence', key=key+'save_evidence', on_click=_save_evidence,
            args=(directory, candidate, revision, snapshot.revision))
        candidate = replace(candidate, active_ids=tuple(selected))
        if selected:
            _activation(directory, journal, revision, snapshot, candidate, row, key+'fix_', resolution='accept')
        if book.records:
            with st.expander('Retained evidence'):
                st.dataframe(pd.DataFrame([dict(Date=e.date, Kind=e.kind, Quantity=str(e.quantity), Source=e.source,
                    Status='Active' if e.id in book.active_ids else 'Evidence', Note=e.note) for e in book.records]), hide_index=True)
        if row.get('purchase_history'):
            from portfolio_app.purchase_ui import render_purchase_history
            render_purchase_history(row.to_dict() if hasattr(row, 'to_dict') else row)
            _legacy_evidence(directory, journal, revision, snapshot, book, row, key)
    except (DataError, ValueError, TypeError) as exc:
        st.caption(str(exc))


def _legacy_evidence(directory, journal, revision, snapshot, book, row, key):
    from portfolio_app.purchases import read_purchase_history
    batches = read_purchase_history(row['purchase_history'])
    index = st.selectbox('Saved batch to review', range(len(batches)), format_func=lambda i: f'Batch {i+1} · {batches[i]["mode"]}', key=key+'legacy_batch')
    if st.button('Copy batch to evidence', key=key+'legacy_copy'):
        batch = batches[index]
        additions = []
        for number, purchase in enumerate(batch['purchases']):
            source = 'legacy:'+str(batch.get('batch_id', index))+':'+str(number)
            if any(e.source == source for e in book.records):
                continue
            additions.append(SecurityEvent(_record_id(book.position_id, source), 'buy', purchase['date'],
                quantity=_decimal(purchase['shares']), price=_decimal(purchase['price'], optional=True),
                currency=batch['currency'], charges=(Charge(_decimal(purchase['fees']), batch['currency']),),
                charges_known=True, source=source, note='Review overlap: legacy '+batch['mode']))
        save_history(directory, append_evidence(book, additions), expected_journal_revision=revision, expected_holdings_revision=snapshot.revision)
        st.rerun()


def _activation(directory, journal, revision, snapshot, candidate, row, key, *, resolution, review_container=None, show_review=False, action_label=None):
    from portfolio_app.currency_display import reporting_currency
    from portfolio_app.history_observations import ObservationFX
    historical = ObservationFX(load_observations(directory)[0])
    signature = sha256(json.dumps(asdict(candidate), default=str, sort_keys=True).encode()).hexdigest()
    if show_review or (review_container or st).button('Review history', key=key+'review'):
        st.session_state[key+'reviewed'] = (signature, revision, snapshot.revision)
    if st.session_state.get(key+'reviewed') != (signature, revision, snapshot.revision):
        return
    candidate, comparison, cash = preview_activation(journal, candidate, candidate.active_ids, row, target=reporting_currency(), historical=historical)
    def number(value):
        return 'Unknown' if value is None else format(value, '.10f').rstrip('0').rstrip('.') or '0'
    old_average = comparison.recorded_cost/comparison.recorded_quantity if comparison.recorded_cost is not None and comparison.recorded_quantity else None
    new_average = comparison.reconstructed_cost/comparison.reconstructed_quantity if comparison.reconstructed_cost is not None and comparison.reconstructed_quantity else None
    st.table(pd.DataFrame([
        {'Measure':'Shares', 'Current portfolio':number(comparison.recorded_quantity), 'From history':number(comparison.reconstructed_quantity)},
        {'Measure':'Acquisition cost ('+comparison.currency+')', 'Current portfolio':_money(comparison.recorded_cost), 'From history':_money(comparison.reconstructed_cost)},
        {'Measure':'Average buy-in ('+comparison.currency+')', 'Current portfolio':_money(old_average), 'From history':_money(new_average)},
    ]), hide_index=True, border='horizontal')
    if not comparison.quantity_matches or comparison.cost_matches is False:
        st.caption('The balances differ. Applying history replaces the current holding with the figures above.')
    elif comparison.cost_matches is None:
        st.caption('Acquisition cost is not yet comparable.')
    else:
        st.caption('Shares and acquisition cost match.')
    if comparison.reconstructed_cost is None:
        from portfolio_app.security_history import replay_securities
        from portfolio_app.cost_basis import resolve_cost
        state = replay_securities((*[b for b in journal.securities if b.position_id != candidate.position_id], candidate)).positions[candidate.position_id]
        remaining_lots = {lot.id for lot in state.lots}
        missing_fees = any(e.id in remaining_lots and e.kind == 'buy' and not e.charges_known for e in selected_events(candidate))
        reason = ('Purchase fees are unconfirmed. Complete them below; enter 0 if there were no fees.' if missing_fees
                  else resolve_cost(list(state.costs), comparison.currency, historical).note)
        st.info('Cost and average buy-in unavailable: '+reason)
    changed_cash = [c for c in cash if c['Before'] != c['After'] or (c['Account'] == candidate.account_id and c['After'] is None)]
    if changed_cash:
        names = {a.id:a.name for a in journal.accounts}
        st.markdown('**Cash balance changes**')
        st.table(pd.DataFrame([{'Account':names.get(c['Account'], 'Linked account'), 'Currency':c['Currency'],
            'Current balance':_money(c['Before']), 'After applying':_money(c['After'])} for c in changed_cash]), hide_index=True, border='horizontal')
        st.caption('Applying history also updates these cash balances. Payments covered by the account’s starting balance are already included.')
    from portfolio_app.security_history import duplicate_candidates
    duplicates = duplicate_candidates(candidate)
    duplicate_accept = False
    if duplicates:
        st.warning('Some transactions have matching dates and amounts. Check that they are separate trades before applying.')
        duplicate_accept = st.radio('Matching transactions', ['I need to check them', 'They are separate trades'],
            horizontal=True, key=key+'duplicates') == 'They are separate trades'
    complete_from = complete_through = ''
    if action_label is None:
        from portfolio_app.history_confirmation import confirmation_interval
        complete_from, complete_through = confirmation_interval(candidate)
        interval = f'{date.fromisoformat(complete_from):%d %b %Y} – {date.fromisoformat(complete_through):%d %b %Y}'
        st.caption('Confirm complete history · '+interval)
        st.caption('All purchases, sales, income, fees, taxes and transfers in this interval are recorded. '
                   'Still backfilling? Choose Apply incomplete history.')
    blocked = comparison.reconstructed_cost is None or any(c['After'] is None for c in changed_cash) or bool(duplicates and not duplicate_accept)
    def apply(*, complete):
        save_history(directory, candidate, expected_journal_revision=revision, expected_holdings_revision=snapshot.revision,
            activate=True, resolution=resolution, accept_balance_change=True, accept_cash_change=True,
            accept_duplicates=duplicate_accept, complete_from=complete_from if complete else '',
            complete_through=complete_through if complete else '')
        st.session_state.pop(key+'reviewed', None)
        st.session_state[_key(directory, candidate.position_id)+'screen'] = 'list'
        st.rerun()
    with st.container(horizontal=True):
        if st.button(action_label or 'Apply and confirm history', key=key+'save', disabled=blocked, type='primary',
                     help='Use the displayed balances and confirm the shown history interval. Missing market data still limits returns.'
                     if action_label is None else 'Apply the displayed transaction and balance changes.'):
            apply(complete=action_label is None)
        if action_label is None and st.button('Apply incomplete history', key=key+'save_incomplete', disabled=blocked,
                                              type='tertiary', help='Update balances without declaring this history complete.'):
            apply(complete=False)
        st.button('Keep portfolio unchanged', key=key+'keep', on_click=_history_action,
            args=(_key(directory, candidate.position_id),'list'))
    if blocked and not duplicates:
        st.caption('Complete the missing inputs before applying. Your saved transactions and current portfolio are preserved.')
    _complete_fees(directory, journal, revision, snapshot, candidate, key)
    if comparison.reconstructed_cost is None:
        _complete_cost_fx(directory, state.costs, comparison.currency, historical, key)


def _complete_fees(directory, journal, revision, snapshot, candidate, key):
    stored = next((b for b in journal.securities if b.position_id == candidate.position_id), None)
    if stored is None:
        return
    saved_ids = {e.id for e in stored.records}
    missing = [e for e in selected_events(candidate) if e.id in saved_ids and e.kind in ('buy','sell','distribution') and not e.charges_known
               and (not e.charges or (len(e.charges) == 1 and e.charges[0].kind == 'fee'))]
    if not missing:
        return
    with st.expander('Complete transaction fees', expanded=True):
        event_id = st.selectbox('Transaction with unconfirmed fees', [e.id for e in missing],
            format_func=lambda i: _event_label(next(e for e in missing if e.id == i)), key=key+'fee_record')
        event = next(e for e in missing if e.id == event_id)
        previous = event.charges[0] if event.charges else None
        currency = previous.currency if previous else event.currency
        amount = st.text_input('Fees ('+currency+')', value=str(previous.amount) if previous and previous.amount is not None else '',
            placeholder='0 if none; blank if unknown', key=key+'fee_amount_'+event_id)
        st.caption('Save the total fee for this transaction. Previously entered fees are shown for confirmation.')
        if st.button('Save fees', key=key+'save_fees', disabled=not amount.strip()):
            fee = replace(previous, amount=_decimal(amount)) if previous else Charge(_decimal(amount), currency)
            fields = dict(charges=(fee,), charges_known=True, replaces=event.id)
            corrected = replace(event, id=_record_id(event.id, fields), **fields)
            # Only retain the correction here, not the candidate's temporary zero boundary.
            save_history(directory, append_evidence(stored, (corrected,)), expected_journal_revision=revision,
                expected_holdings_revision=snapshot.revision)
            st.rerun()


def _complete_cost_fx(directory, costs, target, historical, key):
    missing = sorted({(p['currency'], p['date']) for p in costs
        if p['currency'] and p['currency'] != target and p['date'] and p['amount'] is not None
        and _decimal(p['amount']) != 0 and target not in p.get('conversions', {})
        and historical.get(p['currency'], target, p['date']).rate is None})
    if not missing:
        return
    with st.expander('Complete exchange rates', expanded=True):
        st.caption('Enter the actual historical rate for each date. No current rate is substituted.')
        for currency, stamp in missing:
            suffix = currency+stamp
            value = st.text_input(f'{stamp} · {target} per 1 {currency}', key=key+'fx_'+suffix)
            if st.button('Save exchange rate', key=key+'save_fx_'+suffix, disabled=not value.strip()):
                store, revision = load_observations(directory)
                observation = Observation(uuid4().hex, 'fx', currency+'/'+target, stamp, _decimal(value), currency, target)
                save_observations(directory, add_observation(store, observation), revision)
                st.rerun()


def _add_transaction(directory, journal, revision, snapshot, book, key):
    key += 'new_'
    st.markdown('**Add transaction**')
    left, right = st.columns(2)
    kind = left.selectbox('Transaction', ['buy','sell','distribution','fee','tax','split','transfer','transfer_in','transfer_out'],
        format_func=_KIND_LABELS.get, key=key+'kind')
    stamp = right.date_input('Transaction date', max_value=date.today(), key=key+'date')
    left, right = st.columns([3,1])
    quantity = left.text_input('Quantity' if kind != 'split' else 'New units per old unit',
        value='0' if kind in ('distribution','fee','tax') else '', key=key+'quantity')
    currency = right.text_input('Currency', value='EUR', key=key+'currency').upper()
    left, right = st.columns(2)
    total_label = 'Total paid (including fees)' if kind == 'buy' else 'Total received (after fees)'
    amount_mode = left.selectbox('Amount entry', ['Unit price','Trade value (excluding fees)',total_label], key=key+'amount_mode') if kind in ('buy','sell') else 'Amount'
    amount = right.text_input(amount_mode, key=key+'amount') if kind != 'split' else ''
    fee = st.text_input('Fees', placeholder='0 if none; blank if unknown',
        help='Enter the total transaction fees. For a total paid or received, these fees are already included and will not be counted twice. In the transaction currency unless specified below.', key=key+'fee')
    with st.expander('Different fee currency or settlement date'):
        left, right = st.columns(2)
        fee_currency = left.text_input('Fee currency', value=currency, key=key+'fee_currency').upper()
        fee_date = right.date_input('Fee date', value=stamp, max_value=date.today(), key=key+'fee_date')
        external = st.checkbox('Fee paid outside the linked cash account', key=key+'external') if book.account_id else False
        deferred = st.checkbox('Cash settles later', key=key+'deferred') if book.account_id and kind in ('buy','sell') else False
        settlement = st.date_input('Cash settlement date', min_value=stamp, key=key+'settlement') if deferred else None
    destinations = _holding_choices(snapshot, book)
    destination = st.selectbox('Destination holding', list(destinations), format_func=destinations.get,
        key=key+'destination') if kind == 'transfer' else ''
    try:
        qty, value, charge = _decimal(quantity), _decimal(amount, optional=True), _decimal(fee, optional=True)
        known = charge is not None
        price, gross = (value, None) if amount_mode == 'Unit price' else (None, value)
        if amount_mode == total_label:
            if not known or fee_currency != currency or value is None:
                raise DataError('Enter the fees (0 if none) in the transaction currency to use a total paid or received.')
            gross = value-(charge or 0) if kind == 'buy' else value+(charge or 0)
        fields = dict(kind=kind, date=stamp.isoformat(), sequence=max([e.sequence for e in journal.events]+[e.sequence for b in journal.securities for e in b.records]+[0])+1,
            quantity=qty, price=price, amount=gross, currency=currency,
            charges=(Charge(charge, fee_currency, fee_date.isoformat(), external=external),) if fee else (), charges_known=known,
            other_position_id=destination, value_date=settlement.isoformat() if settlement else '')
        event = SecurityEvent(_record_id(key+str(revision), fields), **fields)
        candidate = append_evidence(book, (event,))
        if st.button('Save transaction', key=key+'evidence', type='primary',
                     on_click=_capture_submit_revision, args=(key, revision, snapshot.revision)):
            expected = st.session_state.pop(key+'submitted_revision')
            _save_evidence(directory, candidate, *expected, screen='list')
            st.rerun()
    except (DataError, ValueError) as exc:
        st.caption(str(exc))


def _edit_transaction(directory, journal, revision, snapshot, book, key):
    from portfolio_app.history_commands import correct_transaction
    records = _visible_records(book)
    if not records:
        st.caption('No transactions to edit.')
        return
    st.markdown('**Edit transaction**')
    by_id = {e.id:e for e in records}
    identity = st.session_state.get(key+'edit_record')
    if identity not in by_id:
        st.caption('Return to transactions and choose the entry to edit.')
        return
    event = by_id[identity]
    st.caption(_event_label(event))
    key += 'edit_'+identity+'_'
    left, right = st.columns(2)
    stamp = left.date_input('Transaction date', value=date.fromisoformat(event.date) if event.date else None,
        max_value=date.today(), key=key+'date')
    quantity = right.text_input('New units per old unit' if event.kind == 'split' else 'Quantity',
        value=str(event.quantity), key=key+'quantity')
    left, right = st.columns([3,1])
    modes = ['Unit price','Trade value (excluding fees)'] if event.kind in ('buy','sell') else ['Total acquisition cost' if event.kind in ('opening','cost') else 'Amount']
    mode = left.selectbox('Amount entry', modes, index=0 if event.price is not None else 1, key=key+'mode') if len(modes) > 1 else modes[0]
    currency = right.text_input('Currency', value=event.currency, key=key+'currency').upper()
    original_value = event.price if mode == 'Unit price' else event.gross
    amount = (st if len(modes) > 1 else left).text_input(mode, value=str(original_value) if original_value is not None else '', key=key+'amount_'+mode) if event.kind != 'split' else ''
    fees = []
    # Keep each original fee/tax separate, including its currency, date and funding.
    charges = event.charges or ((Charge(Decimal(0) if event.charges_known else None, event.currency),) if event.currency and event.kind in ('buy','sell','distribution') else ())
    for index, charge in enumerate(charges):
        suffix = str(index)
        fee = st.text_input('Fees' if len(charges) == 1 and charge.kind == 'fee' else f'{charge.kind.title()} {index+1}',
            value=str(charge.amount) if charge.amount is not None else '', placeholder='0 if none; blank if unknown', key=key+'fee_'+suffix)
        with st.expander('Fee currency and date' if len(charges) == 1 else f'{charge.kind.title()} {index+1} details'):
            left, right = st.columns(2)
            fee_currency = left.text_input('Fee currency', value=charge.currency, key=key+'fee_currency_'+suffix).upper()
            fee_date = right.date_input('Fee date', value=date.fromisoformat(charge.date) if charge.date else None,
                max_value=date.today(), key=key+'fee_date_'+suffix,
                help='Leave empty to use the transaction date.')
            external = st.checkbox('Fee paid outside the linked cash account', value=charge.external, key=key+'external_'+suffix) if book.account_id or charge.external else False
        fees.append((charge, fee, fee_currency, fee_date, external))
    with st.expander('Settlement and note'):
        settlement = st.date_input('Cash settlement date', value=date.fromisoformat(event.value_date) if event.value_date else None,
            key=key+'settlement', help='Leave empty for settlement on the transaction date.')
        note = st.text_input('Note', value=event.note, key=key+'note')
    try:
        updated_fees = tuple(replace(c, amount=_decimal(v, optional=True), currency=currency,
            date=stamp.isoformat() if stamp else '', external=external) for c,v,currency,stamp,external in fees)
        known = all(c.amount is not None for c in updated_fees) if fees else event.charges_known
        # Preserve the absence of an explicitly confirmed zero fee on unchanged records.
        if not event.charges and known and all(c.amount == 0 for c in updated_fees):
            updated_fees = ()
        if not event.charges and event.currency and updated_fees == (Charge(None, event.currency),):
            updated_fees = ()
        price = _decimal(amount, optional=True) if mode == 'Unit price' else None
        gross = _decimal(amount, optional=True) if mode != 'Unit price' else None
        if mode == 'Unit price' and price == event.price and _decimal(quantity) == event.quantity:
            gross = event.amount
        candidate = correct_transaction(book, identity, date=stamp.isoformat() if stamp else '', quantity=_decimal(quantity),
            price=price, amount=gross, currency=currency, charges=updated_fees, charges_known=known,
            value_date=settlement.isoformat() if settlement else '', note=note)
        with st.container(horizontal=True):
            if st.button('Save changes', type='primary', key=key+'save',
                         on_click=_capture_submit_revision, args=(key, revision, snapshot.revision)):
                # Build from this submission's widget values. A callback argument
                # prepared on the previous render can silently save stale input.
                expected = st.session_state.pop(key+'submitted_revision')
                if candidate != book or expected != (revision, snapshot.revision):
                    _save_evidence(directory, candidate, *expected, screen='list')
                else:
                    _history_action(_key(directory, book.position_id), 'list')
                st.rerun()
            st.button('Cancel', key=key+'cancel', on_click=_history_action, args=(_key(directory, book.position_id),'list'))
        st.caption('Full precision is preserved. Changes affect your holding only after Review against portfolio.')
    except (DataError, ValueError) as exc:
        st.caption(str(exc))


def _replace_balance(directory, journal, revision, snapshot, book, row, key):
    key += 'replace_'
    stamp = st.date_input('Balance date', max_value=date.today(), key=key+'date')
    quantity = st.text_input('Total quantity', value=str(row.shares), key=key+'quantity')
    cost = st.text_input('Total acquisition cost', value=str(row.shares*row.acquisition_price) if pd.notna(row.acquisition_price) else '', key=key+'cost')
    currency = st.text_input('Cost currency', value=row.get('acquisition_currency') or 'EUR', key=key+'currency')
    st.caption('A replacement starts a new tracking interval. Earlier evidence remains saved.')
    try:
        fields = dict(kind='opening', date=stamp.isoformat(), sequence=max([e.sequence for e in journal.events]+[e.sequence for b in journal.securities for e in b.records]+[0])+1,
            quantity=_decimal(quantity), amount=_decimal(cost, optional=True), currency=currency.upper())
        event = SecurityEvent(_record_id(key+str(revision), fields), **fields)
        candidate = replace(append_evidence(book, (event,)), active_ids=(*book.active_ids, event.id))
        _activation(directory, journal, revision, snapshot, candidate, row, key, resolution='boundary')
    except (DataError, ValueError) as exc:
        st.caption(str(exc))


def prepare_portfolio_history(directory, holdings, *, config, bucket='', displayed=None, current_valued=None, include_points=True):
    """Read once before choosing the initial Overview tab; no UI side effects."""
    from portfolio_app.currency_display import reporting_currency
    journal, _ = load_journal(directory)
    from portfolio_app.history_scope import category_history_scope
    scope = category_history_scope(journal, holdings, config, bucket, displayed=displayed)
    unresolved = list(scope.unresolved)
    if not bucket:
        from portfolio_app.leverage_settings import load_leverage_settings
        from portfolio_app.futures_workspace import load_sandbox
        sandbox = load_sandbox(directory)
        settings, _ = load_leverage_settings(directory, future=sandbox[0] if sandbox else None)
        if any(item.id not in journal.adopted_liability_ids for item in settings.liabilities):
            unresolved.append('Adopt legacy liabilities into dated account records before calculating whole-portfolio history')
        if sandbox and not journal.accounts:
            unresolved.append('Legacy futures simulations are not actual transaction history')
    scope = replace(scope, unresolved=tuple(unresolved))
    if not scope.positions and not scope.accounts and not scope.unresolved:
        return None
    key = _key(directory, scope.key)
    observations, revision = load_observations(directory)
    period = st.session_state.get(key+'period') or '1Y'
    from portfolio_app.personal_performance import HistoryCalculation
    calculation = HistoryCalculation(journal, observations)
    view = prepared_history_view(directory,journal,scope,observations,revision,period,target=reporting_currency(),
                                 include_points=include_points,calculation=calculation)
    from portfolio_app.gain_accounting import current_component_values, summarize_known_gain
    current = current_values = None
    if current_valued is not None:
        current_values = current_component_values(current_valued, journal, calculation=calculation)
        current = summarize_known_gain(journal, scope, observations, date.today().isoformat(), reporting_currency(),
            current_values=current_values, summary_rows=current_valued, calculation=calculation)
    return dict(journal=journal, scope=scope, key=key, observations=observations, revision=revision, view=view,
                current_gain=current, current_valued=current_valued, calculation=calculation, current_values=current_values,
                history_signature=(revision, period, date.today()))


def render_portfolio_history(directory, prepared, *, label='Portfolio value', percent=False, on_toggle_gain=None, automatic=False):
    render_history_results(directory, prepared['journal'], prepared['scope'], key=prepared['key'],
        label=label, prepared=prepared, percent=percent, on_toggle_gain=on_toggle_gain, automatic=automatic)


def render_history_results(directory, journal, scope, *, key, label='Historical value', prepared=None, percent=False, on_toggle_gain=None, automatic=False, show_summary=True):
    options = dict(key=key, label=label, percent=percent, on_toggle_gain=on_toggle_gain, show_summary=show_summary)
    if not automatic:
        _render_history_results(directory, journal, scope, prepared=prepared, **options)
        return
    from portfolio_app.currency_display import reporting_currency
    from portfolio_app.personal_history_refresh import refresher
    target = reporting_currency()
    refresh = refresher.request(directory, journal, scope, target)
    cached = prepared
    signature = prepared.get('history_signature') if prepared else None
    waiting = refresh.pending
    if on_toggle_gain is not None:
        def toggle():
            on_toggle_gain()
            options['percent'] = not options['percent']
        options['on_toggle_gain'] = toggle

    from portfolio_app.dialog_runtime import local_dialog_updates
    @st.fragment(run_every=2 if refresh.pending else None)
    @local_dialog_updates()
    def history_region():
        nonlocal cached, signature, refresh, waiting
        if waiting and not refresher.pending(directory):
            # A different scope may have occupied the workspace's single job.
            # Ensure this scope once, then stop scheduling on timer ticks.
            refresh = refresher.request(directory, journal, scope, target)
            waiting = refresh.pending
        observations, revision = load_observations(directory)
        period = st.session_state.get(key+'period') or '1Y'
        current = revision, period, date.today()
        if current != signature:
            from portfolio_app.personal_performance import HistoryCalculation
            calculation = HistoryCalculation(journal, observations)
            cached = dict(prepared or {}, observations=observations, revision=revision,
                calculation=calculation, history_signature=current,
                view=prepared_history_view(directory, journal, scope, observations, revision, period, target=target,
                                           calculation=calculation))
            if cached.get('current_valued') is not None:
                from portfolio_app.gain_accounting import summarize_known_gain, current_component_values
                valued = cached['current_valued']
                cached['current_values'] = current_component_values(valued, journal, calculation=calculation)
                cached['current_gain'] = summarize_known_gain(journal, scope, observations, date.today().isoformat(), target,
                    current_values=cached['current_values'], summary_rows=valued, calculation=calculation)
            signature = current
        _render_history_results(directory, journal, scope, prepared=cached,
            refresh=refresh, automatic=True, include_inputs=False, **options)
    history_region()
    # Keep editable evidence controls outside the polling fragment.
    observations, revision = load_observations(directory)
    _history_inputs(directory, journal, scope, observations, revision,
        cached['view'], target, key)


def _render_history_results(directory, journal, scope, *, key, label='Historical value', prepared=None, percent=False, on_toggle_gain=None, automatic=False, refresh=None, include_inputs=True, show_summary=True):
    from portfolio_app.currency_display import reporting_currency, money
    from portfolio_app.history_view import PERIODS
    from portfolio_app.presentation import return_percent
    target = reporting_currency()
    observations, revision = (prepared['observations'],prepared['revision']) if prepared else load_observations(directory)
    # Reserve the summary above the controls, while reading the widget first.
    summary = st.container()
    period = st.segmented_control('Period', PERIODS, default=st.session_state.get(key+'period') or '1Y',
        required=True, key=key+'period', label_visibility='collapsed') or '1Y'
    view = prepared['view'] if prepared else prepared_history_view(directory,journal,scope,observations,revision,period,target=target)
    start,end,result = view.start,view.end,view.result
    full = view.all_result
    known = (prepared.get('current_gain') if prepared else None) or view.all_known
    from portfolio_app.history_data_ui import readable_reasons, scope_names
    names = scope_names(directory, journal, scope)
    def explanation(reasons):
        messages = readable_reasons(directory, journal, scope, reasons, names=names)
        return ' '.join(messages[:3]) + (' More missing dates are listed in Correct market data.' if len(messages) > 3 else '')
    total_value, capital = known.value, known.capital
    gain = known.amount if view.covers_full_history else result.gain
    twr = full.time_weighted if view.covers_full_history else result.time_weighted
    value_key = 'history_value_'+key
    # Position histories keep a local unit choice; Overview shares its existing
    # display preference with Allocation.
    interaction_key = 'gain'
    if on_toggle_gain is None:
        unit_key = key+'gain_percent'
        percent = st.session_state.get(unit_key, False)
        def on_toggle_gain():
            st.session_state[unit_key] = not st.session_state.get(unit_key, False)
        interaction_key = value_key
    displayed_gain = (100*twr if twr is not None else None) if percent else gain
    gain_text = return_percent(displayed_gain) if percent else (money(gain,signed=True) if gain is not None else '—')
    gain_tooltip = ('TWR · ' if percent else 'Gain · ') + ('All history' if view.covers_full_history else period) + (' · Click for amount' if percent else ' · Click for TWR')
    with summary:
        if show_summary and (total_value is not None or full.ending_value is not None):
            cols = st.columns([2,1,1])
            with cols[0].container(key=value_key):
                st.metric(label, money(total_value), delta=gain_text,
                    delta_color=('inverse' if percent and displayed_gain < 0 else 'normal') if displayed_gain else 'off', delta_arrow='off',
                    help=f"{'TWR' if percent else 'Gain'}: {'all history' if view.covers_full_history else period}. Chart hover: total to date. "
                        + explanation((full if view.covers_full_history else result).time_weighted_reasons if percent else known.reasons))
                if known.partial:
                    st.caption(known.coverage_note, help=known.coverage_help)
            from portfolio_app.metric_interactions import toggle_gain_unit
            toggle_gain_unit(percent=percent, on_toggle=on_toggle_gain, container_key=value_key, interaction_key=interaction_key, tooltip=gain_tooltip)
            with cols[1].container(key=key+'hover_capital'):
                st.metric('Invested capital', money(capital),
                    help=f'Known opening acquisition cost (account opening equity for cash/debt/futures), plus net external contributions. After sales this is not remaining acquisition cost and can be negative. Snapshot market value separately seeds returns. Current basis as of {known.as_of}; unchanged by chart period.')
            cols[2].metric('Annualized return', return_percent(None if full.money_weighted is None else 100*full.money_weighted),
                help=f'All history · {view.all_start} – {view.all_end}. Money-weighted; fixed across periods and hover.'
                    + (f' Annualized from a short interval of {full.days} days.' if full.days < 365 else '')
                    + (' '+explanation(full.money_weighted_reasons) if full.money_weighted is None else ''))
    points = view.points
    if sum(p['Value'] is not None for p in points) >= 2:
        import plotly.graph_objects as go
        from portfolio_app.charts import style_figure, chart_config, PALETTE
        figure = style_figure(go.Figure())
        for name in ('Value', 'Invested capital'):
            if any(p[name] is not None for p in points):
                figure.add_trace(go.Scatter(x=[p['Date'] for p in points], y=[p[name] for p in points], name=name, connectgaps=True,
                    customdata=[[p['Total gain'], p['TWR']] for p in points] if name == 'Value' else None,
                    mode='lines', line=dict(shape='hv' if name == 'Invested capital' else 'linear',
                        dash='dash' if name == 'Invested capital' else 'solid',
                        width=1.5 if name == 'Invested capital' else 2.5,
                        color='#87919f' if name == 'Invested capital' else PALETTE[0])))
        figure.update_layout(yaxis_title=target, height=340, margin=dict(l=0,r=0,t=12,b=0),
            xaxis=dict(range=[view.requested_start,view.today]), legend=dict(orientation='h',y=1.12,x=0),
            meta=dict(historyInspection=dict(currency=target, symbol_money=True, metrics=[
                dict(key=value_key, trace='Value', label=label, summary=money(total_value),
                    gainSummary=gain_text, gainAmount=float(displayed_gain) if displayed_gain is not None else None,
                    gainPercent=percent, gainTooltip=gain_tooltip),
                dict(key=key+'hover_capital', trace='Invested capital', label='Invested capital', summary=money(capital))] if show_summary else [])))
        figure = chart_hover(money_history_axes(figure, target))
        if not show_summary:
            # Keep dated personal figures in the tooltip; position headlines stay
            # on their current/all-history basis in both Price and Holdings.
            trace = next(t for t in figure.data if t.name == 'Value')
            trace.text = [f'Total gain: {money(p["Total gain"], signed=True)}<br>TWR: '
                          + return_percent(None if p['TWR'] is None else 100*p['TWR']) for p in points]
            trace.hovertemplate = trace.hovertemplate.replace('<extra>', '<br>%{text}<extra>')
        st.plotly_chart(figure, key=key+'chart', width='stretch', config=chart_config())
    elif view.useful:
        st.caption('Not enough valuations in this period. Choose a longer period to see history.')
    else:
        if not automatic:
            st.caption('Load historical prices to chart your saved transactions.')
    if automatic:
        _automatic_history_status(directory, journal, scope, target, key, refresh)
    else:
        _load_market_observations(directory, journal, scope, observations, revision, view.all_start, view.today, target, key)
    notes = []
    if view.all_end != view.today:
        notes.append(f'Latest valuation: {view.all_end}')
    if view.missing:
        notes.append('Some valuations missing · lines connect available values')
    if notes:
        st.caption(' · '.join(notes))
    if include_inputs:
        _history_inputs(directory, journal, scope, observations, revision, view, target, key)


def _open_transaction_review(directory, identity):
    st.session_state[f'position_edit_detail_view_{identity}'] = 'Transactions'
    st.session_state[_key(directory, identity)+'screen'] = 'review'


def _history_inputs(directory, journal, scope, observations, revision, view, target, key):
    needs_review = any(r.startswith('Review complete scope') for r in view.all_result.flow_reasons)
    if needs_review:
        if len(scope.positions) == 1 and not scope.accounts and st.session_state.get('position_edit_dialog'):
            st.button('Review transactions', type='tertiary', key=key+'review_transactions',
                on_click=_open_transaction_review, args=(directory, scope.positions[0]))
        else:
            with st.expander('Review history completeness'):
                from portfolio_app.history_coverage_ui import render_coverage_review
                render_coverage_review(directory, journal, scope, key=key)
    from portfolio_app.history_data_ui import render_history_data
    render_history_data(directory, journal, scope, observations, revision, view, target, key)


def _load_market_observations(directory, journal, scope, observations, revision, start, end, target, key):
    from portfolio_app.personal_market import download_scope_observations
    if st.button('Load prices and FX', key=key+'fetch_market',
                 help='Load available dated market observations through today. Transactions and coverage reviews stay unchanged.'):
        try:
            with st.spinner('Loading historical prices and FX…'):
                updated, errors = download_scope_observations(directory, journal, scope, observations, start, end, target)
                if updated != observations:
                    save_observations(directory, updated, revision)
            st.session_state[key+'download_status'] = errors or ['Available observations saved.' if updated != observations else 'No new observations available. Supply missing dates manually.']
            st.rerun()
        except (DataError, ValueError, OSError) as exc:
            _show_error(exc)
    for message in st.session_state.get(key+'download_status', []):
        st.caption(message)


def _automatic_history_status(directory, journal, scope, target, key, refresh):
    from portfolio_app.personal_history_refresh import refresher
    if refresh.pending:
        st.caption('Loading historical prices and FX…')
    elif refresh.errors:
        st.caption('Some historical market data is unavailable', help=' '.join(refresh.errors))
        if st.button('Retry prices', key=key+'fetch_retry'):
            refresher.request(directory, journal, scope, target, force=True)
            st.rerun()
