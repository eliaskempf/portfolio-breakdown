"""Create and maintain summary positions without transaction accounting."""

from pathlib import Path

import pandas as pd
import streamlit as st

from portfolio_app.display_names import display_name
from portfolio_app.etf import FundSnapshot, validate_fund_listings
from portfolio_app.holdings import DataError, metadata_dimensions
from portfolio_app.instrument_ui import render_instrument_search
from portfolio_app.positions import HoldingsSnapshot, save_position
from portfolio_app.purchase_ui import render_bulk_purchases, render_purchase_history


def _optional_number(value) -> float | None:
    return None if value is None or pd.isna(value) or value == "" else float(value)


def _clear_editor() -> None:
    for key in list(st.session_state):
        if key.startswith("position_edit_"):
            del st.session_state[key]


def render_position_editor(path: Path, snapshot: HoldingsSnapshot, funds: list[FundSnapshot], *, demo: bool = False, embedded: bool = False, allocation=None) -> None:
    holdings = snapshot.holdings
    if message := st.session_state.pop("position_saved_notice", None):
        st.success(message)
    with (st.container() if embedded else st.expander("Manage positions", expanded=holdings.empty)):
        st.caption("Add investments, update a holding, or record a batch of purchases.")
        action = st.radio("Position action", ["Add position", "Edit position", "Bulk add purchases", "Update balances", "Strategic allocation"], index=3 if allocation else 0, horizontal=True, key="position_edit_action")
        if action == 'Update balances':
            from portfolio_app.allocation_ui import render_balances
            render_balances(path, snapshot)
            return
        if action == 'Strategic allocation':
            from portfolio_app.allocation_ui import render_allocation_editor
            render_allocation_editor(path, snapshot, allocation)
            return
        if action == "Bulk add purchases":
            notice = render_bulk_purchases(path, snapshot, funds, demo=demo)
            if notice is not None:
                _clear_editor()
                if notice:
                    for key in list(st.session_state):
                        if key.startswith("filter_"):
                            del st.session_state[key]
                    st.session_state["position_saved_notice"] = notice
                st.rerun()
            return
        editing = action == "Edit position"
        position_id = None
        if editing:
            if holdings.empty:
                st.info("Create your first position before editing.")
                return
            descriptions = {
                row.position_id: f"{display_name(row.name)} ({row.ticker or row.id.upper()}) · {row.portfolio or 'No portfolio'} · {row.account or 'No account'}"
                for row in holdings.itertuples()
            }
            position_id = st.selectbox("Position to edit", list(descriptions), format_func=descriptions.get, key="position_edit_selected")
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
            identity = st.selectbox("Existing instrument", ["", *labels], format_func=lambda value: labels[value] if value else "New instrument", key="position_edit_instrument")
            row = holdings.loc[holdings["id"] == identity].iloc[0].to_dict() if identity else {}
            row = {column: row.get(column, "") for column in ("id", "name", "ticker", "isin", "instrument_type", "exposure_kind", "quantity_unit")}
        context = (str(path.resolve()), action, position_id, identity)
        if st.session_state.get("position_edit_context") != context:
            st.session_state["position_edit_context"] = context
            st.session_state["position_edit_revision"] = snapshot.revision
        if st.session_state["position_edit_revision"] != snapshot.revision:
            st.warning("Holdings changed while this form was open. Reload the form to edit the latest data.")
            if st.button("Reload position form"):
                _clear_editor()
                st.rerun()
            return
        # Separate keys keep values tied to the selected position, not another row.
        prefix = f"position_edit_fields_{action}_{position_id}_{identity}_"
        if not editing and not identity:
            render_instrument_search(prefix, holdings, path.parent / ".cache" / "yahoo", disabled=demo)
        with st.form(f"position_form_{action}_{position_id}_{identity}"):
            left, right = st.columns(2)
            with left:
                name = st.text_input("Instrument name", value=row.get("name", ""), disabled=bool(identity), key=prefix + "name")
                ticker = st.text_input("Ticker", value=row.get("ticker", ""), disabled=bool(identity), help="Use an exchange-qualified ticker where needed, e.g. VVSM.DE for the EUR UCITS listing.", key=prefix + "ticker")
                isin = st.text_input("ISIN (optional)", value=row.get("isin", ""), disabled=bool(identity), key=prefix + "isin")
                shares = st.number_input("Quantity held (total)", min_value=0.0, value=float(row.get("shares", 0)), format="%.10f", key=prefix + "shares")
                kinds = ['unknown', 'equity', 'etf', 'crypto', 'physical', 'cash', 'other']
                instrument_type = st.selectbox('Instrument type', kinds, index=kinds.index(row.get('instrument_type')) if row.get('instrument_type') in kinds else 0, key=prefix + 'instrument_type')
                exposure_kinds = ['unknown', 'equity', 'non_equity']
                exposure_kind = st.selectbox('Underlying exposure', exposure_kinds,
                                             index=exposure_kinds.index(row.get('exposure_kind')) if row.get('exposure_kind') in exposure_kinds else 0,
                                             format_func=lambda value: {'unknown': 'Unknown / mixed', 'equity': 'Equity', 'non_equity': 'Non-equity'}[value],
                                             key=prefix + 'exposure_kind', help='An ETF wrapper can hold equities or non-equity assets. Leave mixed or uncertain composition unknown.')
                quantity_unit = st.text_input('Quantity unit', value=row.get('quantity_unit', ''), key=prefix + 'quantity_unit', help='For physical holdings, specify the unit explicitly, such as grams.')
                portfolio = st.text_input("Portfolio / sleeve", value=row.get("portfolio", ""), key=prefix + "portfolio")
                account = st.text_input("Account / broker", value=row.get("account", ""), key=prefix + "account")
            with right:
                buy_in = st.number_input("Average buy-in per unit (optional)", min_value=0.0, value=_optional_number(row.get("acquisition_price")), format="%.6f", key=prefix + "buy_in")
                currency = st.text_input("Buy-in currency", value=row.get("acquisition_currency", "" if editing else "EUR"), help="Currency of your recorded purchase cost; it can differ from the live quote currency. Existing unlabeled buy-ins remain unspecified.", key=prefix + "currency")
                target_field = 'within_bucket_target' if allocation else 'target_allocation'
                initial_target = _optional_number(row.get(target_field))
                target = st.number_input("Target (% of bucket)" if allocation else "Target allocation % (optional)", min_value=0.0, max_value=100.0, value=None if initial_target is None else initial_target * 100, key=prefix + "target")
                bucket = ''
                if allocation:
                    bucket_ids = ['', *sorted(allocation.leaves())]
                    bucket = st.selectbox('Allocation bucket', bucket_ids, index=bucket_ids.index(row.get('bucket_id', '')), key=prefix + 'bucket')
                manual_price = st.number_input('Manual unit price (optional)', min_value=0., value=_optional_number(row.get('manual_price')), key=prefix + 'manual_price', help='Overrides market quotes. Clear to return to provider pricing.')
                manual_currency = st.text_input('Manual price currency', value=row.get('manual_price_currency', ''), key=prefix + 'manual_currency')
                manual_date = st.text_input('Manual price date (YYYY-MM-DD)', value=row.get('manual_price_date', ''), key=prefix + 'manual_date')
                extras = {}
                for column in metadata_dimensions(holdings):
                    if column not in {"portfolio", "account", "bucket_id", "instrument_type", "exposure_kind"}:
                        extras[column] = st.text_input(column.replace("_", " ").title(), value=row.get(column, ""), key=prefix + "extra_" + column)
                st.caption("Use Bulk add purchases to record savings-plan buys, or edit the total shares and average buy-in from your broker here. Buy-in is optional for allocation analysis.")
            submitted = st.form_submit_button("Save position", type="primary")
        if submitted:
            values = {
                "id": identity, "name": name, "ticker": ticker, "isin": isin,
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
                st.session_state["position_saved_notice"] = f"Saved {name} ({asset_id}) to {path}."
                st.rerun()
        st.caption("Existing instruments reuse their classifications. New instruments appear as Unclassified until you add their asset ID to classifications.yaml.")
        with st.expander("Buy-in and savings-plan help"):
            st.caption(f"Local storage: {path}")
            st.markdown(
                "Use your broker's current average buy-in / Einstandskurs and total shares as a convenient portfolio summary. "
                "Purchase confirmations provide each execution's shares, price, fees, and settlement currency. "
                "Broker-displayed buy-in conventions can differ.\n\n"
                "For purchases in one currency with no intervening sales or corporate actions: "
                "**average buy-in = sum of purchase costs, including buy fees / total shares**. "
                "For example, 10 shares at €100 and 5 at €120 give €1,600 / 15 = **€106.67 per share**, before fees. "
                "Savings-plan fractions count exactly like whole shares.\n\n"
                "After partial sales, transfers, or splits, reconcile against the broker's remaining-position data. "
                "This app stores summary positions and does not calculate tax lots or realized gains. "
                "Record costs in their original currency rather than converting historical purchases with today's FX rate.\n\n"
                "[Trade Republic: buy-in and purchase confirmations](https://support.traderepublic.com/de-de/1619)"
            )
