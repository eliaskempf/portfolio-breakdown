"""CSV extraction preview before the shared history reconciliation screen."""
from types import SimpleNamespace

import streamlit as st

from portfolio_app.history_ui import _transaction_table
from portfolio_app.import_state import review_control


def render_transactions(valid, active, retained, token):
    assets = sorted({asset for row, _ in valid for _, asset in row.pairs})
    heading, filter_col = st.columns([4, 2], vertical_alignment='center')
    asset = review_control(filter_col.selectbox, 'Show transactions for', ['', *assets],
        format_func=lambda value: value or 'All assets', key='import_tx_'+token+'view_asset',
        filter_mode=None, label_visibility='collapsed',
        help='Filters this table only. Exchanges and asset fees also appear under the asset they affect.')
    visible = [(r, e) for r, e in valid if not asset or any(symbol == asset for _, symbol in r.pairs)]
    table = _transaction_table([e for _, e in visible], SimpleNamespace(active_ids=active))
    table.insert(0, 'Asset', [r.facts['asset_id'] for r, _ in visible])
    for index, (_, event) in enumerate(visible):
        if event.id not in retained:
            table.at[index, 'Status'] = 'Ready to import'
    st.dataframe(table, hide_index=True, width='stretch')
    heading.markdown(f'**Transactions** · {len(visible)} / {len(valid)}' if asset else f'**Transactions** · {len(valid)}')
