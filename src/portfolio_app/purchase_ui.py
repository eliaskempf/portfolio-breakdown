"""Batch purchase entry and review, separate from calculation and persistence."""

from portfolio_app.currency_display import reporting_currency

from pathlib import Path
from hashlib import sha256

import pandas as pd
import streamlit as st

from portfolio_app.display_names import display_name
from portfolio_app.etf import FundSnapshot, validate_fund_listings
from portfolio_app.holdings import DataError, metadata_dimensions
from portfolio_app.instrument_ui import render_instrument_search
from portfolio_app.positions import HoldingsSnapshot
from portfolio_app.purchases import (
    COLUMNS, HISTORY_COLUMN, parse_purchase_text, read_opening, read_purchase_history, repeated_batch,
    save_purchase_batch, summarize_purchases, validate_purchases,
)


def render_purchase_history(row: dict) -> None:
    if not row.get(HISTORY_COLUMN):
        return
    with st.expander("Saved purchase batches"):
        st.caption("These are the inputs saved with earlier updates. Historical cost recalculations may overlap earlier batches; do not sum batches to reconstruct your current holding. Manual summary edits remain authoritative.")
        try:
            batches = read_purchase_history(row[HISTORY_COLUMN])
        except DataError as exc:
            st.warning(str(exc))
            return
        for number, batch in enumerate(batches, start=1):
            label = "New purchases" if batch["mode"] == "add" else "Historical cost calculation"
            st.caption(f"Batch {number} · {label} · {batch['currency']} · saved {batch['saved_at']}")
            st.dataframe(pd.DataFrame(batch["purchases"]), hide_index=True, width="stretch")


