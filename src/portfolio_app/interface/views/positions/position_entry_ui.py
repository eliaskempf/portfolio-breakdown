"""Compact holding inputs and optional purchase rows for the position dialog."""
from decimal import Decimal

import pandas as pd
import streamlit as st

from portfolio_app.portfolio.physical_assets import METALS, WEIGHT_UNITS
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.interface.shared.currency_display import reporting_currency
from portfolio_app.portfolio.portfolio_settings import REPORTING_CURRENCIES
from portfolio_app.portfolio.position_entry import compact_decimal, decimal_input, linked_costs
from portfolio_app.portfolio.purchases import COLUMNS, summarize_purchases, validate_purchases
from portfolio_app.interface.shared.view_state import persistent_editor


def physical_pricing(prefix, row, *, unit_locked):
    """Keep entered amounts tied to the unit in which the holding is recorded."""
    def clear_unit_amounts():
        for field in ('shares', 'buy_in', 'total_buy_in', 'manual_price', 'from_purchases', 'trade_quantity', 'trade_price', 'trade_fees'):
            st.session_state.pop(prefix + field, None)
            st.session_state.get('position_draft', {}).pop(prefix + field, None)
        for name in ('view_editor_drafts', 'view_editor_bases'):
            st.session_state.get(name, {}).pop(prefix + 'purchase_editor', None)
        st.session_state.pop(prefix + 'purchase_editor', None)
        st.session_state[prefix + 'unit_changed'] = True

    methods = {}
    if not unit_locked or row.get('quantity_unit') in WEIGHT_UNITS:
        methods = {f'{metal.name} spot price': source for source, metal in METALS.items()}
    methods['Manual price'] = ''
    initial_source = row.get('price_source', '') if unit_locked else 'gold_spot'

    def change_valuation_method():
        method = st.session_state.get(prefix + 'valuation_method')
        unit = st.session_state.get(prefix + 'quantity_unit')
        if methods.get(method) and unit not in WEIGHT_UNITS:
            unit = 'troy oz'
            clear_unit_amounts()
        if unit:
            st.session_state[prefix + 'quantity_unit'] = unit
            st.session_state.get('position_draft', {})[prefix + 'quantity_unit'] = unit

    method = st.selectbox('Valuation method', list(methods),
        index=list(methods.values()).index(initial_source), disabled=len(methods) == 1,
        help='Choose the spot quote for the metal you hold, or enter a dated manual price per unit.',
        key=prefix + 'valuation_method', on_change=change_valuation_method)
    price_source = methods[method]

    units = list(WEIGHT_UNITS) if price_source else ['units', *WEIGHT_UNITS]
    saved_unit = row.get('quantity_unit') or units[0]
    if saved_unit not in units:
        units.append(saved_unit)
    unit_help = (f'Enter the fine {METALS[price_source].name.lower()} content. The spot quote excludes dealer premiums.'
                 if price_source else 'Choose a unit or enter your own, such as bottles. Use it for quantity, cost and price.')
    quantity_unit = st.selectbox('Quantity unit', units, index=units.index(saved_unit),
        disabled=unit_locked, accept_new_options=not price_source and not unit_locked,
        key=prefix + 'quantity_unit', on_change=clear_unit_amounts, help=unit_help)
    if st.session_state.get(prefix + 'unit_changed'):
        st.caption('Unit changed. Re-enter the quantity and prices.')
    return price_source, (quantity_unit or '').strip()


def optional_number(value) -> float | None:
    return None if value is None or pd.isna(value) or value == "" else float(value)


