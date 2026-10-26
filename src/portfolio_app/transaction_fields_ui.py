"""Economic event fields shared by manual entry and correction of any source."""
from datetime import date
from decimal import Decimal

import streamlit as st

from portfolio_app.cost_basis import component
from portfolio_app.holdings import DataError
from portfolio_app.security_history import AssetCharge, Charge

KINDS = {'swap', 'reward', 'asset_fee', 'transfer', 'transfer_in', 'transfer_out',
         'inventory_in', 'inventory_out', 'checkpoint'}


def order_input(value, key):
    # Legacy timestamp orders exceed JavaScript's safe integer range.
    raw = st.text_input('Order within date', value=str(value), key=key)
    if not raw.isdigit():
        raise DataError('Order within date must be a nonnegative whole number.')
    return int(raw)


def noncash_fields(event, snapshot, book, key):
    from portfolio_app.history_ui import _decimal
    left, right = st.columns(2)
    stamp = left.date_input('Transaction date', value=date.fromisoformat(event.date),
                           max_value=date.today(), key=key+'date')
    quantity = right.text_input('Quantity', value=str(event.quantity) if event.quantity else '', key=key+'quantity')
    fields = dict(date=stamp.isoformat(), quantity=_decimal(quantity))
    names = {r.position_id: r['name']+' · '+r.account for _, r in snapshot.holdings.iterrows()}
    if event.kind in ('swap', 'transfer'):
        options = [p for p in names if p != book.position_id]
        instruments = snapshot.holdings.set_index('position_id')['id'].to_dict()
        options = [p for p in options if (instruments[p] == book.instrument_id) == (event.kind == 'transfer')]
        if not options:
            raise DataError('Add the other holding before recording this movement.')
        fields['other_position_id'] = st.selectbox('Receiving holding', options,
            index=options.index(event.other_position_id) if event.other_position_id in options else 0,
            format_func=names.get, key=key+'destination')
        if event.kind == 'swap':
            fields['received_quantity'] = _decimal(st.text_input('Received quantity',
                value=str(event.received_quantity) if event.received_quantity else '', key=key+'received'))
    if event.kind in ('swap', 'reward', 'transfer_in', 'transfer_out', 'inventory_in', 'inventory_out'):
        left, right = st.columns([3,1])
        amount = event.valuation_amount if event.valuation_amount is not None else event.amount
        fields['valuation_amount'] = _decimal(left.text_input('Monetary value', value=str(amount) if amount is not None else '',
            placeholder='Blank if unknown', key=key+'valuation'), optional=True)
        fields['valuation_currency'] = right.text_input('Currency', value=event.valuation_currency or event.currency or 'EUR', key=key+'currency').upper()
        if event.kind in ('transfer_in', 'transfer_out'):
            fields.update(amount=fields['valuation_amount'], currency=fields['valuation_currency'])
    if event.kind in ('transfer_in', 'inventory_in', 'checkpoint'):
        with st.expander('Acquisition cost'):
            costs = event.costs
            if len(costs) > 1 or event.lots:
                st.caption('Existing lot details are preserved. Use the starting-lots editor to change individual lots.')
                if fields['quantity'] != event.quantity:
                    raise DataError('Adjust the individual lot quantities before changing this balance.')
            else:
                cost = costs[0] if costs else {}
                value = st.text_input('Total acquisition cost', value=cost.get('amount', ''), key=key+'cost')
                currency = st.text_input('Cost currency', value=cost.get('currency') or 'EUR', key=key+'cost_currency').upper()
                part = component(fields['quantity'], _decimal(value, optional=True), currency)
                if cost.get('quality') == 'estimated' and value:
                    part.update(quality='estimated', source=cost.get('source') or 'Supplied transaction')
                fields['costs'] = (part,)
    if event.kind in ('inventory_in', 'inventory_out'):
        fields['flow_classification'] = st.selectbox('Movement', ['unresolved', 'external'],
            index=0 if event.flow_classification == 'unresolved' else 1,
            format_func=lambda v: 'Purpose unknown' if v == 'unresolved' else 'Transfer across the portfolio boundary', key=key+'flow')
    with st.expander('Fees and details'):
        charges = []
        defaults = event.charges or (Charge(Decimal(0) if event.charges_known else None, event.valuation_currency or event.currency or 'EUR'),)
        for i, charge in enumerate(defaults):
            left, right = st.columns([3,1])
            value = left.text_input('Fee' if len(defaults) == 1 else f'{charge.kind.title()} {i+1}',
                value=str(charge.amount) if charge.amount is not None else '', key=key+'fee'+str(i))
            currency = right.text_input('Fee currency', value=charge.currency, key=key+'fee_currency'+str(i)).upper()
            from dataclasses import replace
            charges.append(replace(charge, amount=_decimal(value, optional=True), currency=currency))
        fields['charges_known'] = all(c.amount is not None for c in charges)
        fields['charges'] = tuple(c for c in charges if c.amount != 0)
        fields['asset_charges'] = asset_fee_fields(event, snapshot, key)
        fields['estimated'] = st.checkbox('Estimated amounts', value=event.estimated or event.valuation_quality == 'estimated', key=key+'estimated')
        fields['sequence'] = order_input(event.sequence, key+'sequence')
        fields['note'] = st.text_input('Note', value=event.note, key=key+'note')
    if fields.get('valuation_amount') is not None:
        fields['valuation_quality'] = 'estimated' if fields['estimated'] else 'recorded'
        fields['valuation_source'] = event.valuation_source or 'Supplied transaction'
    else:
        fields.update(valuation_quality='recorded', valuation_source='')
    return fields


