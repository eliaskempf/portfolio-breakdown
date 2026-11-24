"""Create and maintain summary positions without transaction accounting."""

from portfolio_app.interface.shared.currency_display import reporting_currency

from pathlib import Path

import streamlit as st

from portfolio_app.interface.shared.render_sections import render_sections

from portfolio_app.interface.views.positions.position_entry_ui import (holding_amounts, optional_number, physical_pricing,
    purchase_rows, render_manual_price)
from portfolio_app.portfolio.purchases import save_purchase_batch, read_opening
from portfolio_app.portfolio.position_entry import compact_decimal, decimal_input
from portfolio_app.interface.shared.display_names import display_name, instrument_name
from portfolio_app.funds.etf import FundSnapshot, validate_fund_listings
from portfolio_app.portfolio.physical_assets import is_unit_based_physical
from portfolio_app.portfolio.holdings import DataError, metadata_dimensions
from portfolio_app.interface.shared.instrument_ui import render_instrument_search
from portfolio_app.interface.views.positions.position_list import list_context, render_position_list
from portfolio_app.ledger.bank_accounts import bank_detail_position, bank_position_ids
from portfolio_app.portfolio.strategic import category_labels
from portfolio_app.portfolio.positions import HoldingsSnapshot, save_position, read_snapshot
from portfolio_app.interface.views.positions.purchase_ui import render_bulk_purchases, render_purchase_history


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
    st.session_state.pop('position_draft_origin', None)
    st.session_state.pop('strategic_last_position', None)


def request_position(position_id, *, editing=False, local=True):
    origin = st.session_state.get('position_draft_origin')
    if origin is None and st.session_state.get('position_edit_selected') != position_id:
        _clear_editor()
    action = 'Edit position' if editing else 'Details'
    if editing and origin is not None and origin != ('Edit position', position_id):
        action = 'Unsaved position draft'
    st.session_state.update(position_edit_detail_requested=True, position_edit_selected=position_id,
                            position_edit_action=action, position_edit_dialog=True)
    from portfolio_app.interface.shared.dialog_runtime import route_position_callback
    if local:
        route_position_callback()


def _consume_position_event(path, snapshot):
    if event := st.session_state.pop('position_edit_open_request', None):
        from portfolio_app.ledger.account_journal import load_journal
        journal, _ = load_journal(path.parent)
        identity = event.get('id', '')
        actual = identity in {p.id for p in journal.positions} | {investment.id for investment in journal.investments} | {link.position_id for link in journal.cash_links} or (journal.accounts and str(identity).startswith('account-'))
        current = event.get('context') == list_context(path) and event.get('revision') == snapshot.revision
        future_position = identity in {p.id for p in journal.positions} | {investment.id for investment in journal.investments}
        if current and event.get('history_target') == 'accounts' and not journal.accounts:
            st.session_state['positions_workflow_request'] = 'Accounts & futures'
            st.session_state['position_edit_dialog'] = False
        elif current and (bank_position := bank_detail_position(journal, identity)):
            request_position(bank_position, editing=event.get('action') == 'edit')
        elif current and future_position:
            request_position(identity, editing=event.get('action', 'edit') == 'edit')
        elif current and (actual or event.get('history_target') == 'accounts'):
            st.session_state.update(accounts_open_requested=True, accounts_return_tab='Positions',
                                    accounts_requested_position=identity, position_edit_dialog=False)
            st.rerun()
        elif current and identity in set(snapshot.holdings.position_id):
            request_position(identity, editing=event.get('action', 'edit') == 'edit')
            if event.get('history_target') == 'transactions':
                from portfolio_app.interface.views.history.history_ui import _key
                st.session_state[f'position_edit_detail_view_{identity}'] = 'Transactions'
                st.session_state[_key(path.parent, identity)+'screen'] = event.get('history_screen', 'list')
            elif event.get('history_target') == 'holdings':
                st.session_state[f'position_edit_detail_view_{identity}'] = 'History'
                st.session_state[f'position_edit_history_mode_{identity}'] = 'Holdings'
                preferences = dict(st.session_state.get('position_history_preferences', {}))
                st.session_state['position_history_preferences'] = {**preferences, identity: 'Holdings'}
        else:
            st.warning('The position list changed. Open the position again from the refreshed list.')


