"""Optional aggregate-history setup, separate from custody journal activation."""
from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from portfolio_app.asset_history_store import load_settings, save_settings
from portfolio_app.security_history import effective_holdings, replay_securities
from portfolio_app.transaction_csv import digest


def render_asset_history_setup(directory, journal, journal_revision, snapshot, rows):
    if not rows or snapshot.holdings.empty:
        return
    source = rows[0].facts['source_id']
    key = 'asset_history_'+digest(source)[:12]+'_'
    if any(r.identity not in {r.id for r in journal.transaction_imports} for r in rows):
        return
    with st.expander('Approximate asset history'):
        st.caption('Combine your selected locations into one series per asset. Internal transfers and staking principal cancel; rewards count as income and fees reduce gain. Uses daily market prices, independently of FIFO costs.')
        previous, revision = load_settings(directory)
        previous = previous if previous and previous.source_id == source else None
        accounts = sorted({a for r in rows for a, _ in r.pairs})
        owned = st.multiselect('Owned locations included in history', accounts,
            default=[a for a in previous.owned_accounts if a in accounts] if previous else [], key=key+'accounts')
        if not owned:
            return
        assets = sorted({asset for r in rows for account, asset in r.pairs if account in owned})
        holdings = effective_holdings(snapshot.holdings, journal.securities)
        states = replay_securities(journal.securities).positions
        ids = list(holdings.position_id)
        names = {r.position_id:r['name'] for _, r in holdings.iterrows()}
        anchors = {}
        scope_label = st.text_input('History scope name', value=previous.scope_label if previous else 'Selected owned locations', key=key+'label')
        for asset in assets:
            matches = holdings.loc[holdings.id.str.upper().eq(asset.upper()), 'position_id'].tolist()
            saved = next((p for p, a in previous.anchors.items() if a['asset'] == asset and p in ids), None) if previous else None
            default = saved or (matches[0] if len(matches) == 1 else None)
            options = [None, *ids]
            position = st.selectbox('Position for '+asset, options, index=options.index(default),
                format_func=lambda p: names.get(p, 'Exclude this asset'), key=key+'position_'+asset)
            if position:
                if position in anchors:
                    st.caption('Map different assets to different positions.')
                    return
                row = holdings.loc[holdings.position_id.eq(position)].iloc[0]
                quantity = states[position].quantity if position in states else Decimal(str(row.shares))
                ticker = row.ticker
                if previous and position in previous.anchors and previous.anchors[position]['asset'] == asset:
                    quantity = Decimal(previous.anchors[position]['quantity'])
                    ticker = ticker or previous.anchors[position]['ticker']
                anchors[position] = dict(asset=asset, quantity=str(quantity), ticker=ticker)
        st.dataframe(pd.DataFrame([dict(Asset=a['asset'], Position=names[p], Quantity=a['quantity'])
            for p,a in anchors.items()]), hide_index=True, width='stretch')
        stamp = st.date_input('Ending balance reference date',
            value=date.fromisoformat(previous.anchor_date) if previous else date.today(), max_value=date.today(), key=key+'anchor_date')
        accepted = st.checkbox('Use these ending balances to reconstruct the supplied history approximately',
            help='This does not confirm a zero opening or a complete transaction history. Quantities are reconstructed backwards from these balances. Choose the date those balances refer to; do not substitute an export date for an observation date.', key=key+'accept')
        if st.button('Enable asset history', disabled=not accepted or not anchors, key=key+'enable'):
            from portfolio_app.portfolio_settings import load_settings as portfolio_settings
            save_settings(directory, dict(version=1, method='closing-balance-daily-dietz-v1', source_id=source,
                owned_accounts=owned, anchor_date=stamp.isoformat(), anchors=anchors,
                currency=portfolio_settings(directory).reporting_currency, scope_label=scope_label,
                note='Ending balance reference chosen during import review; reconstruction assumes the listed movements.'),
                expected_revision=revision, expected_journal_revision=journal_revision,
                expected_holdings_revision=snapshot.revision)
            st.caption('Asset history enabled. Open a position’s History view.')
