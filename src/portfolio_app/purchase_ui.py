"""Batch purchase entry and review, separate from calculation and persistence."""

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
    destination = st.selectbox("Purchase destination", ["", *positions], help='Choose the holding to update, or create a new position from these purchases.', format_func=lambda value: positions.get(value, "New position"), key="position_edit_bulk_destination")
    position_id = destination or None
    row = holdings.loc[holdings["position_id"] == destination].iloc[0].to_dict() if destination else {}
    identity = row.get("id", "")
    if not destination:
        labels = {item.id: f"{display_name(item.name)} ({item.ticker or item.id.upper()})" for item in holdings.itertuples()}
        if pending := st.session_state.pop("position_edit_pending_instrument", None):
            if pending in labels:
                st.session_state["position_edit_instrument"] = pending
        identity = st.selectbox("Existing instrument", ["", *labels], help='Reuse a saved instrument identity when adding a position in another account or category.', format_func=lambda value: labels.get(value, "New instrument"), key="position_edit_instrument")
        if identity:
            instrument = holdings.loc[holdings["id"] == identity].iloc[0]
            row = {key: instrument[key] for key in ("id", "name", "ticker", "isin")}
    mode_label = st.radio("How to apply purchases", ["Add new purchases", "Calculate buy-in for shares already held"] if destination else ["Add new purchases"], help='Add purchases to the existing quantity, or calculate cost for purchases already included in it.', key="position_edit_bulk_mode")
    mode = "add" if mode_label == "Add new purchases" else "reconcile"
    if mode == "add":
        st.caption("Each row is an additional purchase for this instrument, account, and portfolio. Saved shares increase by the batch total.")
    else:
        st.caption("Enter the purchases making up all shares currently held. Their total must match the position; this updates buy-in without adding shares. Use this only when those purchases have no intervening sales or splits.")
    context = (str(path.resolve()), destination, identity, mode)
    if st.session_state.get("position_edit_bulk_context") != context:
        st.session_state["position_edit_bulk_context"] = context
        st.session_state["position_edit_bulk_revision"] = snapshot.revision
    revision = st.session_state["position_edit_bulk_revision"]
    try:
        opening = read_opening(path, revision, position_id)
    except (DataError, OSError, UnicodeError) as exc:
        st.warning(str(exc))
        return "" if st.button("Reload position form", help='Discard this stale draft and load the latest saved holding.') else None
    render_purchase_history(row)
    prefix = f"position_edit_bulk_{destination}_{identity}_"
    if not destination and not identity:
        render_instrument_search(prefix, holdings, path.parent / ".cache" / "yahoo", disabled=demo)
    fields = {"id": identity}
    left, right = st.columns(2)
    with left:
        for field, label in (("name", "Instrument name"), ("ticker", "Ticker"), ("isin", "ISIN (optional)")):
            fields[field] = st.text_input(label, help='Instrument identity or optional account grouping for the destination holding; shared identities retain existing classifications.', value=row.get(field, ""), disabled=bool(identity), key=prefix + field)
    with right:
        for field, label in (("portfolio", "Portfolio / sleeve"), ("account", "Account / broker")):
            fields[field] = st.text_input(label, help='Instrument identity or optional account grouping for the destination holding; shared identities retain existing classifications.', value=row.get(field, ""), disabled=bool(destination), key=prefix + field)
        currency = st.text_input("Purchase currency", help='Three-letter currency shared by every purchase price and fee in this batch.', value=opening.get("acquisition_currency") or "EUR", key=prefix + "currency")
    if not destination:
        for column in metadata_dimensions(holdings):
            if column not in {"portfolio", "account"}:
                fields[column] = st.text_input(column.replace("_", " ").title(), help='Optional saved classification or grouping label for this position.', key=prefix + "extra_" + column)
    source = st.radio("Purchase input", ["Table", "Paste CSV / TSV", "Upload CSV"], help='Enter rows directly, paste a table, or upload a purchase file.', horizontal=True, key="position_edit_bulk_source")
    st.caption("Columns: date (YYYY-MM-DD, optional), shares, price per share, fees (optional; blank means zero). Prices and fees must use the selected currency. Blank prices remain unknown. Use decimal points or commas without thousands separators.")
    rows = []
    try:
        if source == "Table":
            initial = pd.DataFrame([{column: "" for column in COLUMNS}])
            table = st.data_editor(initial, num_rows="dynamic", hide_index=True, width="stretch", key=prefix + "rows", column_config={
                "date": st.column_config.TextColumn("Date (optional)", help='Purchase date as YYYY-MM-DD. Leave blank when unknown.'),
                "shares": st.column_config.TextColumn("Shares", help='Number of units purchased in this row.'),
                "price": st.column_config.TextColumn("Price per share", help='Purchase cost per unit in the selected purchase currency. Blank means unknown.'),
                "fees": st.column_config.TextColumn("Fees (optional)", help='Total purchase fees for this row. Blank means zero.'),
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
    if summary.average is None:
        st.warning("The resulting buy-in will remain unknown because some purchase prices or the existing position's cost are missing. Known purchase details will still be saved.")
    allow_repeat = False
    if repeat:
        st.warning("This batch matches a batch saved earlier. Saving again would increase your shares again.")
        batch_key = sha256(repr((purchases, currency)).encode()).hexdigest()
        allow_repeat = st.checkbox("These are additional purchases despite matching a saved batch", help='Confirm these are separate real purchases; saving will increase quantity again.', key=prefix + "repeat_" + batch_key)
    st.caption("All rows are counted, including identical rows. Saving applies the whole batch once and keeps the entered purchase details locally. After saving, the entry form clears.")
    if st.button("Save purchase batch", help='Save all reviewed purchases and the resulting holding together, once.', type="primary", disabled=repeat and not allow_repeat):
        try:
            asset_id = save_purchase_batch(path, fields, rows, currency=currency, expected_revision=revision,
                                           position_id=position_id, mode=mode, allow_repeat=allow_repeat,
                                           validate=lambda frame: validate_fund_listings(frame, funds))
        except (DataError, OSError, UnicodeError) as exc:
            st.error(f"Purchases were not saved: {exc}")
        else:
            return f"Saved {len(purchases)} purchases for {asset_id}; the position now holds {summary.total_shares} shares."
    return None
