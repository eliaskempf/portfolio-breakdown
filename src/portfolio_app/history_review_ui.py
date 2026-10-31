"""One reconciliation screen for manual, PDF and CSV transaction histories."""
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import streamlit as st

from portfolio_app.import_state import review_control
from portfolio_app.holdings import DataError
from portfolio_app.transaction_csv import digest


@dataclass(frozen=True)
class ReviewPreview:
    journal: object
    comparisons: tuple
    cash: tuple = ()
    matches: tuple = ()
    editable: frozenset = frozenset()
    known_positions: frozenset = frozenset()
    checkpoints: tuple = ()
    warnings: tuple = ()
    starting_note: str = ''


def comparison_status(item):
    old, new = Decimal(item['before_quantity']), Decimal(item['after_quantity'])
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


def _groups(matches):
    groups = []
    for position, first, second in matches:
        members = {first, second}
        joined = [(p, ids) for p, ids in groups if p == position and ids & members]
        for group in joined:
            groups.remove(group)
            members.update(group[1])
        groups.append((position, members))
    return groups


def _similar_transactions(preview, key):
    from portfolio_app.history_ui import _transaction_table, _event_label
    books = {b.position_id: b for b in preview.journal.securities}
    comparisons = {c['position_id']: c for c in preview.comparisons}
    excluded = set()
    for position, members in _groups(preview.matches):
        records = [e for e in books[position].records if e.id in members]
        item = comparisons[position]
        group_key = key+'similar_'+digest(sorted(members))[:16]
        with st.expander(f'{len(records)} similar transactions · {item["name"]}'):
            table = _transaction_table(records, SimpleNamespace(active_ids=()))
            table.insert(0, 'Entry', range(1, len(records)+1))
            table['Status'] = ['Included in review' if e.id in preview.editable else 'Already recorded' for e in records]
            st.dataframe(table, hide_index=True, width='stretch')
            labels = {e.id: f'{n}. {_event_label(e)}' for n, e in enumerate(records, 1)}
            options = [e.id for e in records if e.id in preview.editable]
            if options:
                kept = review_control(st.multiselect, 'Keep transactions · '+item['name'], options,
                    default=options, format_func=labels.get, key=group_key,
                    help='Repeated fills can share dates and amounts. Applying retains the selected entries; excluded source evidence stays saved.')
                excluded.update(set(options)-set(kept))
            if position in preview.known_positions and comparison_status(item) != 'Units differ':
                st.caption('All entries: units reconcile', help='The selected entries reconstruct the recorded holding.')
            elif position not in preview.known_positions:
                st.caption('No reference balance')
    return excluded


def _balances(preview, key):
    from portfolio_app.history_ui import _money
    items = {c['position_id']: c for c in preview.comparisons}
    position = next(iter(items))
    if len(items) > 1:
        selection_key = key+'holding'
        controls = st.session_state.setdefault('import_controls', {})
        selected = st.session_state.get(selection_key, controls.get(selection_key))
        if selected is not None and selected not in items:
            st.session_state[selection_key] = position
            controls[selection_key] = position
        position = review_control(st.selectbox, 'Holding to review', list(items),
            format_func=lambda p: items[p]['name'], filter_mode=None, key=key+'holding')
    item = items[position]
    known = position in preview.known_positions
    if len(items) == 1:
        st.markdown('**'+item['name']+'**')
    def number(value):
        return 'Unknown' if value is None else format(Decimal(str(value)), '.10f').rstrip('0').rstrip('.') or '0'
    def money(value):
        return _money(None if value is None or pd.isna(value) else Decimal(str(value)))
    old, new = Decimal(item['before_quantity']), Decimal(item['after_quantity'])
    before = None if item['before_cost'] is None else Decimal(str(item['before_cost']))
    after = None if item['after_cost'] is None else Decimal(str(item['after_cost']))
    st.table(pd.DataFrame([
        {'Measure':'Shares', 'Current portfolio':number(old) if known else '—', 'From history':number(new)},
        {'Measure':'Acquisition cost ('+item['currency']+')', 'Current portfolio':_money(before) if known else '—', 'From history':_money(after)},
        {'Measure':'Average buy-in ('+item['currency']+')', 'Current portfolio':_money(before/old if before is not None and old else None) if known else '—',
         'From history':_money(after/new if after is not None and new else None)},
    ]), hide_index=True, border='horizontal')
    status = comparison_status(item)
    if not known:
        st.caption('New holding · no reference balance')
    elif status == 'Units and cost match':
        st.caption('Units and cost match')
    elif 'differ' in status or 'becomes unknown' in status:
        st.caption(status, help='Applying replaces the current holding with the reconstructed balances.')
    else:
        st.caption(status)
    if len(items) > 1:
        with st.expander('All affected holdings'):
            table = pd.DataFrame(preview.comparisons)
            table['Comparison'] = [comparison_status(c) if c['position_id'] in preview.known_positions else 'New holding' for c in preview.comparisons]
            table['before_quantity'] = [number(c['before_quantity']) if c['position_id'] in preview.known_positions else '—' for c in preview.comparisons]
            table['before_cost'] = [money(c['before_cost']) if c['position_id'] in preview.known_positions else '—' for c in preview.comparisons]
            table['after_quantity'] = table.after_quantity.map(number)
            table['after_cost'] = table.after_cost.map(money)
            st.dataframe(table.drop(columns=['position_id']).rename(columns={'name':'Position', 'account':'Account',
                'before_quantity':'Current units', 'after_quantity':'From history', 'before_cost':'Current cost',
                'after_cost':'From history cost', 'currency':'Currency'}), hide_index=True, width='stretch')
    if preview.cash:
        names = {a.id:a.name for a in preview.journal.accounts}
        st.markdown('**Cash balance changes**')
        st.table(pd.DataFrame([{'Account':names.get(c['account'], 'Linked account'), 'Currency':c['currency'],
            'Current balance':_money(c['before']), 'After applying':_money(c['after'])} for c in preview.cash]), hide_index=True, border='horizontal')

    return position