def render_bulk_purchases(path: Path, snapshot: HoldingsSnapshot, funds: list[FundSnapshot], *, demo: bool = False) -> str | None:
    """Return a saved notice, an empty string for reload, or None while editing."""
    holdings = snapshot.holdings
    positions = {
        row.position_id: f"{display_name(row.name)} ({row.ticker or row.id.upper()}) · {row.portfolio or 'No portfolio'} · {row.account or 'No account'}"
        for row in holdings.itertuples()
    }
    destination = st.selectbox("Purchase destination", ["", *positions], format_func=lambda value: positions.get(value, "New position"), key="position_edit_bulk_destination")
    position_id = destination or None
    row = holdings.loc[holdings["position_id"] == destination].iloc[0].to_dict() if destination else {}
    identity = row.get("id", "")
    if not destination:
        labels = {item.id: f"{display_name(item.name)} ({item.ticker or item.id.upper()})" for item in holdings.itertuples()}
        if pending := st.session_state.pop("position_edit_pending_instrument", None):
            if pending in labels:
                st.session_state["position_edit_instrument"] = pending
        identity = st.selectbox("Existing instrument", ["", *labels], format_func=lambda value: labels.get(value, "New instrument"), key="position_edit_instrument")
        if identity:
            instrument = holdings.loc[holdings["id"] == identity].iloc[0]
            row = {key: instrument[key] for key in ("id", "name", "ticker", "isin")}
    mode_label = st.radio("How to apply purchases", ["Add new purchases", "Calculate buy-in for shares already held"] if destination else ["Add new purchases"], key="position_edit_bulk_mode")
    mode = "add" if mode_label == "Add new purchases" else "reconcile"
    if mode == "add":
        st.caption("Each row is an additional purchase for this instrument, account, and portfolio. Saved shares increase by the batch total.")
    else:
        st.caption("Enter the purchases making up all shares currently held. Their total must match the position; this updates buy-in without adding shares. Use this only when those purchases have no intervening sales or splits.")
    context = (str(path.resolve()), destination, identity, mode, reporting_currency())
    if st.session_state.get("position_edit_bulk_context") != context:
        st.session_state["position_edit_bulk_context"] = context
        st.session_state["position_edit_bulk_revision"] = snapshot.revision
    revision = st.session_state["position_edit_bulk_revision"]
    try:
        opening = read_opening(path, revision, position_id)
    except (DataError, OSError, UnicodeError) as exc:
        st.warning(str(exc))
        return "" if st.button("Reload position form") else None
    render_purchase_history(row)
    prefix = f"position_edit_bulk_{destination}_{identity}_{reporting_currency()}_"
    if not destination and not identity:
        render_instrument_search(prefix, holdings, path.parent / ".cache" / "yahoo", disabled=demo)
    fields = {"id": identity}
    left, right = st.columns(2)
    with left:
        for field, label in (("name", "Instrument name"), ("ticker", "Ticker"), ("isin", "ISIN (optional)")):
            fields[field] = st.text_input(label, value=row.get(field, ""), disabled=bool(identity), key=prefix + field)
    with right:
        for field, label in (("portfolio", "Portfolio / sleeve"), ("account", "Account / broker")):
            fields[field] = st.text_input(label, value=row.get(field, ""), disabled=bool(destination), key=prefix + field)
        currency = st.text_input("Purchase currency", value=reporting_currency(), key=prefix + "currency")
    if not destination:
        for column in metadata_dimensions(holdings):
            if column not in {"portfolio", "account"}:
                fields[column] = st.text_input(column.replace("_", " ").title(), key=prefix + "extra_" + column)
    source = st.radio("Purchase input", ["Table", "Paste CSV / TSV", "Upload CSV"], horizontal=True, key="position_edit_bulk_source")
    st.caption("Columns: date (YYYY-MM-DD, optional), shares, price per share, fees (optional; blank means zero), fx_rate (optional; reporting-currency units per purchase-currency unit). Prices and fees must use the selected currency. Blank prices remain unknown. Use decimal points or commas without thousands separators.")
    rows = []
    try:
        if source == "Table":
            initial = pd.DataFrame([{column: "" for column in COLUMNS}])
            table = st.data_editor(initial, num_rows="dynamic", hide_index=True, width="stretch", key=prefix + "rows", column_config={
                "date": st.column_config.TextColumn("Date (optional)"),
                "shares": st.column_config.TextColumn("Shares"),
                "price": st.column_config.TextColumn("Price per share"),
                "fees": st.column_config.TextColumn("Fees (optional)"),
                "fx_rate": st.column_config.TextColumn(f"FX rate to {reporting_currency()} (optional)"),
            })
            rows = table.fillna("").to_dict("records")
            if not any(any(str(value).strip() for value in item.values()) for item in rows):
                rows = []
        elif source == "Paste CSV / TSV":
            content = st.text_area("Paste purchases with headers", value="date,shares,price,fees\n", height=160, key=prefix + "paste")
            rows = parse_purchase_text(content)
        else:
            upload = st.file_uploader("Purchase CSV or TSV", type=["csv", "tsv"], key=prefix + "upload")
            if upload is not None:
                if upload.size > 1_000_000:
                    raise DataError("Purchase input is too large (maximum 1 MB).")
                rows = parse_purchase_text(upload.getvalue().decode("utf-8-sig"))
        if not rows:
            st.info("Enter purchases to preview the resulting position before saving.")
            return None
        purchases = validate_purchases(rows)
        summary = summarize_purchases(purchases, currency, opening, mode=mode)
        repeat = mode == "add" and repeated_batch(purchases, currency, opening)
    except (DataError, UnicodeError) as exc:
        st.error(str(exc) if isinstance(exc, DataError) else "Use a UTF-8 encoded CSV or TSV file.")
        return None
    st.markdown("**Purchase preview**")
    st.dataframe(pd.DataFrame([item.record() for item in purchases]), hide_index=True, width="stretch")
    st.table(pd.DataFrame([
        {"Measure": "Shares before", "Value": str(summary.previous_shares)},
        {"Measure": "Shares in batch", "Value": str(summary.batch_shares)},
        {"Measure": "Shares after save", "Value": str(summary.total_shares)},
        {"Measure": "Batch fees", "Value": f"{summary.fees} {summary.currency}"},
        {"Measure": "Batch cost including fees", "Value": "Unknown" if summary.batch_cost is None else f"{summary.batch_cost} {summary.currency}"},
        {"Measure": "Average buy-in after save", "Value": "Unknown" if summary.average is None else f"{summary.average:.6f} {summary.currency}"},
    ]))
    from portfolio_app.purchases import purchase_components
    from portfolio_app.currency_ui import historical_for, ui_prices
    from portfolio_app.cost_basis import resolve_cost, estimate_missing, freeze_historical
    historical = historical_for(path.parent, demo=demo)
    parts = purchase_components(purchases, currency, opening, mode=mode, reporting_currency=reporting_currency())
    resolved = resolve_cost(parts, reporting_currency(), historical)
    if resolved.amount is None:
        st.warning(resolved.note + ' Available current values remain included.')
        if st.checkbox('Review latest FX approximation for missing conversions', key=prefix + 'estimate'):
            try:
                estimated_parts = estimate_missing(parts, reporting_currency(), ui_prices(path.parent, demo), historical)
                converted = resolve_cost(estimated_parts, reporting_currency(), historical)
                records = [dict(Currency=p['currency'], **p['conversions'][reporting_currency()]) for p in estimated_parts
                           if p.get('conversions', {}).get(reporting_currency(), {}).get('method') == 'estimate']
                st.dataframe(pd.DataFrame(records), hide_index=True)
                st.warning('Using current FX for historical costs may hide currency gains or losses. Confirmed rates stay fixed.')
                signature = sha256(repr([(p['currency'], p['amount'], p.get('conversions', {}).get(reporting_currency(), {}).get('rate')) for p in estimated_parts]).encode()).hexdigest()[:12]
                if st.checkbox('Confirm these FX approximations', key=prefix + 'confirm_' + signature):
                    parts, resolved = estimated_parts, converted
            except DataError as exc:
                st.warning(str(exc))
    if resolved.amount is not None:
        st.caption(f'Combined buy-in: {resolved.amount:,.2f} {reporting_currency()} · {resolved.note or "Recorded cost"}')
    if summary.average is None and resolved.amount is None:
        st.warning("The resulting buy-in will remain unknown because some purchase prices or the existing position's cost are missing. Known purchase details will still be saved.")
    from portfolio_app.currency_ui import render_fx_progress
    render_fx_progress(path.parent)
    allow_repeat = False
    if repeat:
        st.warning("This batch matches a batch saved earlier. Saving again would increase your shares again.")
        batch_key = sha256(repr((purchases, currency)).encode()).hexdigest()
        allow_repeat = st.checkbox("These are additional purchases despite matching a saved batch", key=prefix + "repeat_" + batch_key)
    st.caption("All rows are counted, including identical rows. Saving applies the whole batch once and keeps the entered purchase details locally. After saving, the entry form clears.")
    if st.button("Save purchase batch", type="primary", disabled=repeat and not allow_repeat):
        try:
            asset_id = save_purchase_batch(path, fields, rows, currency=currency, expected_revision=revision,
                                           position_id=position_id, mode=mode, allow_repeat=allow_repeat,
                                           reporting_currency=reporting_currency(), historical=historical, components=freeze_historical(parts, reporting_currency(), historical),
                                           validate=lambda frame: validate_fund_listings(frame, funds))
        except (DataError, OSError, UnicodeError) as exc:
            st.error(f"Purchases were not saved: {exc}")
        else:
            return f"Saved {len(purchases)} purchases for {asset_id}; the position now holds {summary.total_shares} shares."
    return None
