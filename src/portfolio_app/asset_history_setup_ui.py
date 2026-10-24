"""Optional asset view configuration; openings and activation use normal history."""
import streamlit as st

from portfolio_app.asset_history_store import load_settings, save_settings
from portfolio_app.transaction_csv import digest


def render_asset_history_setup(directory, journal, journal_revision, snapshot, rows):
    if not rows or snapshot.holdings.empty:
        return
    source = rows[0].facts['source_id']
    key = 'asset_history_'+digest(source)[:12]+'_'
    if any(r.identity not in {r.id for r in journal.transaction_imports} for r in rows):
        return
    with st.expander('Asset history'):
        previous, revision = load_settings(directory)
        previous = previous if previous and previous.source_id == source else None
        if previous and previous.legacy:
            st.caption('Ending-balance reconstruction is retired. Evidence and old settings are retained; review all owned locations to use forward history.')
        accounts = sorted({a for r in rows for a, _ in r.pairs} | set(previous.owned_accounts if previous else ()))
        owned = st.multiselect('Owned locations included in history', accounts,
            default=list(previous.owned_accounts) if previous and not previous.legacy else accounts, key=key+'accounts')
        if not owned:
            return
        st.caption('Recorded net changes are available before activation. Absolute history uses the reviewed opening and forward transactions in History.')
        assets = sorted({asset for r in rows for account, asset in r.pairs if account in owned})
        holdings = snapshot.holdings
        ids = list(holdings.position_id)
        names = {r.position_id:r['name'] for _, r in holdings.iterrows()}
        anchors = {}
        scope_label = st.text_input('History scope name', value=previous.scope_label if previous and not previous.legacy else 'All reviewed owned locations', key=key+'label')
        for asset in assets:
            matches = holdings.loc[holdings.id.str.upper().eq(asset.upper()), 'position_id'].tolist()
            saved = next((p for p, a in previous.anchors.items() if a['asset'] == asset and p in ids), None) if previous else None
            default = saved or (matches[0] if len(matches) == 1 else None)
            position = st.selectbox('Position for '+asset, [None, *ids], index=[None, *ids].index(default),
                format_func=lambda p: names.get(p, 'Exclude this asset'), key=key+'position_'+asset)
            if position:
                if position in anchors:
                    st.caption('Map different assets to different positions.')
                    return
                row = holdings.loc[holdings.position_id.eq(position)].iloc[0]
                anchors[position] = dict(asset=asset, ticker=row.ticker or '')
        accepted = st.checkbox('I reviewed the asset mapping and owned locations', key=key+'accept')
        if st.button('Enable asset history', disabled=not accepted or not anchors, key=key+'enable'):
            from portfolio_app.portfolio_settings import load_settings as portfolio_settings
            save_settings(directory, dict(version=2, method='forward-daily-dietz-v1', source_id=source,
                owned_accounts=owned, anchors=anchors,
                currency=portfolio_settings(directory).reporting_currency, scope_label=scope_label),
                expected_revision=revision, expected_journal_revision=journal_revision,
                expected_holdings_revision=snapshot.revision)
            st.caption('Asset history enabled. Open a position’s History view.')
