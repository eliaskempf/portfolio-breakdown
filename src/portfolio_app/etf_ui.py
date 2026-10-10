"""ETF snapshot presentation, separate from portfolio calculations."""
from portfolio_app.ui_help import column_help

import pandas as pd
import plotly.express as px
import streamlit as st

from portfolio_app.etf import FundSnapshot, fund_breakdown, matching_fund, constituent_resolver, snapshot_age_days
from portfolio_app.display_names import display_name, compact_fund_name, instrument_name
from portfolio_app.taxonomy import Classifications, describe, taxonomy_names
from portfolio_app.vaneck import refresh_snapshot
from portfolio_app.etf_sources import SOURCES, refresh_snapshot as refresh_provider_snapshot
from portfolio_app.list_ui import BOUNDED_LIST_HEIGHT, ListColumn, frame_rows, render_list


def render_snapshot_controls(funds: list[FundSnapshot], *, demo: bool = False) -> list[FundSnapshot]:
    updated = []
    for fund in funds:
        source = SOURCES.get(fund.isin)
        if fund.isin == "IE00BMC38736" or source is not None:
            provider = source.provider if source else 'VanEck'
            label = f'Update from {provider}' + (' (proxy)' if fund.proxy_source else '')
            if st.button(label, help='Download and validate a fund snapshot from its configured provider. Offline demos retain their synthetic snapshots.', disabled=demo, key=f"refresh_etf_{fund.fund_id}"):
                try:
                    with st.spinner(f"Downloading and validating {provider} holdings…"):
                        fund = (refresh_provider_snapshot if source else refresh_snapshot)(fund)
                    st.success(f"{provider} holdings checked; snapshot as of {fund.as_of.isoformat()}.")
                except Exception as exc:
                    st.warning(f"{provider} update failed; keeping the snapshot from {fund.as_of.isoformat()}: {exc}")
            age = snapshot_age_days(fund)
            caption = compact_fund_name(fund.name) if source else 'VanEck holdings'
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
                        classification_names: list[str] | None = None, key_prefix: str = '',
                        percentages_only: bool = False) -> None:
    for fund in funds:
        source = selected if holdings is None else holdings
        position = next((row for row in source.to_dict('records') if matching_fund(row, [fund]) is not None), None)
        label = instrument_name(position) if position else compact_fund_name(fund.name)
        panel = st.expander(f"ETF breakdown: {label}", key=f'{key_prefix}etf_detail_{fund.fund_id}', on_change='rerun')
        if not panel.open:
            continue
        with panel:
            if fund.proxy_source:
                st.info(f'Approximate breakdown · {fund.proxy_source}')
            st.caption(f"ISIN {fund.isin} · Holdings as of {fund.as_of.isoformat()} · {snapshot_age_days(fund)} day(s) old")
            if fund.source.startswith(('https://', 'http://')):
                st.markdown(f"[Holdings source]({fund.source})")
            else:
                st.caption(f'Source: {fund.source}')
            st.caption(f'{len(fund.constituents):,} components · {100 * fund.constituents.weight.sum():.2f}% covered')
            if fund.notes:
                st.caption(fund.notes)
            view = st.segmented_control('Breakdown view', ['Holdings', 'Summary'], help='Choose between fund composition summaries and individual underlying holdings.',
                default='Summary' if fund.asset_class in {'fixed_income', 'money_market'} else 'Holdings',
                key=f'{key_prefix}fund_view_{fund.fund_id}')
            if view == 'Summary':
                render_fund_summary(fund, key_prefix=key_prefix)
                continue
            if fund.breakdown_basis == 'economic':
                st.info('Economic allocation; the substitute basket below is separate from portfolio exposure.')
            if fund.basket is not None:
                with st.expander('Actual substitute basket · excluded from portfolio exposure'):
                    st.caption('These are fund-held securities, not the overnight-rate economic allocation. Signed weights are retained. '
                               f'Net basket coverage: {fund.basket.weight.sum():.4%}.')
                    basket = fund.basket.copy()
                    basket['Basket weight %'] = basket.pop('weight') * 100
                    st.dataframe(basket.drop(columns=['constituent_id'], errors='ignore'), hide_index=True, height=350)
            table = classified_fund_table(fund, selected if holdings is None else holdings, classifications or {})
            table["name"] = table["name"].map(display_name)
            table["Fund allocation %"] = table["weight"] * 100
            fund_positions = [] if percentages_only else [row for row in selected.to_dict("records") if matching_fund(row, [fund]) is not None]
            columns = ["name", "ticker", "Fund allocation %"]
            if fund.asset_class in {'fixed_income', 'money_market'}:
                columns += [c for c in ('isin', 'instrument_type', 'issuer', 'market_currency', 'maturity', 'credit_rating') if c in table]
            if fund_positions:
                values = pd.Series([row["current_value_reporting"] for row in fund_positions])
                if values.notna().any():
                    table["Selected ETF exposure"] = table["weight"] * values.sum()
                    columns.append("Selected ETF exposure")
                if values.isna().any():
                    st.caption("Exposure amounts exclude ETF positions that could not be valued.")
            elif percentages_only:
                st.caption('Fund composition is available independently of position pricing.')
            else:
                st.caption("No position in this ETF is selected. Fund percentages are available independently of your holdings.")
            columns += [column for column in table if column.startswith("classification:") and
                        (classification_names is None or column.removeprefix("classification:") in classification_names)]
            labels = {'name': 'Holding', 'ticker': 'Ticker'}
            specs = [ListColumn(column, labels.get(column, column.removeprefix('classification:').replace('_', ' ').title()
                                if column.startswith('classification:') else column),
                                numeric=column in {'Fund allocation %', 'Selected ETF exposure'})
                     for column in columns if column != 'ticker' or show_tickers]
            render_list(frame_rows(table[columns]), specs, key=f'{key_prefix}etf_holdings_{fund.fund_id}',
                        context=f'{key_prefix}etf_holdings_{fund.fund_id}', title=f'{label} holdings', max_height=BOUNDED_LIST_HEIGHT,
                        default_sort='Fund allocation %', search_label='Filter ETF holdings', search_fields=['name', 'ticker'])
            st.caption("Other retains the weight not assigned to named constituents, including cash and rounding residuals. Partial holdings are never scaled up to 100%.")