def _has_position_draft():
    return bool(st.session_state.get('position_draft') or
                st.session_state.get('position_edit_action') in {'Edit position', 'Add position'})


def render_position_editor(path, snapshot, funds, *, demo=False, embedded=False, allocation=None,
                           valued=None, account_valued=None, percent=False, defer_dialog=False, has_accounts=False, journal=None):
    dialog_baseline = position_dialog_sources(path.parent) if not defer_dialog else None
    account_valued = account_valued if account_valued is not None else valued
    _consume_position_event(path, snapshot)
    if warning := st.session_state.pop('position_stale_notice', None):
        st.warning(warning)
    if message := st.session_state.pop('position_saved_notice', None):
        st.success(message)
    if requested := st.session_state.pop('positions_workflow_request', None):
        st.session_state['positions_workflow'] = requested
    workflow = st.session_state.setdefault('positions_workflow', 'Positions')
    if workflow != 'Positions':
        st.button('Back to positions', icon=':material/arrow_back:', type='tertiary',
                  on_click=_open_position_task, args=('Positions',))
    if workflow in ('Import', 'Bulk add purchases'):
        st.subheader(workflow)
    if workflow == 'Accounts & futures':
        if has_accounts:
            st.session_state.update(accounts_open_requested=True, accounts_return_tab='Positions',
                                    accounts_requested_position='', positions_workflow_request='Positions')
            st.rerun()
        from portfolio_app.interface.views.derivatives.accounts_ui import render_accounts
        render_accounts(path.parent, snapshot, account_valued if account_valued is not None else snapshot.holdings, allocation=allocation)
    elif workflow == 'Import':
        from portfolio_app.interface.views.ingestion.import_ui import render_import
        render_import(path, snapshot, funds, demo=demo)
    elif workflow == 'Connect live prices':
        from portfolio_app.interface.views.ingestion.import_ui import render_listing_link
        render_listing_link(path, snapshot, funds, demo=demo)
    elif workflow == 'Update balances':
        from portfolio_app.interface.views.portfolio.allocation_ui import render_balances
        render_balances(path, snapshot)
    elif workflow == 'Bulk add purchases':
        notice = render_bulk_purchases(path, snapshot, funds, demo=demo)
        if notice is not None:
            _clear_editor()
            if notice:
                st.session_state['position_saved_notice'] = notice
            st.rerun()
    else:
        draft_waiting = _has_position_draft() and not st.session_state.get('position_edit_dialog')
        statement_request = st.session_state.pop('positions_existing_holding_requested', False)
        add_requested = st.session_state.pop('empty_overview_add_requested', False) or statement_request
        if add_requested and draft_waiting:
            st.info('An unsaved position draft is available. Resume it or discard it to start another.')
        with st.container(horizontal=True, key='position_list_actions'):
            if draft_waiting and st.button('Resume unsaved edit', type='primary', help='Reopen the position draft kept in this session.'):
                # Reattach detached widget values even when their state keys remain.
                for key, value in st.session_state.get('position_draft', {}).items():
                    st.session_state[key] = value
                if origin := st.session_state.get('position_draft_origin'):
                    st.session_state['position_edit_action'], st.session_state['position_edit_selected'] = origin
                st.session_state['position_edit_dialog'] = True
            add_label = 'Discard draft & add position' if draft_waiting else 'Add position'
            add = st.button(add_label, type='secondary' if draft_waiting else 'primary', icon=':material/add:',
                            help='Record a new purchase or add an existing holding.', key='tour_add_position')
            if add or (add_requested and not draft_waiting):
                _clear_editor()
                if statement_request:
                    st.session_state['position_edit_fields_entry_mode'] = 'Existing holding'
                st.session_state.update(position_edit_action='Add position', position_edit_dialog=True)
            st.button('Import', on_click=_open_position_task, args=('Import',),
                      help='Transaction CSV, statement PDFs or holdings snapshot.')
            with st.popover('More actions', key='positions_actions'):
                for task in ('Bulk add purchases', 'Update balances', 'Connect live prices', 'Accounts & futures'):
                    if task in ('Update balances', 'Connect live prices') and snapshot.holdings.empty:
                        continue
                    st.button(task, on_click=_open_position_task, args=(task,))
        if snapshot.holdings.empty and (valued is None or valued.empty):
            st.caption('No positions')
        else:
            with st.container(key='tour_position_list'):
                render_position_list(path, snapshot, allocation, valued=valued, account_valued=account_valued, percent=percent, demo=demo)
    if workflow == 'Positions':
        from portfolio_app.ledger.account_journal import load_journal
        from portfolio_app.interface.views.derivatives.accounts_ui import render_actual_positions
        if journal is None:
            journal, _ = load_journal(path.parent)
        render_actual_positions(path.parent, journal, valued=account_valued)
    if not defer_dialog:
        render_position_dialog(path, snapshot, funds, demo=demo, allocation=allocation, valued=account_valued,
                               journal=journal, baseline=dialog_baseline)