def render_manual_price(prefix, row, *, physical=False, quantity_unit=None):
    with st.expander('Current valuation' if physical else 'Manual pricing', expanded=physical):
        manual_price = st.number_input('Current unit price (optional)' if physical else 'Manual unit price (optional)',
            min_value=0., value=optional_number(row.get('manual_price')), key=prefix + 'manual_price',
            help=f'Price for one unit ({quantity_unit}). Leave blank for an unknown value.' if physical else 'Overrides market quotes. Clear to return to provider pricing.')
        manual_currency = st.text_input('Price currency' if physical else 'Manual price currency', help='Currency of the manually entered current unit price.',
                                        value=row.get('manual_price_currency') or (reporting_currency() if physical else ''), key=prefix + 'manual_currency')
        if physical:
            from datetime import date
            initial_date = date.fromisoformat(row['manual_price_date']) if row.get('manual_price_date') else date.today()
            manual_date = st.date_input('Price date', value=initial_date, max_value=date.today(), key=prefix + 'manual_date').isoformat()
        else:
            manual_date = st.text_input('Manual price date (YYYY-MM-DD)', help='Date the manual price was observed, in year-month-day format.', value=row.get('manual_price_date', ''), key=prefix + 'manual_date')

    return manual_price, manual_currency, manual_date


def buy_in_currency(prefix, row, *, editing):
    initial = row.get('acquisition_currency', '') if editing else reporting_currency()
    key = prefix + 'currency'
    st.session_state.setdefault(key, initial)
    current = st.session_state[key]
    choices = list(dict.fromkeys([current, *REPORTING_CURRENCIES]))
    return st.selectbox('Buy-in currency', choices, key=key, accept_new_options=True,
        format_func=lambda value: value or 'Unspecified',
        help='Original currency of purchase costs, including fees. Select or enter a three-letter currency code; foreign costs need a purchase conversion for portfolio gains.').strip().upper()


def purchase_rows(prefix):
    enabled = st.checkbox('Calculate from purchases', key=prefix + 'from_purchases',
        help='Enter the purchases making up this new holding. Quantity and fee-inclusive buy-in are calculated together; sales and splits are not supported.')
    rows = []
    if enabled:
        st.caption('Enter the purchases making up the holding, with no intervening sales or splits. Use the buy-in currency for every price and fee.')
        table = persistent_editor(pd.DataFrame([{column: '' for column in COLUMNS}]),
            key=prefix + 'purchase_editor', num_rows='dynamic', hide_index=True, width='stretch',
            column_config={
                'date': st.column_config.TextColumn('Date (optional)', help='Purchase date as YYYY-MM-DD; future dates are not allowed.'),
                'shares': st.column_config.TextColumn('Quantity', help='Positive units bought in this row; decimal points or commas are accepted.'),
                'price': st.column_config.TextColumn('Unit price', help='Cost per unit in the buy-in currency. Blank means unknown cost.'),
                'fx_rate': st.column_config.TextColumn(f'FX rate to {reporting_currency()} (optional)', help='Reporting-currency units per buy-in currency unit. Otherwise the purchase date is used for historical conversion.'),
                'fees': st.column_config.TextColumn('Fees', help='Total purchase fees for this row in the buy-in currency. Blank means zero.'),
            })
        rows = table.fillna('').to_dict('records')
    return enabled, rows


