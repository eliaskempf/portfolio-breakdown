"""Create and maintain summary positions without transaction accounting."""

from portfolio_app.currency_display import reporting_currency

from pathlib import Path

import pandas as pd
import streamlit as st

from portfolio_app.position_entry_ui import holding_amounts, purchase_rows
from portfolio_app.purchases import save_purchase_batch, read_opening
from portfolio_app.position_entry import compact_decimal, decimal_input
from portfolio_app.display_names import display_name, instrument_name
from portfolio_app.etf import FundSnapshot, validate_fund_listings
from portfolio_app.holdings import DataError, metadata_dimensions
from portfolio_app.instrument_ui import render_instrument_search
from portfolio_app.position_list import list_context, render_position_list
from portfolio_app.strategic import category_labels
from portfolio_app.positions import HoldingsSnapshot, save_position, read_snapshot
from portfolio_app.purchase_ui import render_bulk_purchases, render_purchase_history


def _optional_number(value) -> float | None:
    return None if value is None or pd.isna(value) or value == "" else float(value)


def _clear_editor() -> None:
    for key in list(st.session_state):
        if key.startswith("position_edit_"):
            del st.session_state[key]
    for name in ('view_editor_drafts', 'view_editor_bases'):
        drafts = st.session_state.get(name, {})
        for key in list(drafts):
            if key.startswith('position_edit_fields_'):
                del drafts[key]
    st.session_state.pop('position_draft', None)
    st.session_state.pop('strategic_last_position', None)


def request_position(position_id, *, editing=False):
    if st.session_state.get('position_edit_selected') != position_id:
        _clear_editor()
    st.session_state.update(position_edit_selected=position_id,
        position_edit_action='Edit position' if editing else 'Details', position_edit_dialog=True)


def _consume_position_event(path, snapshot):
    if event := st.session_state.pop('position_edit_open_request', None):
        if (event.get('context') == list_context(path) and event.get('revision') == snapshot.revision
                and event.get('id') in set(snapshot.holdings.position_id)):
            request_position(event['id'], editing=event.get('action', 'edit') == 'edit')
        else:
            st.warning('The position list changed. Open the position again from the refreshed list.')


def render_position_editor(path, snapshot, funds, *, demo=False, embedded=False, allocation=None,
                           valued=None, percent=False, defer_dialog=False):
    _consume_position_event(path, snapshot)
    if warning := st.session_state.pop('position_stale_notice', None):
        st.warning(warning)
    if message := st.session_state.pop('position_saved_notice', None):
        st.success(message)
    action, secondary = st.columns([1, 3])
    if action.button('Add position', help='Create a holding in the active portfolio; nothing is saved until you choose Save position.', type='primary', icon=':material/add:', key='tour_add_position'):
        _clear_editor()
        st.session_state.update(position_edit_action='Add position', position_edit_dialog=True)
    if st.session_state.get('position_draft') or st.session_state.get('position_edit_action') in {'Edit position', 'Add position'}:
        if secondary.button('Resume unsaved edit', help='Reopen the position draft kept in this session.'):
            # Reattach detached widget values even when their state keys remain.
            for key, value in st.session_state.get('position_draft', {}).items():
                st.session_state[key] = value
            st.session_state['position_edit_dialog'] = True
    # Apply navigation after remount: an old browser widget value can otherwise
    # restore the import form after a successful save switches to Overview.
    if requested := st.session_state.pop('positions_workflow_request', None):
        st.session_state['positions_workflow'] = requested
    workflow = st.segmented_control('Position tools', ['Positions', 'Bulk add purchases', 'Update balances', 'Import portfolio', 'Connect live prices'], help='Choose between individual holdings, purchase batches, balance replacement and imports.',
                                    default=st.session_state.get('positions_workflow', 'Positions'),
                                    key='positions_workflow', on_change=_dismiss)
    if workflow == 'Import portfolio':
        from portfolio_app.import_ui import render_import
        render_import(path, snapshot, funds)
    elif workflow == 'Connect live prices':
        from portfolio_app.import_ui import render_listing_link
        render_listing_link(path, snapshot, funds, demo=demo)
    elif workflow == 'Update balances':
        from portfolio_app.allocation_ui import render_balances
        render_balances(path, snapshot)
    elif workflow == 'Bulk add purchases':
        notice = render_bulk_purchases(path, snapshot, funds, demo=demo)
        if notice is not None:
            _clear_editor()
            if notice:
                st.session_state['position_saved_notice'] = notice
            st.rerun()
    else:
        if snapshot.holdings.empty:
            st.info('Add your first position or import a holdings file to see its value and allocation.')
            def start_import():
                st.session_state['positions_workflow'] = 'Import portfolio'
            st.button('Import portfolio — experimental', help='Review a holdings file before saving its positions; supported formats are still experimental.', on_click=start_import)
        else:
            st.caption('Select a position for details and price history. Use the pencil to edit.')
            with st.container(key='tour_position_list'):
                render_position_list(path, snapshot, allocation, valued=valued, percent=percent, demo=demo)
    if not defer_dialog:
        render_position_dialog(path, snapshot, funds, demo=demo, allocation=allocation, valued=valued)