def _remember_draft():
    if st.session_state.get('position_edit_action') in {'Edit position', 'Add position'}:
        fields = {k: v for k, v in st.session_state.items()
                  if (k.startswith('position_edit_fields_') and not k.endswith('purchase_editor')) or k == 'position_edit_instrument'}
        # On a dismiss callback Streamlit may already have detached the dialog's
        # widget state. Don't replace its last complete draft with an empty one.
        if fields:
            st.session_state['position_draft'] = fields
            st.session_state['position_draft_origin'] = (st.session_state['position_edit_action'],
                                                         st.session_state.get('position_edit_selected'))


def _dismiss():
    _remember_draft()
    st.session_state['position_edit_dialog'] = False


def _open_position_task(task):
    _dismiss()
    st.session_state['positions_workflow'] = task


def _dismiss_dialog():
    _dismiss()
    editing = st.session_state.get('position_edit_action') in {'Add position', 'Edit position'}
    if editing and st.session_state.get('position_draft'):
        # The resume action lives on the parent page. A saved session draft
        # needs that page refreshed; read-only dismissal still stays local.
        st.rerun()
    from portfolio_app.interface.shared.dialog_runtime import route_position_callback
    route_position_callback()


def position_dialog_sources(directory):
    from portfolio_app.interface.shared.input_cache import file_stamp
    return tuple(file_stamp(directory/name) for name in
        ('holdings.csv', 'account-journal.json', 'history-observations.json', 'allocation.yaml', 'portfolio.yaml'))


def _reload_dialog_values(directory, *, demo, prices=None, inputs=None):
    """Use the normal valuation pipeline after a child workflow changes facts."""
    from portfolio_app.interface.shared.input_cache import load_inputs
    from portfolio_app.application import prepare_application, select_price_service, historical_for
    from portfolio_app.performance.history_observations import ObservationFX, load_observations
    from portfolio_app.ledger.account_journal import load_journal
    inputs = inputs or load_inputs(directory)
    prices = select_price_service(directory, offline_demo=demo, supplied=prices)
    historical = ObservationFX(load_observations(directory)[0], historical_for(directory, demo=demo))
    valued = prepare_application(inputs, prices, reporting_currency=reporting_currency(), historical=historical).valued
    journal, _ = load_journal(directory)
    if journal.accounts:
        from portfolio_app.portfolio.actual_portfolio import journal_positions
        from portfolio_app.derivatives.futures_valuation import load_valuations
        valuations, rates = load_valuations(directory, journal)
        valued = journal_positions(valued, journal, prices, reporting_currency=reporting_currency(),
                                  valuations=valuations, fx_rates=rates)
    return inputs.snapshot, inputs.allocation, valued