def asset_fee_fields(event, snapshot, key):
    from portfolio_app.history_ui import _decimal
    names = {r.position_id: r['name']+' · '+r.account for _, r in snapshot.holdings.iterrows()}
    asset_fees = []
    for i, charge in enumerate(event.asset_charges):
        st.caption('Fee paid from '+names.get(charge.position_id, charge.position_id))
        amount = _decimal(st.text_input('Fee units', value=str(charge.quantity), key=key+'asset_fee'+str(i)))
        if amount:
            asset_fees.append(AssetCharge(charge.position_id, amount))
    if names and st.checkbox('Additional fee paid in units', key=key+'extra_asset_fee'):
        payer = st.selectbox('Fee holding', list(names), format_func=names.get, key=key+'fee_payer')
        amount = _decimal(st.text_input('Additional fee units', key=key+'extra_units'))
        if amount:
            asset_fees.append(AssetCharge(payer, amount))
    return tuple(asset_fees)


def render_noncash_form(directory, journal, revision, snapshot, book, event, key, *, editing, return_key):
    from dataclasses import replace
    from uuid import uuid4
    from portfolio_app.history_commands import correct_transaction, save_history
    from portfolio_app.security_history import append_evidence
    from portfolio_app.history_ui import _history_action, _capture_submit_revision
    try:
        fields = noncash_fields(event, snapshot, book, key)
        candidate = (correct_transaction(book, event.id, **fields) if editing else
                     append_evidence(book, (replace(event, id=uuid4().hex, **fields),)))
        with st.container(horizontal=True):
            if st.button('Save changes' if editing else 'Save transaction', type='primary', key=key+'save',
                         on_click=_capture_submit_revision, args=(key, revision, snapshot.revision)):
                expected = st.session_state.pop(key+'submitted_revision')
                save_history(directory, candidate, expected_journal_revision=expected[0], expected_holdings_revision=expected[1])
                _history_action(return_key, 'list')
                st.rerun()
            st.button('Cancel', key=key+'cancel', on_click=_history_action, args=(return_key,'list'))
    except (DataError, ValueError) as exc:
        st.caption(str(exc))
