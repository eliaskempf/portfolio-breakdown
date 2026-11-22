"""Strategic category grouping, allocation modes and trade priorities."""

import pandas as pd
import streamlit as st

from portfolio_app.portfolio.category_commands import create_category, save_category_settings
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.strategic import category_labels
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


def render_member_editor(path, snapshot, config, stamp, scope):
    node = next((b for b in config.buckets if b.id == scope), None)
    children = config.children(scope)
    direct = snapshot.holdings.loc[snapshot.holdings.bucket_id.eq(scope)] if scope else snapshot.holdings.iloc[:0]
    mode = node.allocation_mode if node else 'targets'
    if node and not children:
        mode = st.segmented_control('Internal allocation', ['targets', 'flexible'],
            format_func=lambda v: 'Member targets' if v == 'targets' else 'Flexible', default=mode, required=True,
            key=f'targets_category_mode_{scope}_{stamp}', help='Flexible allocation constrains only the combined category value.')

    if mode != 'flexible':
        if node and node.allocation_mode == 'flexible':
            from portfolio_app.interface.views.portfolio.target_dialog import request_target_edit
            st.button('Review member targets', on_click=request_target_edit, args=(path.parent,config,scope),
                      kwargs={'switch_to_members':True})
        return
    frame = direct[['position_id','name','account']].rename(columns={'position_id':'identity','name':'Investment','account':'Account'}).copy()
    for field, ordered in [('Buy',node.buy_priority), ('Sell',node.sell_priority)]:
        frame[field] = pd.to_numeric(frame.identity.map({pid:i for i,pid in enumerate(ordered,1)}))
    edited = persistent_editor(frame, hide_index=True, width='stretch', height='content',
        disabled=['identity','Investment','Account'], key=f'targets_member_editor_{scope}_{stamp}_{snapshot.revision}_{mode}',
        column_config={'identity': None,
            'Buy': st.column_config.NumberColumn('Buy priority', min_value=1, max_value=max(1,len(direct)), step=1,
                help='1 is first. Set a unique rank for every investment before using this direction.'),
            'Sell': st.column_config.NumberColumn('Sell priority', min_value=1, max_value=max(1,len(direct)), step=1)})
    if st.button('Save priorities', type='primary', key=f'targets_save_members_{scope}'):
        try:
            orders = []
            for field in ('Buy','Sell'):
                ranks = edited[field]
                if ranks.notna().any() and (ranks.isna().any() or set(ranks) != set(range(1,len(ranks)+1))):
                    raise DataError(f'{field} priorities must be blank or contain each rank from 1 to {len(ranks)} once.')
                orders.append(tuple(edited.sort_values(field).identity) if ranks.notna().all() else ())
            save_category_settings(path.parent, config, scope, mode, *orders, holdings_revision=snapshot.revision, allocation_revision=stamp)
        except (DataError, OSError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state['portfolio_save_notice'] = 'Allocation saved.'
            st.rerun()