def render_position_dialog(path, snapshot, funds, *, demo=False, allocation=None, valued=None, prices=None,
                           journal=None, baseline=None):
    from portfolio_app.interface.shared.dialog_runtime import HOST
    if baseline is None:
        baseline = position_dialog_sources(path.parent)
    draft_visible = _has_position_draft()
    current_values = [baseline, snapshot, allocation, valued]
    from portfolio_app.ledger.account_journal import load_journal
    from portfolio_app.ledger.security_history import selected_events
    from dataclasses import replace

    def valuation_facts(journal):
        # Pending evidence and review receipts do not change prices, costs or cash.
        active = tuple((b.position_id, b.account_id, b.cash_from, selected_events(b))
                       for b in journal.securities if b.active)
        return replace(journal, securities=()), active

    if journal is None:
        journal, _ = load_journal(path.parent)

    def values():
        nonlocal journal
        signature = position_dialog_sources(path.parent)
        if signature != current_values[0]:
            from portfolio_app.interface.shared.input_cache import load_inputs
            inputs = load_inputs(path.parent)
            current_journal, _ = load_journal(path.parent)
            before = current_values[1].holdings.drop(columns=['position_key'], errors='ignore')
            after = inputs.snapshot.holdings.drop(columns=['position_key'], errors='ignore')
            same_valuation = (signature[2:] == current_values[0][2:] and before.equals(after)
                              and valuation_facts(current_journal) == valuation_facts(journal))
            refreshed = ((inputs.snapshot, inputs.allocation, current_values[3]) if same_valuation else
                         _reload_dialog_values(path.parent, demo=demo, prices=prices, inputs=inputs))
            current_values[:] = [signature, *refreshed]
            journal = current_journal
        return current_values[1:]

    mounted = False

    @st.fragment(key=HOST)
    def host():
        nonlocal mounted
        revisiting = mounted
        mounted = True
        st.session_state['position_dialog_host_ready'] = True
        if not st.session_state.get('position_edit_dialog') and not st.session_state.get('position_edit_open_request'):
            # Read-only open/close never rebuilds the portfolio. Persisted changes
            # get one parent refresh once the dialog has actually closed.
            # Refresh the parent toolbar when a draft is created or discarded,
            # while keeping ordinary read-only dismissal local.
            # On initial mount the parent may already be showing a save conflict.
            # A later fragment dismissal owns the refresh after a dialog edit.
            if revisiting and (position_dialog_sources(path.parent) != baseline or _has_position_draft() != draft_visible):
                st.rerun()
            return
        _render_position_dialog(path, snapshot, funds, demo=demo, allocation=allocation, valued=valued, values=values)
    host()