def holding_amounts(prefix, row, *, editing, from_purchases=False, rows=()):
    """Return quantity, average cost, currency and validation errors."""
    initial = {'shares': compact_decimal(row.get('shares')), 'buy_in': compact_decimal(row.get('acquisition_price'))}
    def amount_input(field, text, label, *, optional=False):
        # Existing CSVs can contain exact values outside the entry widget's
        # limits. Keep them when untouched; validate every changed input.
        if editing and text and text == initial[field]:
            return Decimal(text)
        return decimal_input(text, label, optional=optional)

    initial['total_buy_in'] = compact_decimal(linked_costs(
        amount_input('shares', initial['shares'], 'Quantity', optional=True),
        amount_input('buy_in', initial['buy_in'], 'Average', optional=True), 'average')[1])
    for field, value in initial.items():
        st.session_state.setdefault(prefix + field, value)
    st.session_state.setdefault(prefix + 'cost_source', 'average')

    def synchronize(source=None):
        # A browser callback can arrive after save/cancel cleared the dialog.
        if prefix + 'cost_source' not in st.session_state:
            return
        if source:
            st.session_state[prefix + 'cost_source'] = source
        active = st.session_state[prefix + 'cost_source']
        field = 'buy_in' if active == 'average' else 'total_buy_in'
        other = 'total_buy_in' if active == 'average' else 'buy_in'
        try:
            quantity = amount_input('shares', st.session_state[prefix + 'shares'], 'Quantity', optional=True)
            amount = amount_input(field, st.session_state[prefix + field], 'Buy-in', optional=True)
            average, total = linked_costs(quantity, amount, active)
            st.session_state[prefix + other] = compact_decimal(total if active == 'average' else average)
        except DataError:
            st.session_state[prefix + other] = ''

    def clear_cost():
        if prefix + 'cost_source' not in st.session_state:
            return
        st.session_state[prefix + 'buy_in'] = ''
        st.session_state[prefix + 'total_buy_in'] = ''
        st.session_state[prefix + 'cost_source'] = 'average'

    quantity_column, currency_column = st.columns([3, 1])
    with currency_column:
        currency = buy_in_currency(prefix, row, editing=editing)
    errors = []
    summary = None
    if from_purchases:
        try:
            summary = summarize_purchases(validate_purchases(rows), currency, {})
        except DataError as exc:
            errors.append(str(exc))
        for field, value in {
            'shares': summary.total_shares if summary else None,
            'buy_in': summary.average if summary else None,
            'total_buy_in': summary.batch_cost if summary else None,
        }.items():
            st.session_state[prefix + 'calculated_' + field] = compact_decimal(value)
    display_prefix = prefix + 'calculated_' if from_purchases else prefix
    quantity_text = quantity_column.text_input('Quantity held (total)', key=display_prefix + 'shares', disabled=from_purchases,
            placeholder='e.g. 12.5', on_change=None if from_purchases else synchronize,
            help='Total units currently held, including fractional units. Zero keeps a planned position. Decimal points or commas are accepted.')
    if not from_purchases:
        try:
            quantity = amount_input('shares', quantity_text, 'Quantity')
        except DataError as exc:
            quantity = None
            if quantity_text:
                st.error(str(exc))
            errors.append(str(exc))
    else:
        quantity = summary.total_shares if summary else None
    left, right = st.columns(2)
    with left:
        average_text = st.text_input('Average buy-in per unit (optional)', key=display_prefix + 'buy_in', disabled=from_purchases,
            placeholder='Unknown', on_change=None if from_purchases else synchronize, args=('average',),
            help='Average purchase cost per unit including fees. Editing this recalculates total cost; it stays fixed when quantity changes.')
    with right:
        total_text = st.text_input('Total buy-in (optional)', key=display_prefix + 'total_buy_in', disabled=from_purchases,
            placeholder='Unknown', on_change=None if from_purchases else synchronize, args=('total',),
            help='Cost of the quantity still held, including purchase fees; not lifetime deposits. Editing this recalculates average cost.')
    if from_purchases:
        if errors:
            st.error(errors[0])
        elif summary.average is None:
            st.info('Buy-in remains unknown because a purchase price is missing. The entered purchase records will still be saved.')
        else:
            st.caption(f'Calculated from purchases, including {compact_decimal(summary.fees)} {summary.currency} in fees.')
        average = summary.average if summary else None
    else:
        source = st.session_state[prefix + 'cost_source']
        try:
            field, text = ('buy_in', average_text) if source == 'average' else ('total_buy_in', total_text)
            amount = amount_input(field, text, 'Buy-in', optional=True)
            average, _ = linked_costs(quantity, amount, source)
        except DataError as exc:
            average = None
            errors.append(str(exc))
            st.error(str(exc))
        explanation, clear = st.columns([5, 1])
        explanation.caption('Enter either cost; the other updates automatically. ' + ('Average per unit' if source == 'average' else 'Total buy-in') + ' stays fixed when quantity changes.')
        clear.button('Clear buy-in', help='Remove both cost amounts; the holding remains saved with unknown purchase cost.', type='tertiary', key='position_clear_cost_' + prefix, on_click=clear_cost)
    return quantity, average, currency, errors
