"""Readable transaction previews and explicit decisions about possible duplicates."""
from decimal import Decimal
from types import SimpleNamespace

import streamlit as st

from portfolio_app.history_ui import _transaction_table
from portfolio_app.import_state import review_control
from portfolio_app.transaction_csv import digest


def render_transactions(valid, active, retained, token):
    assets = sorted({asset for row, _ in valid for _, asset in row.pairs})
    asset = review_control(st.selectbox, 'Show transactions for', ['', *assets],
        format_func=lambda value: value or 'All assets', key='import_tx_'+token+'view_asset',
        help='Filters this table only. Exchanges and asset fees also appear under the asset they affect.')
    visible = [(r, e) for r, e in valid if not asset or any(symbol == asset for _, symbol in r.pairs)]
    table = _transaction_table([e for _, e in visible], SimpleNamespace(active_ids=active))
    table.insert(0, 'Asset', [r.facts['asset_id'] for r, _ in visible])
    for index, (_, event) in enumerate(visible):
        if event.id not in retained:
            table.at[index, 'Status'] = 'Ready to import'
    st.dataframe(table, hide_index=True, width='stretch')
    if asset:
        st.caption(f'Showing {len(visible)} of {len(valid)} transactions. The asset filter does not change what will be imported.')


def review_matches(preview, selected, holdings, token):
    if not preview.matches:
        return set(), {}
    st.markdown('**Possible duplicate transactions**')
    st.caption('These entries have matching economic details. Reimporting an already-applied transaction does not apply it twice.')
    books = {b.position_id: b for b in preview.journal.securities}
    names = holdings.set_index('position_id')['name'].to_dict()
    excluded, decisions = set(), {}
    for position, first, second in preview.matches:
        book = books[position]
        by_id = {e.id: e for e in book.records}
        pair = (first, second)
        entry_labels = {first: 'first entry', second: 'second entry'}
        table = _transaction_table([by_id[i] for i in pair], SimpleNamespace(active_ids=()))
        table.insert(0, 'Entry', ['First entry', 'Second entry'])
        table['Status'] = ['In this import' if i in selected else 'Already recorded' for i in pair]
        st.dataframe(table, hide_index=True, width='stretch')
        choices = [None, 'keep', *(i for i in pair if i in selected)]
        decision = review_control(st.selectbox, 'Resolve match · '+names[position], choices,
            format_func=lambda value, entry_labels=entry_labels: 'Choose how to handle these entries' if value is None else
                'Keep both — separate transactions' if value == 'keep' else 'Exclude '+entry_labels[value],
            key='import_tx_'+token+'match_'+digest(pair)[:16])
        if decision not in (None, 'keep'):
            excluded.add(decision)
        decisions[position, first, second] = decision
    return excluded, decisions


def comparison_status(item):
    old, new = Decimal(item['before_quantity']), Decimal(item['after_quantity'])
    # Same quantity and cost tolerances as ordinary history reconciliation.
    tolerance = min(Decimal('1e-9'), abs(old)*Decimal('1e-9'))
    if abs(old-new) > tolerance:
        return 'Units differ'
    before, after = item['before_cost'], item['after_cost']
    if before is not None and after is None:
        return 'Units match · cost becomes unknown'
    if before is None or after is None:
        return 'Units match · cost unavailable'
    if abs(Decimal(str(before))-Decimal(str(after))) > Decimal('.01'):
        return 'Units match · cost differs'
    return 'Units and cost match'