def _render_position_dialog(path, snapshot, funds, *, demo, allocation, valued, values):
    from portfolio_app.interface.shared.dialog_runtime import local_dialog_updates, close_dialog_button
    _consume_position_event(path, snapshot)
    if not st.session_state.get('position_edit_dialog'):
        if st.session_state.get('positions_workflow_request'):
            st.rerun()
        return

    from portfolio_app.ledger.account_journal import load_journal
    dialog_journal, _ = load_journal(path.parent)
    future_ids = {p.id for p in dialog_journal.positions} | {item.id for item in dialog_journal.investments}
    selected_id = st.session_state.get('position_edit_selected')
    actual_future = selected_id in future_ids
    bank = bank_position_ids(dialog_journal).get(selected_id)
    title = st.session_state.get('position_edit_action', 'Details')
    if title == 'Details' or (actual_future and title == 'Edit position'):
        title = 'Position details'
    if title == 'Position details' and not actual_future:
        selected_title = snapshot.holdings.loc[snapshot.holdings.position_id.eq(st.session_state.get('position_edit_selected'))]
        if not selected_title.empty:
            title = instrument_name(selected_title.iloc[0])
    if bank:
        title = bank.name
    @st.dialog(title, width='large', on_dismiss=_dismiss_dialog)
    def dialog():
        # Button callbacks run before the dialog body. Never execute its data
        # loaders again when the user has already requested dismissal.
        if not st.session_state.get('position_edit_dialog'):
            st.rerun()
        with local_dialog_updates(), st.container(key='position_dialog_'+str(st.session_state.get('position_edit_selected', 'new'))+'_'+st.session_state.get('position_edit_action', 'Details')):
            snapshot, allocation, valued = values()
            current = read_snapshot(path)
            for key, value in st.session_state.get('position_draft', {}).items():
                if key not in st.session_state:
                    st.session_state[key] = value
            action = st.session_state.get('position_edit_action', 'Details')
            if action == 'Unsaved position draft':
                st.write('Editing another position would discard your unsaved draft.')
                identity = st.session_state['position_edit_selected']
                if st.button('Keep draft and view details'):
                    request_position(identity, local=False)
                    st.rerun()
                if st.button('Discard draft and edit position'):
                    _clear_editor()
                    request_position(identity, editing=True, local=False)
                    st.rerun()
                return
            if bank:
                from portfolio_app.interface.views.positions.bank_account_ui import render_bank_detail
                latest = bank_position_ids(load_journal(path.parent)[0]).get(selected_id)
                if latest:
                    render_bank_detail(path.parent, current, allocation, valued, latest, editing=action == 'Edit position')
                else:
                    st.warning('This cash account no longer exists.')
                if action != 'Edit position':
                    close_dialog_button('Close', key='position_dialog_close')
            elif actual_future:
                journal, revision = load_journal(path.parent)
                from portfolio_app.interface.views.derivatives.investment_detail_ui import render_investment_detail
                render_investment_detail(path.parent, journal, revision, valued,
                    st.session_state.get('position_edit_selected'), allocation=allocation, editing=action == 'Edit position')
                close_dialog_button('Close', key='position_dialog_close')
            elif action == 'Details':
                from portfolio_app.ledger.security_history import effective_holdings
                current_holdings = effective_holdings(current.holdings, load_journal(path.parent)[0].securities)
                selected = current_holdings.loc[current_holdings.position_id.eq(st.session_state.get('position_edit_selected'))]
                if selected.empty:
                    st.warning('This position no longer exists.')
                else:
                    row = selected.iloc[0]
                    previous = snapshot.holdings.loc[snapshot.holdings.position_id.eq(row.position_id)]
                    # The first evidence save may add stable position keys. Keep
                    # the valuation when the effective holding itself is unchanged.
                    unchanged = (not previous.empty and row.drop(labels=['position_key'], errors='ignore').equals(
                        previous.iloc[0].drop(labels=['position_key'], errors='ignore')))
                    if valued is not None and unchanged:
                        matches = valued.loc[valued.position_id.eq(row.position_id)]
                        if not matches.empty:
                            row = matches.iloc[0]
                    def edit_position():
                        request_position(row.position_id, editing=True, local=False)
                    from portfolio_app.interface.views.positions.position_detail import render_position_detail
                    render_position_detail(row, path.parent, demo=demo, allocation=allocation, on_edit=edit_position, funds=funds)
                close_dialog_button('Close', key='position_dialog_close')
            else:
                rendered = render_position_form(path, current, funds, demo=demo, allocation=allocation, action=action, valued=valued)
                _remember_draft()
                if not rendered and st.button('Cancel', help='Discard this position draft without saving.'):
                    _clear_editor()
                    st.rerun()
    dialog()


def _reload_stale_position(action):
    st.warning("Holdings changed while this form was open. Reload the form to edit the latest data.")
    if st.button("Reload position form", help='Discard this stale draft and load the latest saved holding.'):
        selected = st.session_state.get('position_edit_selected')
        _clear_editor()
        st.session_state.update(position_edit_selected=selected, position_edit_action=action, position_edit_dialog=True)
        st.rerun()