def render_history_review(build_preview, *, key, apply, keep, action_label=None, keep_label='Keep portfolio unchanged'):
    """Build from unchanged evidence, then recompute exclusions before final acceptance."""
    try:
        preview = build_preview(set())
        balances = st.container()
        excluded = _similar_transactions(preview, key)
        if excluded:
            preview = build_preview(excluded)
            st.caption(f'{len(excluded)} excluded')
    except (DataError, ValueError) as exc:
        st.warning(str(exc))
        st.button(keep_label, key=key+'keep', on_click=keep)
        return None
    if not preview.comparisons:
        st.caption('No new transactions selected')
        st.button(keep_label, key=key+'keep', on_click=keep)
        return preview
    with balances:
        position = _balances(preview, key)
    if preview.starting_note:
        st.caption(preview.starting_note)
    checkpoints = True
    if preview.checkpoints:
        checkpoints = review_control(st.checkbox, 'Accept the displayed replacement balances', key=key+'checkpoints')
        table = pd.DataFrame(preview.checkpoints)
        names = {c['position_id']:c['name'] for c in preview.comparisons}
        table.insert(0, 'Position', table.position_id.map(names))
        st.dataframe(table.drop(columns=['position_id']), hide_index=True, width='stretch')
    if preview.warnings:
        with st.expander('Transaction details'):
            for warning in preview.warnings:
                st.caption(warning)
    blocked = not checkpoints or any(c['after'] is None for c in preview.cash)
    multiple = len(preview.comparisons) > 1 and action_label is None
    if multiple:
        from portfolio_app.security_history import history_fingerprint
        signature = digest([history_fingerprint(preview.journal), str(preview.comparisons),
                            sorted(excluded)])
        state_key = key+'asset_reviews'
        state = st.session_state.get(state_key, {})
        if state.get('signature') != signature:
            state = {'signature':signature, 'choices':{}}
            st.session_state[state_key] = state
        choices = state['choices']
        identities = [c['position_id'] for c in preview.comparisons]
        st.caption(f'Reviewed {len(choices)} / {len(identities)}')
        if position in choices:
            st.caption('Reviewed')
        def decide():
            choices[position] = True
            following = next((p for p in identities if p not in choices), position)
            st.session_state[key+'holding'] = following
            st.session_state.setdefault('import_controls', {})[key+'holding'] = following
        with st.container(horizontal=True):
            st.button('Accept this holding', key=key+'confirm_asset', disabled=blocked,
                      on_click=decide, type='primary')
        with st.container(horizontal=True, key=key+'actions'):
            if st.button('Apply reviewed holdings', key=key+'save',
                         disabled=blocked or any(p not in choices for p in identities)):
                apply(excluded)
            st.button(keep_label, key=key+'keep', on_click=keep)
        return preview
    with st.container(horizontal=True, key=key+'actions'):
        if st.button(action_label or 'Apply history', key=key+'save', disabled=blocked, type='primary',
                     help='Apply the displayed balances and retain selected similar entries.'):
            apply(excluded)
        st.button(keep_label, key=key+'keep', on_click=keep)
    if blocked:
        st.caption('Confirm replacement balances' if not checkpoints else 'Cash amounts missing')
    return preview
