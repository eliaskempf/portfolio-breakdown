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


def render_position_editor(path: Path, snapshot: HoldingsSnapshot, funds: list[FundSnapshot], *, demo: bool = False, embedded: bool = False) -> None:
    holdings = snapshot.holdings
    if message := st.session_state.pop("position_saved_notice", None):
        st.success(message)
    with (st.container() if embedded else st.expander("Manage positions", expanded=holdings.empty)):
        st.caption("Add investments, update a holding, or record a batch of purchases.")
        action = st.radio("Position action", ["Add position", "Edit position", "Bulk add purchases"], horizontal=True, key="position_edit_action")
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
        else:
            labels = {row.id: f"{display_name(row.name)} ({row.ticker or row.id.upper()})" for row in holdings.itertuples()}
            if pending := st.session_state.pop("position_edit_pending_instrument", None):
                if pending in labels:
                    st.session_state["position_edit_instrument"] = pending
            identity = st.selectbox("Existing instrument", ["", *labels], format_func=lambda value: labels[value] if value else "New instrument", key="position_edit_instrument")
            row = holdings.loc[holdings["id"] == identity].iloc[0].to_dict() if identity else {}
            row = {column: row.get(column, "") for column in ("id", "name", "ticker", "isin")}
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
                shares = st.number_input("Shares held (total)", min_value=0.0, value=float(row.get("shares", 0)), format="%.10f", key=prefix + "shares")
                portfolio = st.text_input("Portfolio / sleeve", value=row.get("portfolio", ""), key=prefix + "portfolio")
                account = st.text_input("Account / broker", value=row.get("account", ""), key=prefix + "account")
            with right:
                buy_in = st.number_input("Average buy-in per share (optional)", min_value=0.0, value=_optional_number(row.get("acquisition_price")), format="%.6f", key=prefix + "buy_in")
                currency = st.text_input("Buy-in currency", value=row.get("acquisition_currency", "" if editing else "EUR"), help="Currency of your recorded purchase cost; it can differ from the live quote currency. Existing unlabeled buy-ins remain unspecified.", key=prefix + "currency")
                initial_target = _optional_number(row.get("target_allocation"))
                target = st.number_input("Target allocation % (optional)", min_value=0.0, max_value=100.0, value=None if initial_target is None else initial_target * 100, key=prefix + "target")
                extras = {}
                for column in metadata_dimensions(holdings):
                    if column not in {"portfolio", "account"}:
                        extras[column] = st.text_input(column.replace("_", " ").title(), value=row.get(column, ""), key=prefix + "extra_" + column)
                st.caption("Use Bulk add purchases to record savings-plan buys, or edit the total shares and average buy-in from your broker here. Buy-in is optional for allocation analysis.")
            submitted = st.form_submit_button("Save position", type="primary")
        if submitted:
            values = {
                "id": identity, "name": name, "ticker": ticker, "isin": isin,
                "shares": str(shares), "portfolio": portfolio, "account": account,
                "acquisition_price": "" if buy_in is None else str(buy_in),
                "acquisition_currency": currency if buy_in is not None else "",
                "target_allocation": "" if target is None else str(target / 100), **extras,
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
