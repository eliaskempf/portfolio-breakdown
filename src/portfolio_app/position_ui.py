"""Create and maintain summary positions without transaction accounting."""

from pathlib import Path

import pandas as pd
import streamlit as st

from portfolio_app.costs import average_from_total
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
    if action.button('Add position', type='primary', icon=':material/add:'):
        _clear_editor()
        st.session_state.update(position_edit_action='Add position', position_edit_dialog=True)
    if st.session_state.get('position_draft') or st.session_state.get('position_edit_action') in {'Edit position', 'Add position'}:
        if secondary.button('Resume unsaved edit'):
            # Reattach detached widget values even when their state keys remain.
            for key, value in st.session_state.get('position_draft', {}).items():
                st.session_state[key] = value
            st.session_state['position_edit_dialog'] = True
    # Apply navigation after remount: an old browser widget value can otherwise
    # restore the import form after a successful save switches to Overview.
    if requested := st.session_state.pop('positions_workflow_request', None):
        st.session_state['positions_workflow'] = requested
    workflow = st.segmented_control('Position tools', ['Positions', 'Bulk add purchases', 'Update balances', 'Import portfolio', 'Connect live prices'],
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
            st.button('Import portfolio — experimental', on_click=start_import)
        else:
            st.caption('Select a position for details and price history. Use the pencil to edit.')
            render_position_list(path, snapshot, allocation, valued=valued, percent=percent, demo=demo)
    if not defer_dialog:
        render_position_dialog(path, snapshot, funds, demo=demo, allocation=allocation, valued=valued)


def _remember_draft():
    if st.session_state.get('position_edit_action') in {'Edit position', 'Add position'}:
        fields = {k: v for k, v in st.session_state.items()
                  if k.startswith('position_edit_fields_') or k == 'position_edit_instrument'}
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
                if st.button('Edit position', icon=':material/edit:'):
                    st.session_state['position_edit_action'] = 'Edit position'
                    st.rerun()
                from portfolio_app.position_detail import render_position_detail
                render_position_detail(row, path.parent, demo=demo, allocation=allocation)
            st.button('Close', on_click=_dismiss)
        else:
            rendered = render_position_form(path, current, funds, demo=demo, allocation=allocation, action=action)
            _remember_draft()
            if not rendered and st.button('Cancel'):
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
        render_purchase_history(row)
        if row.get('balance_replaced_at'):
            st.caption('The current quantity was replaced from a balance snapshot. Retained purchase batches are not a complete ledger.')
    else:
        labels = {row.id: f"{display_name(row.name)} ({row.ticker or row.id.upper()})" for row in holdings.itertuples()}
        if pending := st.session_state.pop("position_edit_pending_instrument", None):
            if pending in labels:
                st.session_state["position_edit_instrument"] = pending
        identity = st.selectbox("Existing instrument", ["", *labels], format_func=lambda value: labels[value] if value else "New instrument", key="position_edit_instrument") if labels else ""
        row = holdings.loc[holdings["id"] == identity].iloc[0].to_dict() if identity else {}
        row = {column: row.get(column, "") for column in ("id", "name", "ticker", "isin", "instrument_type", "exposure_kind", "quantity_unit", "short_name")}
    physical = row.get('instrument_type') == 'physical' and not row.get('ticker') and bool(row.get('quantity_unit'))
    if not editing and not identity:
        entry = st.segmented_control('Position type', ['Listed investment', 'Physical asset'], default='Listed investment',
                                     key='position_edit_fields_entry_kind') or 'Listed investment'
        physical = entry == 'Physical asset'
    context = (str(path.resolve()), action, position_id, identity, physical)
    if st.session_state.get("position_edit_context") != context:
        st.session_state["position_edit_context"] = context
        st.session_state["position_edit_revision"] = snapshot.revision
    if st.session_state["position_edit_revision"] != snapshot.revision:
        st.warning("Holdings changed while this form was open. Reload the form to edit the latest data.")
        if st.button("Reload position form"):
            selected = st.session_state.get('position_edit_selected')
            _clear_editor()
            st.session_state.update(position_edit_selected=selected, position_edit_action=action, position_edit_dialog=True)
            st.rerun()
        return
    # Separate keys keep values tied to the selected position, not another row.
    prefix = f"position_edit_fields_{action}_{position_id}_{identity}_{physical}_"
    if not editing and not identity and not physical:
        with st.expander('Find a listed investment', expanded=True):
            render_instrument_search(prefix, holdings, path.parent / ".cache" / "yahoo", disabled=demo)
    if st.session_state.get('onboarding_step') == 'position':
        st.caption('2 of 2 · Add your first position')
        st.write('Choose what you own, enter the quantity, and select its category. Buy-in and target details can wait.')
    left, right = st.columns(2)
    with left:
        st.markdown('**Holding**')
        name = st.text_input('Instrument name', value=row.get('name') or ('Physical gold' if physical else ''),
                             disabled=bool(identity) and not editing,
                             help='Renaming applies to every position of this instrument.' if editing else None, key=prefix + 'name')
        if physical:
            units = list(dict.fromkeys(['troy oz', 'grams', 'units', row.get('quantity_unit') or 'troy oz']))
            def clear_unit_amounts():
                for field in ('shares', 'buy_in', 'total_buy_in', 'manual_price'):
                    st.session_state.pop(prefix + field, None)
                    st.session_state.get('position_draft', {}).pop(prefix + field, None)
                st.session_state[prefix + 'unit_changed'] = True
            quantity_unit = st.selectbox('Quantity unit', units, index=units.index(row.get('quantity_unit') or 'troy oz'),
                                        disabled=editing or bool(identity), key=prefix + 'quantity_unit', on_change=clear_unit_amounts)
            if st.session_state.get(prefix + 'unit_changed'):
                st.caption('Unit changed. Re-enter the quantity and prices; amounts are not converted.')
            st.caption('For gold, use troy ounces or grams of fine gold. The quantity, price and buy-in must use the same unit; no unit conversion is applied.')
        shares = st.number_input('Quantity held (total)', min_value=0.0, value=float(row.get('shares', 0)), format='%.10f', key=prefix + 'shares')
        account = st.text_input('Storage location (optional)' if physical else 'Account / broker', value=row.get('account', ''), key=prefix + 'account')
        bucket = ''
        if allocation:
            bucket_ids = ['', *sorted(allocation.leaves())]
            bucket_names = category_labels(allocation)
            bucket = st.selectbox('Category', bucket_ids, index=bucket_ids.index(row.get('bucket_id', '')),
                                  key=prefix + 'bucket', format_func=lambda value: bucket_names.get(value, 'Unassigned'),
                                  help='The category owns this position. Its target is a share of the whole portfolio.')
        elif st.session_state.get('onboarding_step') == 'position':
            st.caption('You can add categories later in Rebalance → Targets.')
    with right:
        st.markdown('**Valuation**')
        if physical:
            ticker, isin = '', ''
            st.caption('Enter a dated value per unit. Physical holdings use your manual valuation, not an ETF or futures quote.')
        else:
            ticker = st.text_input('Ticker', value=row.get('ticker', ''), disabled=bool(identity),
                                   help='Choose a listing above or enter its exchange-qualified ticker. Leave blank for manual pricing.', key=prefix + 'ticker')
            isin = st.text_input('ISIN (optional)', value=row.get('isin', ''), disabled=bool(identity), key=prefix + 'isin')
        with st.expander('Current valuation' if physical else 'Manual pricing', expanded=physical):
            manual_price = st.number_input(f'Current price per {quantity_unit} (optional)' if physical else 'Manual unit price (optional)',
                min_value=0., value=_optional_number(row.get('manual_price')), key=prefix + 'manual_price',
                help='Leave blank to track quantity with an unknown value.' if physical else 'Overrides market quotes. Clear to return to provider pricing.')
            manual_currency = st.text_input('Price currency' if physical else 'Manual price currency',
                                            value=row.get('manual_price_currency') or ('EUR' if physical else ''), key=prefix + 'manual_currency')
            if physical:
                from datetime import date
                initial_date = date.fromisoformat(row['manual_price_date']) if row.get('manual_price_date') else date.today()
                manual_date = st.date_input('Price date', value=initial_date, max_value=date.today(), key=prefix + 'manual_date').isoformat()
            else:
                manual_date = st.text_input('Manual price date (YYYY-MM-DD)', value=row.get('manual_price_date', ''), key=prefix + 'manual_date')
        with st.expander('Buy-in (optional)'):
            total_buy_in = st.radio('Buy-in entry', ['Average per unit', 'Total buy-in'], horizontal=True,
                                   key=prefix + 'buy_in_mode') == 'Total buy-in'
            initial_buy_in = _optional_number(row.get('acquisition_price'))
            if total_buy_in:
                initial_total = None if initial_buy_in is None else initial_buy_in * float(row.get('shares', 0))
                buy_in = st.number_input('Total buy-in (optional)', min_value=0.0, value=initial_total,
                                         format='%.8f', key=prefix + 'total_buy_in',
                                         help='Total cost of the quantity currently held, including purchase fees.')
            else:
                buy_in = st.number_input('Average buy-in per unit (optional)', min_value=0.0, value=initial_buy_in, format='%.6f', key=prefix + 'buy_in')
            currency = st.text_input('Buy-in currency', value=row.get('acquisition_currency', '' if editing else 'EUR'),
                                     help='Currency of your purchase cost; it can differ from the current price currency.', key=prefix + 'currency')
        with st.expander('Target (optional)'):
            target_field = 'within_bucket_target' if allocation else 'target_allocation'
            initial_target = _optional_number(row.get(target_field))
            target = st.number_input('Target within category % (optional)' if allocation else 'Target allocation % (optional)',
                                    min_value=0.0, max_value=100.0, value=None if initial_target is None else initial_target * 100, key=prefix + 'target')
            st.caption('A share of the selected category, not of the whole portfolio.' if allocation else 'A share of the whole portfolio. Leave blank if undecided.')
    with st.expander('More details'):
        short_name = st.text_input('Short display name (optional)', value=row.get('short_name', ''), key=prefix + 'short_name')
        portfolio = st.text_input('Portfolio / sleeve', value=row.get('portfolio', ''), key=prefix + 'portfolio')
        if physical:
            instrument_type, exposure_kind = 'physical', 'non_equity'
        else:
            kinds = ['unknown', 'equity', 'etf', 'etc', 'crypto', 'physical', 'cash', 'other']
            instrument_type = st.selectbox('Instrument type', kinds, index=kinds.index(row.get('instrument_type')) if row.get('instrument_type') in kinds else 0, key=prefix + 'instrument_type')
            exposure_kinds = ['unknown', 'equity', 'non_equity']
            exposure_kind = st.selectbox('Underlying exposure', exposure_kinds,
                                         index=exposure_kinds.index(row.get('exposure_kind')) if row.get('exposure_kind') in exposure_kinds else 0,
                                         format_func=lambda value: {'unknown': 'Unknown / mixed', 'equity': 'Equity', 'non_equity': 'Non-equity'}[value],
                                         key=prefix + 'exposure_kind', help='Leave mixed or uncertain composition unknown.')
            quantity_unit = st.text_input('Quantity unit', value=row.get('quantity_unit', ''), key=prefix + 'quantity_unit', help='Required for a manual unit price, e.g. shares or units.')
        extras = {}
        for column in metadata_dimensions(holdings):
            if column not in {'portfolio', 'account', 'bucket_id', 'instrument_type', 'exposure_kind'}:
                extras[column] = st.text_input(column.replace('_', ' ').title(), value=row.get(column, ''), key=prefix + 'extra_' + column)
    save, cancel = st.columns(2)
    submitted = save.button('Save position', type='primary', width='stretch')
    if cancel.button('Finish later' if st.session_state.get('onboarding_step') == 'position' else 'Cancel', width='stretch'):
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
            **({'bucket_id': bucket} if allocation else {}), **extras,
        }
        try:
            if total_buy_in:
                # Preserve the original unit cost on an unchanged round trip,
                # including planned zero-quantity positions with a known cost.
                unchanged = editing and shares == row.get('shares') and buy_in == initial_total
                buy_in = initial_buy_in if unchanged else average_from_total(buy_in, shares)
                values['acquisition_price'] = '' if buy_in is None else str(buy_in)
                values['acquisition_currency'] = currency if buy_in is not None else ''
            new_buy_in = not editing or buy_in != _optional_number(row.get("acquisition_price"))
            if buy_in is not None and not currency.strip() and new_buy_in:
                raise DataError("Enter the currency of the buy-in price, for example EUR.")
            asset_id = save_position(
                path, values, expected_revision=st.session_state["position_edit_revision"], position_id=position_id,
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
