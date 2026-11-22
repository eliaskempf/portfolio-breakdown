"""One parent-relative target editor for categories and direct investments."""
from dataclasses import replace
from io import StringIO

import pandas as pd
import streamlit as st

from portfolio_app.portfolio.allocation import Allocation, validate_allocation
from portfolio_app.portfolio.category_commands import create_category, publish_category_edit
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.strategic import category_labels
from portfolio_app.workspace.storage import revision
from portfolio_app.interface.shared.view_state import persistent_editor


def render_create_category(path, snapshot, config, stamp):
    with st.popover('Create category from selection', help='Combine positions sharing a parent into one flexible allocation category.'):
        names = category_labels(config)
        choices = [b.id for b in config.buckets if not config.flexible(b.id)]
        if not choices:
            st.caption('Set a parent category to Member targets first.')
            return
        parent = st.selectbox('Containing category', choices, format_func=names.get, key='targets_create_category_parent')
        rows = snapshot.holdings.loc[snapshot.holdings.bucket_id.eq(parent)]
        labels = {r.position_id: r['name']+(' · '+r.account if r.account else '') for _, r in rows.iterrows()}
        members = st.multiselect('Investments to combine', list(labels), format_func=labels.get, key=f'targets_create_category_members_{parent}_{snapshot.revision}')
        name = st.text_input('New category name', key='targets_create_category_name')
        selected = rows.loc[rows.position_id.isin(members)]
        known = len(selected) and selected.within_bucket_target.notna().all()
        default = float(selected.within_bucket_target.sum()*100) if known and selected.within_bucket_target.sum() <= 1 else None
        target = st.number_input('Combined target (% of parent)', min_value=0., max_value=100., value=default,
                                 key=f'targets_create_category_target_{parent}_{tuple(members)}')
        if st.button('Create category', disabled=not members or not name.strip(), type='primary'):
            try:
                create_category(path.parent, config, members, name, target/100 if target is not None else None,
                                holdings_revision=snapshot.revision, allocation_revision=stamp)
            except (DataError, OSError) as exc:
                st.error(str(exc))
            else:
                st.rerun()


def render_member_editor(path, snapshot, config, stamp):
    names = {'': 'Portfolio', **category_labels(config)}
    if st.session_state.get('targets_allocation_scope', '') not in names:
        st.session_state['targets_allocation_scope'] = ''
    scope = st.selectbox('Allocation category', list(names), format_func=names.get, key='targets_allocation_scope')
    node = next((b for b in config.buckets if b.id == scope), None)
    children = config.children(scope)
    direct = snapshot.holdings.loc[snapshot.holdings.bucket_id.eq(scope)] if scope else snapshot.holdings.iloc[:0]
    mode = node.allocation_mode if node else 'targets'
    if node and not children:
        mode = st.segmented_control('Internal allocation', ['targets', 'flexible'],
            format_func=lambda v: 'Member targets' if v == 'targets' else 'Flexible', default=mode, required=True,
            key=f'targets_category_mode_{scope}_{stamp}', help='Flexible allocation constrains only the combined category value.')
    elif node:
        st.caption('Member targets · includes subcategories')
    flexible = mode == 'flexible'
    records = [dict(identity=b.id, kind='Category', Investment=b.name, Account='', Target=None if b.target is None else 100*b.target,
                    Buy=None, Sell=None) for b in children]
    for row in direct.itertuples():
        records.append(dict(identity=row.position_id, kind='Investment', Investment=row.name, Account=row.account,
            Target=None if pd.isna(row.within_bucket_target) else 100*row.within_bucket_target,
            Buy=node.buy_priority.index(row.position_id)+1 if node and row.position_id in node.buy_priority else None,
            Sell=node.sell_priority.index(row.position_id)+1 if node and row.position_id in node.sell_priority else None))
    frame = pd.DataFrame(records, columns=['identity','kind','Investment','Account','Target','Buy','Sell'])
    for col in ('Target', 'Buy', 'Sell'):
        frame[col] = pd.to_numeric(frame[col])
    shown = frame.drop(columns=['Target'] if flexible else ['Buy','Sell'])
    edited = persistent_editor(shown, hide_index=True, width='stretch', height='content',
        disabled=['identity','kind','Investment','Account'], key=f'targets_member_editor_{scope}_{stamp}_{snapshot.revision}_{mode}',
        column_config={'identity': None, 'kind': 'Type',
            'Target': st.column_config.NumberColumn('Target (% of parent)', min_value=0., max_value=100.,
                help=f'Percentage of {names[scope]}. Categories and investments together total 100%.'),
            'Buy': st.column_config.NumberColumn('Buy priority', min_value=1, max_value=max(1,len(direct)), step=1,
                help='1 is first. Set a unique rank for every investment before using this direction.'),
            'Sell': st.column_config.NumberColumn('Sell priority', min_value=1, max_value=max(1,len(direct)), step=1)})
    if not flexible:
        missing = int(edited.Target.isna().sum())
        total = float(edited.Target.sum())
        st.caption(f'{total:g}% assigned' + (f' · {missing} missing' if missing else ' · needs 100%' if abs(total-100)>1e-7 else ''))
    if st.button('Save allocation', type='primary', key=f'targets_save_members_{scope}'):
        try:
            if revision(path) != snapshot.revision or revision(path.parent/'allocation.yaml') != stamp:
                raise DataError('Portfolio changed. Review the allocation again.')
            raw = pd.read_csv(StringIO(path.read_text(encoding='utf-8-sig')), dtype=str, keep_default_na=False)
            if 'position_key' not in raw:
                from uuid import uuid4
                raw['position_key'] = [uuid4().hex for _ in range(len(raw))]
            identities = dict(zip(snapshot.holdings.position_id, raw.position_key))
            buckets = {b.id:b for b in config.buckets}
            if flexible:
                priorities = []
                for field in ('Buy','Sell'):
                    ranks = edited[field]
                    if ranks.notna().any() and (ranks.isna().any() or set(ranks) != set(range(1,len(ranks)+1))):
                        raise DataError(f'{field} priorities must be blank or contain each rank from 1 to {len(ranks)} once.')
                    priorities.append(tuple(identities[key] for key in edited.sort_values(field).identity) if ranks.notna().all() else ())
                buckets[scope] = replace(node, allocation_mode=mode, buy_priority=priorities[0], sell_priority=priorities[1])
            else:
                for row in edited.itertuples():
                    target = None if pd.isna(row.Target) else row.Target/100
                    if row.kind == 'Category':
                        buckets[row.identity] = replace(buckets[row.identity], target=target)
                    else:
                        indexes = snapshot.holdings.index[snapshot.holdings.position_id.eq(row.identity)]
                        raw.loc[indexes, 'within_bucket_target'] = '' if target is None else str(target)
                if node:
                    buckets[scope] = replace(buckets[scope], allocation_mode=mode, buy_priority=(), sell_priority=())
                if node and node.allocation_mode == 'flexible' and (missing or abs(total-100)>1e-7):
                    raise DataError('Review member targets totaling 100% before switching from Flexible.')
            candidate = Allocation(tuple(buckets.values()), 3)
            publish_category_edit(path.parent, candidate, raw.to_csv(index=False), holdings_revision=snapshot.revision,
                                  allocation_revision=stamp)
        except (DataError, OSError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state['portfolio_save_notice'] = 'Allocation saved.'
            st.rerun()