def _remember_draft():
    if st.session_state.get('position_edit_action') in {'Edit position', 'Add position'}:
        fields = {k: v for k, v in st.session_state.items()
                  if (k.startswith('position_edit_fields_') and not k.endswith('purchase_editor')) or k == 'position_edit_instrument'}
        # On a dismiss callback Streamlit may already have detached the dialog's
        # widget state. Don't replace its last complete draft with an empty one.
        if fields:
            st.session_state['position_draft'] = fields


def _dismiss():
    _remember_draft()
    st.session_state['position_edit_dialog'] = False


def render_position_dialog(path, snapshot, funds, *, demo=False, allocation=None, valued=None):
    _consume_position_event(path, snapshot)
    if not st.session_state.get('position_edit_dialog'):
        return

    title = st.session_state.get('position_edit_action', 'Details')
    title = 'Position details' if title == 'Details' else title
    @st.dialog(title, width='large', on_dismiss=_dismiss)
    def dialog():
        # Button callbacks run before the dialog body. Never execute its data
        # loaders again when the user has already requested dismissal.
        if not st.session_state.get('position_edit_dialog'):
            st.rerun()
        current = read_snapshot(path)
        for key, value in st.session_state.get('position_draft', {}).items():
            if key not in st.session_state:
                st.session_state[key] = value
        action = st.session_state.get('position_edit_action', 'Details')
        if action == 'Details':
            selected = current.holdings.loc[current.holdings.position_id.eq(st.session_state.get('position_edit_selected'))]
            if selected.empty:
                st.warning('This position no longer exists.')
            else:
                row = selected.iloc[0]
                if valued is not None and current.revision == snapshot.revision:
                    matches = valued.loc[valued.position_id.eq(row.position_id)]
                    if not matches.empty:
                        row = matches.iloc[0]
                if st.button('Edit position', help='Change the saved quantity, costs, classification or target for this holding.', icon=':material/edit:'):
                    st.session_state['position_edit_action'] = 'Edit position'
                    st.rerun()
                from portfolio_app.position_detail import render_position_detail
                render_position_detail(row, path.parent, demo=demo, allocation=allocation)
            st.button('Close', help='Close this detail view and return to the portfolio.', on_click=_dismiss)
        else:
            rendered = render_position_form(path, current, funds, demo=demo, allocation=allocation, action=action)
            _remember_draft()
            if not rendered and st.button('Cancel', help='Discard this position draft without saving.'):
                _clear_editor()
                st.rerun()
    dialog()