def _edit_physical_valuation(path, snapshot, row):
    prefix = f"position_edit_fields_valuation_{row.position_id}_"
    expected = st.session_state.setdefault(prefix + 'revision', snapshot.revision)
    if expected != snapshot.revision:
        _reload_stale_position('Edit position')
        return
    source, unit = physical_pricing(prefix, row, unit_locked=True)
    price, currency, observed = (None, '', '') if source else render_manual_price(
        prefix, row, physical=True, quantity_unit=unit)
    if st.button('Save valuation', type='primary'):
        try:
            save_position(path, dict(price_source=source, manual_price='' if price is None else str(price),
                          manual_price_currency=currency.upper(), manual_price_date=observed),
                          position_id=row.position_id, expected_revision=expected)
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            _clear_editor()
            st.session_state['position_saved_notice'] = 'Valuation saved.'
            st.rerun()


def render_position_form(path, snapshot, funds, *, demo=False, allocation=None, action='Add position', valued=None):
    holdings = snapshot.holdings
    editing = action == "Edit position"
    position_id = None
    purchase = False
    if editing:
        if holdings.empty:
            st.info("Create your first position before editing.")
            return
        position_id = st.session_state.get('position_edit_selected')
        if position_id not in set(holdings.position_id):
            st.warning('This position no longer exists. Close and reopen the list.')
            return
        row = holdings.loc[holdings.position_id.eq(position_id)].iloc[0]
        from portfolio_app.ledger.account_journal import load_journal
        from portfolio_app.ledger.security_history import effective_holdings
        history_journal, _ = load_journal(path.parent)
        if any(b.position_id == position_id and b.active for b in history_journal.securities):
            if is_unit_based_physical(row):
                view = st.segmented_control('Edit position', ['Valuation', 'Transactions'], default='Valuation',
                    required=True, key=f'position_edit_fields_active_view_{position_id}')
                if view == 'Valuation':
                    _edit_physical_valuation(path, snapshot, row)
                    return True
            from portfolio_app.interface.views.history.history_ui import render_position_history
            current = effective_holdings(holdings, history_journal.securities)
            st.caption('Quantity and cost come from history. Use Replace balance for a new snapshot; categories and targets remain editable in Rebalance.')
            render_position_history(path.parent, current.loc[current.position_id == position_id].iloc[0])
            return
        row = row.to_dict()
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
    physical = is_unit_based_physical(row)
    if not editing and not identity:
        entry = kind_column.segmented_control('Position type', ['Listed investment', 'Cash account', 'Physical asset', 'Future'], help='Choose the kind of position to add.', required=True, default='Listed investment',
                                     key='position_edit_fields_entry_kind') or 'Listed investment'
        if entry == 'Cash account':
            from portfolio_app.interface.views.positions.bank_account_ui import render_bank_form
            render_bank_form(path.parent, snapshot, allocation, valued)
            return True
        if entry == 'Future':
            from portfolio_app.interface.views.derivatives.accounts_ui import render_future_entry
            render_future_entry(path.parent, snapshot, valued=valued, allocation=allocation)
            return True
        physical = entry == 'Physical asset'
    if not editing:
        purchase = st.segmented_control('Entry method', ['New purchase', 'Existing holding'],
            required=True, default='Existing holding' if st.session_state.get('onboarding_step') == 'position' else 'New purchase',
            key='position_edit_fields_entry_mode') != 'Existing holding'
    if purchase and identity:
        destinations = holdings.loc[holdings.id.eq(identity)]
        bucket_names = category_labels(allocation) if allocation else {}
        destination_labels = {r.position_id: ' · '.join(filter(None, (r.account or 'Unspecified account',
                    r.portfolio, bucket_names.get(r.get('bucket_id', ''), ''))))
                  for _, r in destinations.iterrows()}
        destination = st.selectbox('Purchase destination', [*destination_labels, ''],
            format_func=lambda value: destination_labels.get(value, 'New account / holding'),
            key='position_edit_fields_destination_'+identity)
        if destination:
            from portfolio_app.ledger.account_journal import load_journal
            from portfolio_app.interface.views.positions.trade_entry_ui import render_trade
            journal, revision = load_journal(path.parent)
            trade_prefix = 'position_edit_fields_trade_'+destination+'_'
            for field, value in st.session_state.pop('position_edit_pending_fields', {}).items():
                if field.startswith('trade_'):
                    st.session_state[trade_prefix + field] = value
            def saved_trade():
                _clear_editor()
                st.session_state['position_saved_notice'] = 'Purchase saved.'
            render_trade(path.parent, snapshot, journal, revision, destination,
                trade_prefix, on_saved=saved_trade)
            if st.button('Cancel'):
                _clear_editor()
                st.rerun()
            return True
    context = (str(path.resolve()), action, position_id, identity, physical)
    if st.session_state.get("position_edit_context") != context:
        st.session_state["position_edit_context"] = context
        st.session_state["position_edit_revision"] = snapshot.revision
    if st.session_state["position_edit_revision"] != snapshot.revision:
        _reload_stale_position(action)
        return
    # Separate keys keep values tied to the selected position, not another row.
    prefix = f"position_edit_fields_{action}_{position_id}_{identity}_{physical}_"
    if purchase:
        from portfolio_app.ledger.account_journal import load_journal
        from portfolio_app.workspace.storage import revision as file_revision
        trade_journal, trade_revision = load_journal(path.parent)
        trade_expected = st.session_state.setdefault(prefix + 'trade_expected',
            (snapshot.revision, trade_revision, file_revision(path.parent/'allocation.yaml')))
    if not editing:
        for field, value in st.session_state.pop('position_edit_pending_fields', {}).items():
            st.session_state[prefix + field] = value
        pending_rows = st.session_state.pop('position_edit_pending_purchases', None)
        if pending_rows is not None:
            st.session_state.setdefault('view_editor_drafts', {})[prefix + 'purchase_editor'] = pending_rows
    sections = render_sections('position_entry_', 'search', 'fields', 'actions')
    with sections['search']:
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
    with sections['fields']:
        if st.session_state.get('onboarding_step') == 'position':
            st.caption('2 of 2 · Add your first position')
            st.write('Choose what you own, enter the quantity, and select its category. Buy-in and target details can wait.')
        price_source = row.get('price_source', '')
        manual_entry = st.session_state.get(prefix + 'manual_entry', False)
        name = st.session_state.get(prefix + 'name', row.get('name', ''))
        ticker = st.session_state.get(prefix + 'ticker', row.get('ticker', ''))
        identity_ready = bool(name) or manual_entry or physical or editing or bool(identity)
        if identity_ready:
            name_column, ticker_column = st.columns([3, 2]) if not physical else (st, st)
            name = name_column.text_input('Instrument name', value=row.get('name', ''),
                disabled=bool(identity) and not editing, key=prefix + 'name',
                help='Edit the name shown in your portfolio. Renaming a saved instrument applies to all its account positions.')
            if not physical:
                ticker = ticker_column.text_input('Ticker', value=row.get('ticker', ''),
                    disabled=bool(identity) or not manual_entry, key=prefix + 'ticker',
                    help='Exchange-qualified symbol used to retrieve prices. Use Change investment to select another listing. '
                         'For saved holdings, use Connect live prices to review a listing change.')
        if physical:
            ticker, isin = '', ''

            price_source, quantity_unit = physical_pricing(prefix, row, unit_locked=editing or bool(identity))

        amounts_slot = st.container()
        category_column, target_column = st.columns(2)
        bucket = ''
        with category_column:
            if allocation:
                bucket_ids = ['', *sorted(allocation.descendants())]
                bucket_names = category_labels(allocation)
                bucket = st.selectbox('Category', bucket_ids, index=bucket_ids.index(row.get('bucket_id', '')),
                    key=prefix + 'bucket', format_func=lambda value: bucket_names.get(value, 'Unassigned'),
                    help='Category owning this holding. The position target beside it is a share of this category.')
            else:
                st.caption('Add categories in Rebalance → Targets.')
        with target_column:
            target_field = 'within_bucket_target' if allocation else 'target_allocation'
            initial_target = optional_number(row.get(target_field))
            target = st.number_input('Target within category % (optional)' if allocation else 'Target allocation % (optional)',
                min_value=0.0, max_value=100.0, value=None if initial_target is None else initial_target * 100,
                key=prefix + 'target', help='Share of the selected category, not the whole portfolio. Blank means undecided.' if allocation else
                'Share of the whole portfolio. Blank means undecided.')

        if physical and not price_source:
            manual_price, manual_currency, manual_date = render_manual_price(prefix, row, physical=True, quantity_unit=quantity_unit)
        else:
            manual_price, manual_currency, manual_date = None, '', ''
        snapshot_date = None
        if not editing and not purchase:
            from datetime import date
            snapshot_date = st.date_input('Holding as of', value=date.today(), max_value=date.today(), key=prefix+'snapshot_date',
                help='Date of this total holding; it is not a purchase date.')
        from_purchases, purchases = False, []
        with st.expander('More details'):
            if not physical:
                isin = st.text_input('ISIN (optional)', value=row.get('isin', ''),
                    disabled=bool(identity) or not manual_entry, key=prefix + 'isin',
                    help='Optional security identifier retained from search. Prices use the ticker, so a missing ISIN does not prevent live pricing.')
            account = st.text_input('Storage location (optional)' if physical else 'Account / broker', value=row.get('account', ''), key=prefix + 'account',
                help='Optional location or account label. Use separate positions for holdings in different accounts.')
            if not physical:
                manual_price, manual_currency, manual_date = render_manual_price(prefix, row)
            if not editing and not purchase:
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
            if purchase:
                from portfolio_app.interface.views.positions.trade_entry_ui import trade_inputs
                trade_event, trade_error = trade_inputs(prefix, trade_journal)
                shares, buy_in, currency = 0, None, ''
                amount_errors = [trade_error] if trade_error else []
                if trade_error:
                    st.caption(trade_error)
            else:
                shares, buy_in, currency, amount_errors = holding_amounts(prefix, row, editing=editing, from_purchases=from_purchases, rows=purchases)
        from portfolio_app.performance.cost_basis import (FIELD, active_components, aggregate_component, encode_components,
            fingerprint, same_summary, resolve_cost)
        from portfolio_app.interface.views.workspace.currency_ui import cost_conversion_controls, historical_for, ui_prices, render_fx_progress
        from portfolio_app.portfolio.purchases import purchase_components, validate_purchases
        parts, conversion_error = [], None
        historical = None if purchase else historical_for(path.parent, demo=demo)
        if not amount_errors and not purchase:
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
    with sections['actions']:
        save, cancel = st.columns(2)
        submitted = save.button('Save purchase' if purchase else 'Save position', help='Save this holding and its optional purchase records together in the active workspace.', type='primary', width='stretch')
        in_setup = st.session_state.get('onboarding_step') == 'position'
        if cancel.button('Finish later' if in_setup else 'Cancel',
                         help='Keep this draft for this session.' if in_setup else 'Discard this draft without saving.', width='stretch'):
            if in_setup:
                _dismiss()
            else:
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
            if physical and not quantity_unit:
                raise DataError('Enter a quantity unit for this physical asset.')
            if amount_errors:
                raise DataError(amount_errors[0])
            new_buy_in = not editing or buy_in != decimal_input(compact_decimal(row.get("acquisition_price")), "Buy-in", optional=True)
            if buy_in is not None and not currency.strip() and new_buy_in:
                raise DataError("Enter the currency of the buy-in price, for example EUR.")
            if conversion_error:
                raise DataError(conversion_error)
            if snapshot_date:
                values['holdings_confirmed_on'] = snapshot_date.isoformat()
            if purchase:
                from portfolio_app.ledger.trade_commands import save_trade
                asset_id = save_trade(path.parent, trade_event, fields=values,
                    expected_holdings_revision=trade_expected[0], expected_journal_revision=trade_expected[1],
                    expected_allocation_revision=trade_expected[2],
                    validate=lambda frame: validate_fund_listings(frame, funds))
            elif from_purchases:
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
