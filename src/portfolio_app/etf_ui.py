"""ETF snapshot presentation, separate from portfolio calculations."""

import pandas as pd
import streamlit as st

from portfolio_app.etf import FundSnapshot, fund_breakdown, matching_fund, constituent_resolver, snapshot_age_days
from portfolio_app.display_names import display_name
from portfolio_app.taxonomy import Classifications, describe, taxonomy_names
from portfolio_app.vaneck import refresh_snapshot
from portfolio_app.etf_sources import SOURCES, refresh_snapshot as refresh_provider_snapshot


def render_snapshot_controls(funds: list[FundSnapshot], *, demo: bool = False) -> list[FundSnapshot]:
    updated = []
    for fund in funds:
        source = SOURCES.get(fund.isin)
        if fund.isin == "IE00BMC38736" or source is not None:
            provider = source.provider if source else 'VanEck'
            label = f'Update from {provider}' + (' (proxy)' if fund.proxy_source else '')
            if st.button(label, disabled=demo, key=f"refresh_etf_{fund.fund_id}"):
                try:
                    with st.spinner(f"Downloading and validating {provider} holdings…"):
                        fund = (refresh_provider_snapshot if source else refresh_snapshot)(fund)
                    st.success(f"{provider} holdings checked; snapshot as of {fund.as_of.isoformat()}.")
                except Exception as exc:
                    st.warning(f"{provider} update failed; keeping the snapshot from {fund.as_of.isoformat()}: {exc}")
            age = snapshot_age_days(fund)
            caption = display_name(fund.name) if source else 'VanEck holdings'
            st.caption(f"{caption}: {fund.as_of.isoformat()} · {age} day(s) old")
            if age > 7:
                st.warning(f"{provider} holdings are {age} days old. Update the snapshot before relying on current ETF weights.")
            elif age < 0:
                st.warning(f"{provider} snapshot date is in the future; check the data file date.")
        updated.append(fund)
    return updated


def classified_fund_table(fund: FundSnapshot, holdings: pd.DataFrame, classifications: Classifications) -> pd.DataFrame:
    table = fund_breakdown(fund)
    resolve = constituent_resolver(holdings)
    asset_ids = [resolve(row)[0] for row in table.to_dict("records")]
    for name in taxonomy_names(classifications):
        table[f"classification:{name}"] = [describe(classifications, asset_id, name) for asset_id in asset_ids]
    return table.sort_values("weight", ascending=False, kind="stable", ignore_index=True)


def render_fund_details(funds: list[FundSnapshot], selected: pd.DataFrame, *, holdings: pd.DataFrame | None = None,
                        classifications: Classifications | None = None, show_tickers: bool = False,
                        classification_names: list[str] | None = None) -> None:
    for fund in funds:
        with st.expander(f"ETF breakdown: {display_name(fund.name)}"):
            if fund.proxy_source:
                st.info(f'Approximate breakdown · {fund.proxy_source}')
            st.caption(f"ISIN {fund.isin} · Holdings as of {fund.as_of.isoformat()} · {snapshot_age_days(fund)} day(s) old")
            st.markdown(f"[Holdings source]({fund.source})")
            st.caption(f'{len(fund.constituents):,} components · {100 * fund.constituents.weight.sum():.2f}% covered')
            if fund.notes:
                st.caption(fund.notes)
            table = classified_fund_table(fund, selected if holdings is None else holdings, classifications or {})
            table["name"] = table["name"].map(display_name)
            table["Fund allocation %"] = table["weight"] * 100
            fund_positions = [row for row in selected.to_dict("records") if matching_fund(row, [fund]) is not None]
            columns = ["name", "ticker", "Fund allocation %"]
            if fund_positions:
                values = pd.Series([row["current_value_eur"] for row in fund_positions])
                if values.notna().any():
                    table["Selected ETF exposure (EUR)"] = table["weight"] * values.sum()
                    columns.append("Selected ETF exposure (EUR)")
                if values.isna().any():
                    st.caption("Exposure amounts exclude ETF positions that could not be valued.")
            else:
                st.caption("No position in this ETF is selected. Fund percentages are available independently of your holdings.")
            columns += [column for column in table if column.startswith("classification:") and
                        (classification_names is None or column.removeprefix("classification:") in classification_names)]
            st.dataframe(table[columns], hide_index=True, width="stretch", height=600 if len(table) > 100 else "content", column_config={
                "name": "Holding", "ticker": "Ticker" if show_tickers else None,
                "Fund allocation %": st.column_config.NumberColumn(format="%.2f %%"),
                "Selected ETF exposure (EUR)": st.column_config.NumberColumn(format="€ %.2f"),
            } | {column: column.removeprefix("classification:").replace("_", " ").title() for column in columns if column.startswith("classification:")})
            st.caption("Other retains the weight not assigned to named constituents, including cash and rounding residuals. Partial holdings are never scaled up to 100%.")
