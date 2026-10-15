"""On-demand settlement history kept out of the main position summary."""
import streamlit as st

from portfolio_app.futures_mtm import daily_mtm, load_settlements, mtm_stats, refresh_settlements, settlement_dates
from portfolio_app.holdings import DataError
from portfolio_app.money import euros


def render_daily_mtm(position, directory):
    with st.expander('Daily MTM'):
        if directory is None:
            st.info('Open this position in its saved portfolio to load settlements.')
            return
        try:
            settlement_dates(position)
            if not position.contracts:
                st.info('Closed contract: historical contract quantities are needed to reconstruct daily MTM.')
                return
            if st.button('Load / refresh daily settlements', key='future_mtm_refresh'):
                with st.spinner('Reading dated Eurex settlements…'):
                    failed = refresh_settlements(directory, position, refresh=True)
                if failed:
                    st.warning(f'{len(failed)} weekday(s) could not be refreshed. Previously verified observations are retained.')
            quotes = load_settlements(directory).get(position.contract_expiry, [])
            table = daily_mtm(position, quotes)
        except (DataError, OSError) as exc:
            st.info(str(exc))
            return
        if table.empty or table['Settlement (points)'].notna().sum() == 0:
            st.info('Load the current contract’s daily settlements to see MTM and statistics.')
            return
        stats = mtm_stats(table)
        st.caption('Current contract · constant saved quantity · gross P&L before fees · simulated MTM, not broker cash transfers')
        if not stats['complete']:
            st.warning('Some weekdays have no verified settlement (holiday or unavailable). Daily changes across those gaps and full-period statistics are withheld.')
        cols = st.columns(4)
        for col, label, key in zip(cols, ['Settlement P&L', 'Best day', 'Worst day', 'Maximum drawdown'], ['total', 'best', 'worst', 'drawdown']):
            col.metric(label, euros(stats[key]))
        if stats['complete']:
            st.caption(f"{stats['up']} positive · {stats['down']} negative · {stats['flat']} unchanged observations")
        if stats['total'] is not None and abs(stats['total'] - position.pnl) > .01:
            st.caption('The saved mock mark differs from the observed settlement; settlement P&L and the position’s marked gain differ.')
        st.dataframe(table, hide_index=True, width='stretch', column_config={'Source': st.column_config.LinkColumn('Source')})
