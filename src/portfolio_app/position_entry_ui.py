"""Compact holding inputs and optional purchase rows for the position dialog."""
import pandas as pd
import streamlit as st

from portfolio_app.holdings import DataError
from portfolio_app.currency_display import reporting_currency
from portfolio_app.portfolio_settings import REPORTING_CURRENCIES
from portfolio_app.position_entry import compact_decimal, decimal_input, linked_costs
from portfolio_app.purchases import COLUMNS, summarize_purchases, validate_purchases
from portfolio_app.view_state import persistent_editor


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
    initial['total_buy_in'] = compact_decimal(linked_costs(
        decimal_input(initial['shares'], 'Quantity', optional=True),
        decimal_input(initial['buy_in'], 'Average', optional=True), 'average')[1])
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
            quantity = decimal_input(st.session_state[prefix + 'shares'], 'Quantity', optional=True)
            amount = decimal_input(st.session_state[prefix + field], 'Buy-in', optional=True)
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
            quantity = decimal_input(quantity_text, 'Quantity')
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
            amount = decimal_input(average_text if source == 'average' else total_text, 'Buy-in', optional=True)
            average, _ = linked_costs(quantity, amount, source)
        except DataError as exc:
            average = None
            errors.append(str(exc))
            st.error(str(exc))
        explanation, clear = st.columns([5, 1])
        explanation.caption('Enter either cost; the other updates automatically. ' + ('Average per unit' if source == 'average' else 'Total buy-in') + ' stays fixed when quantity changes.')
        clear.button('Clear buy-in', help='Remove both cost amounts; the holding remains saved with unknown purchase cost.', type='tertiary', key='position_clear_cost_' + prefix, on_click=clear_cost)
    return quantity, average, currency, errors