def render_fund_summary(fund, *, key_prefix=''):
    from portfolio_app.fund_summary import composition_summary, DIMENSIONS
    if fund.breakdown_basis == 'economic':
        description = ' · '.join(fund.constituents['name'].astype(str))
        basket_note = 'The substitute basket is available under Holdings.' if fund.basket is not None else 'Substitute basket unavailable.'
        st.info(f'{description}. This represents the economic benchmark, not a bank deposit. {basket_note}')
        st.caption('100% economic representation · Basket coverage is reported separately. No basket securities enter portfolio allocation.')
        return
    dimension = st.selectbox('Summarize by', list(DIMENSIONS), help='Choose the dimension used to summarize fund holdings.', key=f'{key_prefix}fund_summary_{fund.fund_id}')
    frame = composition_summary(fund, dimension)
    provider = fund.summaries.get(dimension)
    if provider and 'rows' in provider and frame[dimension].isin(['Unknown', 'Other']).all():
        frame = pd.DataFrame(provider['rows']).rename(columns={'label': dimension, 'percentage': 'Fund allocation %'})
        st.caption(f"Provider aggregate · {provider['as_of']} · These categories are not assigned to individual securities.")
        st.markdown(f"[Summary source]({provider['source']})")
    else:
        coverage = fund.constituents.weight.sum()
        if coverage < 1 - 1e-8:
            st.caption(f'Partial holdings summary · {coverage:.2%} covered; Other retains the remainder. '
                       'These are not whole-fund issuer, country or currency totals.')
        st.caption(f'Calculated from holdings dated {fund.as_of}; missing metadata remains Unknown.')
    if dimension == 'Denomination currency':
        st.caption('Security denomination, not net currency risk after hedging.')
    if dimension == 'Issuer':
        st.caption('Issuer identifiers group bonds without combining their security identities or merging with shares.')
    chart = px.bar(frame, x='Fund allocation %', y=dimension, orientation='h')
    chart.update_layout(yaxis={'categoryorder': 'total ascending'}, height=max(280, min(650, len(frame) * 28)))
    st.plotly_chart(chart, width='stretch', key=f'{key_prefix}fund_summary_chart_{fund.fund_id}',
                    config={'showSendToCloud': False})
    st.dataframe(frame, hide_index=True, width='stretch')
    labels = {'modelOad': 'Effective duration (years)', 'effectiveDuration': 'Effective duration (years)',
              'weightedAvgLife': 'Weighted average maturity (years)', 'weightedAverageMaturity': 'Weighted average maturity (years)',
              'yieldToWorst': 'Yield to worst (%)', 'weightedAverageYieldToMaturity': 'Yield to maturity (%)',
              'weightedAvgCoupon': 'Weighted average coupon (%)', 'weightedAverageCoupon': 'Weighted average coupon (%)'}
    for key, item in fund.summaries.items():
        if key in labels and 'value' in item:
            st.caption(f"{labels[key]}: {item['value']:.2f} · Provider figure as of {item['as_of']}")
            st.markdown(f"[Metric source]({item['source']})")