def render_position_form(path, snapshot, funds, *, demo=False, allocation=None, action='Add position'):
    holdings = snapshot.holdings
    editing = action == "Edit position"
    position_id = None
    if editing:
        if holdings.empty:
            st.info("Create your first position before editing.")
            return
        position_id = st.session_state.get('position_edit_selected')
        if position_id not in set(holdings.position_id):
            st.warning('This position no longer exists. Close and reopen the list.')
            return
        row = holdings.loc[holdings["position_id"] == position_id].iloc[0].to_dict()
        identity = row["id"]
        raw = read_opening(path, snapshot.revision, position_id)
        row.update({key: raw.get(key, "") for key in ("shares", "acquisition_price")})
        render_purchase_history(row)
        if row.get('balance_replaced_at'):
            st.caption('The current quantity was replaced from a balance snapshot. Retained purchase batches are not a complete ledger.')
    else:
        labels = {row.id: f"{display_name(row.name)} ({row.ticker or row.id.upper()})" for row in holdings.itertuples()}
        instrument_column, kind_column = st.columns([2, 1]) if labels else (st, st)
        if pending := st.session_state.pop("position_edit_pending_instrument", None):
            if pending in labels:
                st.session_state["position_edit_instrument"] = pending
        identity = instrument_column.selectbox("Existing instrument", ["", *labels], help='Reuse a saved instrument identity when adding a position in another account or category.', format_func=lambda value: labels[value] if value else "New instrument", key="position_edit_instrument") if labels else ""
        row = holdings.loc[holdings["id"] == identity].iloc[0].to_dict() if identity else {}
        row = {column: row.get(column, "") for column in ("id", "name", "ticker", "isin", "instrument_type", "exposure_kind", "quantity_unit", "short_name", "price_source")}
    physical = row.get('instrument_type') == 'physical' and not row.get('ticker') and bool(row.get('quantity_unit'))
    if not editing and not identity:
        entry = kind_column.segmented_control('Position type', ['Listed investment', 'Physical asset'], help='Choose a listed security or a physical holding measured in units or weight.', default='Listed investment',
                                     key='position_edit_fields_entry_kind') or 'Listed investment'
        physical = entry == 'Physical asset'
    context = (str(path.resolve()), action, position_id, identity, physical)
    if st.session_state.get("position_edit_context") != context:
        st.session_state["position_edit_context"] = context
        st.session_state["position_edit_revision"] = snapshot.revision
    if st.session_state["position_edit_revision"] != snapshot.revision:
        st.warning("Holdings changed while this form was open. Reload the form to edit the latest data.")
        if st.button("Reload position form", help='Discard this stale draft and load the latest saved holding.'):
            selected = st.session_state.get('position_edit_selected')
            _clear_editor()
            st.session_state.update(position_edit_selected=selected, position_edit_action=action, position_edit_dialog=True)
            st.rerun()
        return
    # Separate keys keep values tied to the selected position, not another row.
    prefix = f"position_edit_fields_{action}_{position_id}_{identity}_{physical}_"
    if not editing:
        for field, value in st.session_state.pop('position_edit_pending_fields', {}).items():
            st.session_state[prefix + field] = value
        pending_rows = st.session_state.pop('position_edit_pending_purchases', None)
        if pending_rows is not None:
            st.session_state.setdefault('view_editor_drafts', {})[prefix + 'purchase_editor'] = pending_rows
    if not editing and not identity and not physical:
        search_open = st.session_state.setdefault(prefix + 'search_open', True)
        manual = st.session_state.get(prefix + 'manual_entry', False)
        if search_open and not manual:
            render_instrument_search(prefix, holdings, path.parent / '.cache' / 'yahoo', disabled=demo, compact=True)
            if st.button('Enter manually', help='Enter an instrument without using search, including holdings without a live ticker.'):
                st.session_state[prefix + 'manual_entry'] = True
                st.rerun()
        else:
            if st.button('Search investments' if manual else 'Change investment', help='Choose another listing; your quantity, costs and allocation draft are retained.'):
                st.session_state[prefix + 'search_open'] = True
                st.session_state[prefix + 'manual_entry'] = False
                st.rerun()
    if st.session_state.get('onboarding_step') == 'position':
        st.caption('2 of 2 · Add your first position')
        st.write('Choose what you own, enter the quantity, and select its category. Buy-in and target details can wait.')
    def clear_unit_amounts():
        for field in ('shares', 'buy_in', 'total_buy_in', 'manual_price', 'from_purchases'):
            st.session_state.pop(prefix + field, None)
            st.session_state.get('position_draft', {}).pop(prefix + field, None)
        for name in ('view_editor_drafts', 'view_editor_bases'):
            st.session_state.get(name, {}).pop(prefix + 'purchase_editor', None)
        st.session_state.pop(prefix + 'purchase_editor', None)
        st.session_state[prefix + 'unit_changed'] = True
    price_source = row.get('price_source', '')
    if physical:
        def change_valuation_method():
            from portfolio_app.physical_assets import GOLD_WEIGHT_UNITS
            # A queued browser event can arrive after saving cleared the dialog.
            if (not editing and not identity and st.session_state.get(prefix + 'valuation_method') == 'Gold spot price'
                    and st.session_state.get(prefix + 'quantity_unit') not in GOLD_WEIGHT_UNITS):
                st.session_state.pop(prefix + 'quantity_unit', None)
                st.session_state.get('position_draft', {}).pop(prefix + 'quantity_unit', None)
                clear_unit_amounts()
        live_gold = st.radio('Valuation method', ['Gold spot price', 'Manual price'], help='Use live gold spot pricing or an optional dated manual price per unit.', horizontal=True,
            index=0 if price_source == 'gold_spot' or not (editing or identity) else 1,
            key=prefix + 'valuation_method', on_change=change_valuation_method) == 'Gold spot price'
        price_source = 'gold_spot' if live_gold else ''
    manual_entry = st.session_state.get(prefix + 'manual_entry', False)
    name = st.session_state.get(prefix + 'name', row.get('name', ''))
    ticker = st.session_state.get(prefix + 'ticker', row.get('ticker', ''))
    identity_ready = bool(name) or manual_entry or physical or editing or bool(identity)
    if identity_ready:
        name_column, ticker_column = st.columns([3, 2]) if not physical else (st, st)
        name = name_column.text_input('Instrument name', value=row.get('name') or ('Physical gold' if physical else ''),
            disabled=bool(identity) and not editing, key=prefix + 'name',
            help='Edit the name shown in your portfolio. Renaming a saved instrument applies to all its account positions.')
        if not physical:
            ticker = ticker_column.text_input('Ticker', value=row.get('ticker', ''),
                disabled=bool(identity) or not manual_entry, key=prefix + 'ticker',
                help='Exchange-qualified symbol used to retrieve prices. Use Change investment to select another listing. '
                     'For saved holdings, use Connect live prices to review a listing change.')
    if physical:
        ticker, isin = '', ''

    if physical:
        from portfolio_app.physical_assets import GOLD_WEIGHT_UNITS
        units = list(GOLD_WEIGHT_UNITS) if live_gold else list(dict.fromkeys([*GOLD_WEIGHT_UNITS, 'units', row.get('quantity_unit') or 'troy oz']))
        if row.get('quantity_unit') and row['quantity_unit'] not in units:
            st.info('This holding has no supported gold weight unit. Keep manual pricing or create a gold holding with a known weight.')
            return
        quantity_unit = st.selectbox('Quantity unit', units, help='Unit used consistently for quantity, purchase cost and current price.', index=units.index(row.get('quantity_unit') or 'troy oz'),
                                    disabled=editing or bool(identity), key=prefix + 'quantity_unit', on_change=clear_unit_amounts)
        if st.session_state.get(prefix + 'unit_changed'):
            st.caption('Unit changed. Re-enter the quantity and prices; amounts are not converted.')
        st.caption('Enter the fine-gold weight in troy ounces, grams or kilograms. Buy-in amounts must use the selected unit.'
                   if live_gold else 'Quantity, manual price and buy-in must use the same unit.')

    amounts_slot = st.container()
    category_column, target_column = st.columns(2)
    bucket = ''
    with category_column:
        if allocation:
            bucket_ids = ['', *sorted(allocation.leaves())]
            bucket_names = category_labels(allocation)
            bucket = st.selectbox('Category', bucket_ids, index=bucket_ids.index(row.get('bucket_id', '')),
                key=prefix + 'bucket', format_func=lambda value: bucket_names.get(value, 'Unassigned'),
                help='Category owning this holding. The position target beside it is a share of this category.')
        else:
            st.caption('Add categories in Rebalance → Targets.')
    with target_column:
        target_field = 'within_bucket_target' if allocation else 'target_allocation'
        initial_target = _optional_number(row.get(target_field))
        target = st.number_input('Target within category % (optional)' if allocation else 'Target allocation % (optional)',
            min_value=0.0, max_value=100.0, value=None if initial_target is None else initial_target * 100,
            key=prefix + 'target', help='Share of the selected category, not the whole portfolio. Blank means undecided.' if allocation else
            'Share of the whole portfolio. Blank means undecided.')

    def render_manual_price():
        manual_price, manual_currency, manual_date = None, '', ''
        if not physical or not live_gold:
            with st.expander('Current valuation' if physical else 'Manual pricing', expanded=physical):
                manual_price = st.number_input(f'Current price per {quantity_unit} (optional)' if physical else 'Manual unit price (optional)',
                    min_value=0., value=_optional_number(row.get('manual_price')), key=prefix + 'manual_price',
                    help='Leave blank to track quantity with an unknown value.' if physical else 'Overrides market quotes. Clear to return to provider pricing.')
                manual_currency = st.text_input('Price currency' if physical else 'Manual price currency', help='Currency of the manually entered current unit price.',
                                                value=row.get('manual_price_currency') or (reporting_currency() if physical else ''), key=prefix + 'manual_currency')
                if physical:
                    from datetime import date
                    initial_date = date.fromisoformat(row['manual_price_date']) if row.get('manual_price_date') else date.today()
                    manual_date = st.date_input('Price date', value=initial_date, max_value=date.today(), key=prefix + 'manual_date').isoformat()
                else:
                    manual_date = st.text_input('Manual price date (YYYY-MM-DD)', help='Date the manual price was observed, in year-month-day format.', value=row.get('manual_price_date', ''), key=prefix + 'manual_date')

        return manual_price, manual_currency, manual_date

    if physical and not live_gold:
        manual_price, manual_currency, manual_date = render_manual_price()
    else:
        manual_price, manual_currency, manual_date = None, '', ''
    from_purchases, purchases = False, []
    with st.expander('More details'):
        if not physical:
            isin = st.text_input('ISIN (optional)', value=row.get('isin', ''),
                disabled=bool(identity) or not manual_entry, key=prefix + 'isin',
                help='Optional security identifier retained from search. Prices use the ticker, so a missing ISIN does not prevent live pricing.')
        account = st.text_input('Storage location (optional)' if physical else 'Account / broker', value=row.get('account', ''), key=prefix + 'account',
            help='Optional location or account label. Use separate positions for holdings in different accounts.')
        if not physical:
            manual_price, manual_currency, manual_date = render_manual_price()
        if not editing:
            from_purchases, purchases = purchase_rows(prefix)
        short_name = st.text_input('Short display name (optional)', help='Optional shorter label for charts and lists; the full instrument name is retained.', value=row.get('short_name', ''), key=prefix + 'short_name')
        portfolio = st.text_input('Portfolio / sleeve', help='Optional label for grouping holdings into separate portfolios or strategies.', value=row.get('portfolio', ''), key=prefix + 'portfolio')
        if physical:
            instrument_type, exposure_kind = 'physical', 'non_equity'
        else:
            kinds = ['unknown', 'equity', 'etf', 'etc', 'crypto', 'physical', 'cash', 'other']
            instrument_type = st.selectbox('Instrument type', kinds, help='Legal or product type of the holding, separate from the assets it contains.', index=kinds.index(row.get('instrument_type')) if row.get('instrument_type') in kinds else 0, key=prefix + 'instrument_type')
            exposure_kinds = ['unknown', 'equity', 'non_equity']
            exposure_kind = st.selectbox('Underlying exposure', exposure_kinds, help='Leave mixed or uncertain composition unknown.',
                                         index=exposure_kinds.index(row.get('exposure_kind')) if row.get('exposure_kind') in exposure_kinds else 0,
                                         format_func=lambda value: {'unknown': 'Unknown / mixed', 'equity': 'Equity', 'non_equity': 'Non-equity'}[value],
                                         key=prefix + 'exposure_kind')
            quantity_unit = st.text_input('Quantity unit', help='Required for a manual unit price, e.g. shares or units.', value=row.get('quantity_unit', ''), key=prefix + 'quantity_unit')
        extras = {}
        for column in metadata_dimensions(holdings):
            if column not in {'portfolio', 'account', 'bucket_id', 'instrument_type', 'exposure_kind'}:
                extras[column] = st.text_input(column.replace('_', ' ').title(), help='Optional saved classification or grouping label for this position.', value=row.get(column, ''), key=prefix + 'extra_' + column)
    with amounts_slot:
        shares, buy_in, currency, amount_errors = holding_amounts(prefix, row, editing=editing, from_purchases=from_purchases, rows=purchases)
    from portfolio_app.cost_basis import (FIELD, active_components, aggregate_component, encode_components,
        fingerprint, same_summary, resolve_cost)
    from portfolio_app.currency_ui import cost_conversion_controls, historical_for, ui_prices, render_fx_progress
    from portfolio_app.purchases import purchase_components, validate_purchases
    parts, conversion_error = [], None
    historical = historical_for(path.parent, demo=demo)
    if not amount_errors:
        try:
            if from_purchases:
                parts = purchase_components(validate_purchases(purchases), currency, {}, reporting_currency=reporting_currency())
                resolved = resolve_cost(parts, reporting_currency(), historical)
                if resolved.amount is None:
                    st.warning(resolved.note + ' Available current values remain included.')
                else:
                    st.caption(f'Combined buy-in: {resolved.amount:,.2f} {reporting_currency()} · {resolved.note or "Recorded cost"}')
                render_fx_progress(path.parent)
            else:
                cost_row = dict(shares=shares, acquisition_price=buy_in,
                                acquisition_currency=currency if buy_in is not None else '')
                unchanged_cost = editing and same_summary(fingerprint(row), fingerprint(cost_row))
                parts = active_components(row) if unchanged_cost else [aggregate_component(cost_row)]
                if len(parts) > 1:
                    st.caption('Original purchase components are preserved. Changing quantity or buy-in replaces their active cost basis. Use Bulk add purchases to add purchases.')
                with amounts_slot:
                    parts = cost_conversion_controls(parts, reporting_currency(), path.parent, ui_prices(path.parent, demo),
                        key=prefix + reporting_currency() + str(fingerprint(cost_row)) + '_conversion_', demo=demo)
        except DataError as exc:
            conversion_error = str(exc)
            st.warning(conversion_error)
    save, cancel = st.columns(2)
    submitted = save.button('Save position', help='Save this holding and its optional purchase records together in the active workspace.', type='primary', width='stretch')
    if cancel.button('Finish later' if st.session_state.get('onboarding_step') == 'position' else 'Cancel', help='Discard this draft and leave setup without saving.', width='stretch'):
        _clear_editor()
        st.session_state['onboarding_step'] = 'done'
        st.rerun()
    if submitted:
        values = {
            "id": identity, "name": name, "short_name": short_name, "ticker": ticker, "isin": isin,
            "shares": str(shares), "portfolio": portfolio, "account": account,
            "acquisition_price": "" if buy_in is None else str(buy_in),
            "acquisition_currency": currency if buy_in is not None else "",
            target_field: "" if target is None else str(target / 100),
            'instrument_type': instrument_type, 'exposure_kind': exposure_kind, 'quantity_unit': quantity_unit,
            'manual_price': '' if manual_price is None else str(manual_price),
            'manual_price_currency': manual_currency.upper(), 'manual_price_date': manual_date,
            'price_source': price_source,
            **({'bucket_id': bucket} if allocation else {}), **extras,
        }
        try:
            if amount_errors:
                raise DataError(amount_errors[0])
            new_buy_in = not editing or buy_in != decimal_input(compact_decimal(row.get("acquisition_price")), "Buy-in", optional=True)
            if buy_in is not None and not currency.strip() and new_buy_in:
                raise DataError("Enter the currency of the buy-in price, for example EUR.")
            if conversion_error:
                raise DataError(conversion_error)
            if from_purchases:
                asset_id = save_purchase_batch(path, values, purchases, currency=currency,
                    expected_revision=st.session_state['position_edit_revision'],
                    reporting_currency=reporting_currency(), historical=historical, components=parts,
                    validate=lambda frame: validate_fund_listings(frame, funds))
            else:
                values[FIELD] = encode_components(parts, values)
                asset_id = save_position(
                    path, values, expected_revision=st.session_state['position_edit_revision'], position_id=position_id,
                    validate=lambda frame: validate_fund_listings(frame, funds),
                )
        except (DataError, OSError, UnicodeError) as exc:
            st.error(f"Position was not saved: {exc}")
        else:
            _clear_editor()
            for key in list(st.session_state):
                if key.startswith("filter_"):
                    del st.session_state[key]
            st.session_state["position_saved_notice"] = f"Saved {name}."
            st.session_state["onboarding_step"] = "done"
            st.rerun()
    return True
