"""Strategic allocation setup and balance replacement controls."""
from datetime import date
from uuid import uuid4
from portfolio_app.strategic import category_labels
from pathlib import Path

import pandas as pd
import streamlit as st
from portfolio_app.view_state import persistent_editor

from portfolio_app.allocation import (Allocation, Bucket, migrate, migration_preview, save_allocation,
                                     validate_allocation)
from portfolio_app.balances import patch_holdings, replacement_changes
from portfolio_app.holdings import DataError
from portfolio_app.storage import revision


def render_balances(path, snapshot):
    st.subheader('Update balances')
    st.caption('Replace current quantities and optional buy-ins. Holdings confirmation is separate from quote freshness.')
    if snapshot.holdings.empty:
        return
    rows = snapshot.holdings.copy()
    for column, default in [('holdings_confirmed_on', ''), ('acquisition_currency', '')]:
        if column not in rows:
            rows[column] = default
    total_buy_in = st.radio('Buy-in entry', ['Average per unit', 'Total buy-in'], help='Enter average cost per unit or total cost of the remaining holding.', horizontal=True,
                           key=f'balance_buy_in_mode_{path}') == 'Total buy-in'
    cost_field = 'total_buy_in' if total_buy_in else 'acquisition_price'
    if total_buy_in:
        rows['total_buy_in'] = rows.shares * rows.acquisition_price
    key = f'balance_editor_{path}_{snapshot.revision}_{cost_field}'
    fields = ['shares', cost_field, 'acquisition_currency', 'holdings_confirmed_on']
    with st.form(key):
        edited = persistent_editor(rows[['position_id', 'name', 'account', *fields]], key=key + '_rows', hide_index=True,
                                disabled=['position_id', 'name', 'account'], width='stretch',
                                column_config={'position_id': None, 'shares': st.column_config.NumberColumn('Quantity', help='Number of held units, including fractional quantities.', min_value=0., format='%.10f'),
                                               cost_field: st.column_config.NumberColumn('Total buy-in (optional)' if total_buy_in else 'Average buy-in (optional)', min_value=0., format='%.8f',
                                                                                        help='Cost of the quantity currently held, in the buy-in currency.' if total_buy_in else None),
                                               'acquisition_currency': 'Buy-in currency',
                                               'holdings_confirmed_on': 'Holdings confirmed (YYYY-MM-DD)'})
        confirm_today = st.checkbox('Confirm all displayed balances as of today', help='Mark today as the date these quantities were checked against your records.')
        submit = st.form_submit_button('Save replacement balances')
    if submit:
        try:
            changes = replacement_changes(edited, rows, total_buy_in=total_buy_in,
                                          confirmed_on=date.today().isoformat() if confirm_today else None)
            patch_holdings(path, changes, expected_revision=snapshot.revision, replacement=True)
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            st.success('Replacement balances saved.')
            st.rerun()



def _discard_draft(key):
    st.session_state.get('targets_drafts', {}).pop(key, None)
    for store in ('view_editor_drafts', 'view_editor_bases'):
        for widget in list(st.session_state.get(store, {})):
            if widget.startswith(key):
                st.session_state[store].pop(widget, None)
    for widget in list(st.session_state):
        if widget.startswith(key):
            del st.session_state[widget]


def _bucket_editor(config, key, holdings=None):
    labels = category_labels(config)
    frame = pd.DataFrame([{'ID': b.id, 'Name': b.name, 'Parent': labels.get(b.parent, 'Portfolio'),
                           'Target (% of parent)': None if b.target is None else 100 * b.target,
                           'Protect from selling': b.sell_protected} for b in config.buckets],
                         columns=['ID', 'Name', 'Parent', 'Target (% of parent)', 'Protect from selling'])
    frame['Target (% of parent)'] = pd.to_numeric(frame['Target (% of parent)'])
    drafts = st.session_state.setdefault('targets_drafts', {})
    draft = drafts.setdefault(key, {'rows': frame, 'generation': 0})
    add, delete, _ = st.columns([1, 1, 3])
    with add.popover('Add category', help='Add a category to the draft hierarchy; save to make it persistent.', width='stretch'):
        with st.form(key + '_add'):
            name = st.text_input('Category name', help='Display name for this category in charts and target editors.')
            parent = st.selectbox('Parent category', ['', *labels], format_func=lambda v: labels.get(v, 'Portfolio'),
                                  help='Save a new category before selecting it as a parent.')
            target = st.number_input('Target (% of parent)', help='Desired share of the parent category; top-level categories use the whole portfolio.', min_value=0., max_value=100., value=None)
            protected = st.toggle('Protect from selling', help='Prevent sell suggestions when rebalancing this category or its descendants.')
            if st.form_submit_button('Add to draft'):
                if not name.strip():
                    st.error('Enter a category name.')
                else:
                    row = {'ID': uuid4().hex, 'Name': name.strip(), 'Parent': labels.get(parent, 'Portfolio'),
                           'Target (% of parent)': target, 'Protect from selling': protected}
                    draft['rows'] = pd.concat([draft['rows'], pd.DataFrame([row])], ignore_index=True)
                    draft['generation'] += 1
                    st.rerun()
    with delete.popover('Delete categories', help='Choose categories to remove from the draft hierarchy.', width='stretch'):
        names = dict(zip(draft['rows'].ID, draft['rows'].Name))
        selected = st.multiselect('Categories to delete', list(names), help='Select the categories to remove; review the remaining hierarchy before saving.', format_func=lambda v: labels.get(v, names[v]),
                                  key=key + '_delete_selection')
        if st.button('Remove from draft', help='Remove the selected categories from this unsaved draft.', disabled=not selected, key=key + '_delete'):
            try:
                candidate = _config_from_rows(draft['rows'], config)
                candidate = Allocation(tuple(b for b in candidate.buckets if b.id not in selected))
                validate_allocation(candidate, holdings)
            except (DataError, ValueError) as exc:
                st.error('Move positions and child categories before deleting. ' + str(exc))
            else:
                draft['rows'] = draft['rows'].loc[~draft['rows'].ID.isin(selected)].reset_index(drop=True)
                draft['generation'] += 1
                st.session_state.pop(key + '_delete_selection', None)
                st.rerun()
    edited = persistent_editor(draft['rows'], num_rows='fixed', hide_index=True, width='stretch', height='content',
        key=f"{key}_rows_{draft['generation']}", disabled=['ID'],
        column_config={'ID': None, 'Name': st.column_config.TextColumn(required=True),
            'Parent': st.column_config.SelectboxColumn(options=['Portfolio', *labels.values()], required=True),
            'Target (% of parent)': st.column_config.NumberColumn(min_value=0., max_value=100.,
                help='Blank means unspecified; zero is an explicit target. Sibling targets should total 100%.'),
            'Protect from selling': st.column_config.CheckboxColumn()})
    draft['rows'] = edited
    _category_summary(edited)
    return edited


def _config_from_rows(rows, config):
    labels = {value: key for key, value in category_labels(config).items()}
    def text(value):
        return '' if pd.isna(value) else str(value).strip()
    buckets = []
    for _, row in rows.iterrows():
        name = text(row['Name'])
        parent = text(row['Parent'])
        if not name:
            raise DataError('Each category needs a name. Use Delete categories to remove a category.')
        if parent not in {'Portfolio', *labels}:
            raise DataError('Choose an existing parent category.')
        buckets.append(Bucket(text(row['ID']) or uuid4().hex, name, labels.get(parent, ''),
            None if pd.isna(row['Target (% of parent)']) else float(row['Target (% of parent)']) / 100,
            False if pd.isna(row['Protect from selling']) else bool(row['Protect from selling'])))
    return Allocation(tuple(buckets))


def category_position_editor(positions, config, *, key, extra=(), filtered=False):
    labels = category_labels(config)
    choices = {label: identity for identity, label in labels.items() if identity in config.leaves()}
    display = positions.copy()
    display['Category'] = display.bucket_id.map(labels).fillna('Unassigned')
    columns = ['position_id', 'name', *extra, 'Category', 'within_bucket_target']
    drafts = st.session_state.setdefault('targets_drafts', {})
    draft = drafts.setdefault(key, {'rows': display[columns], 'generation': 0, 'filter': '*'})
    selected = '*'
    if filtered:
        options = ['*', *sorted(config.leaves(), key=labels.get)]
        if draft['rows'].Category.eq('Unassigned').any():
            options.append('')
        if st.session_state.get(key + '_filter', '*') not in options:
            st.session_state[key + '_filter'] = '*'
        selected = st.selectbox('Position category', options, help='Choose the category that owns each selected position.',
            format_func=lambda v: 'All categories' if v == '*' else labels.get(v, 'Unassigned'), key=key + '_filter')
    if selected != draft['filter']:
        draft['generation'] += 1
        draft['filter'] = selected
    rows = draft['rows']
    visible = rows if selected == '*' else rows.loc[rows.Category.eq(labels.get(selected, 'Unassigned'))]
    edited = persistent_editor(visible.reset_index(drop=True), hide_index=True, width='stretch', height='content', num_rows='fixed',
        disabled=['position_id', 'name', *extra], key=f"{key}_rows_{draft['generation']}",
        column_config={'position_id': None, 'name': 'Investment', 'account': 'Account',
            'Category': st.column_config.SelectboxColumn(options=['Unassigned', *choices], required=True),
            'within_bucket_target': st.column_config.NumberColumn('Target (% of category)', min_value=0., max_value=100.,
                help='Blank means unspecified; zero is an explicit target.')})
    # Assignment by identity also preserves deliberately cleared target cells.
    merged = rows.set_index('position_id')
    merged.loc[edited.position_id, ['Category', 'within_bucket_target']] = edited.set_index('position_id')[['Category', 'within_bucket_target']]
    draft['rows'] = merged.reset_index()
    result = draft['rows'].copy()
    result['bucket_id'] = result.Category.map(choices).fillna('')
    return result


def render_allocation_editor(path: Path, snapshot, config):
    st.subheader('Categories and targets')
    st.caption('Category targets are relative to their parent; position targets are relative to their category.')
    config_path = path.parent / 'allocation.yaml'
    stamp = revision(config_path)
    if config is None:
        proposed, preview = migration_preview(snapshot.holdings)
        st.info('Enable strategic allocation with a reviewed migration. Existing whole-portfolio targets stay preserved in the CSV. No macro percentages are prefilled.')
        rows = _bucket_editor(proposed, f'targets_migration_categories_{snapshot.revision}', preview)
        st.caption('Review the proposed categories and position assignments before enabling strategic allocation.')
        preview = preview.copy()
        preview['Legacy target (% of portfolio)'] = preview.get('target_allocation', float('nan')) * 100
        preview['within_bucket_target'] *= 100
        edited = category_position_editor(preview, proposed, key=f'targets_migration_positions_{snapshot.revision}', extra=['portfolio', 'Legacy target (% of portfolio)'])
        st.caption('Existing target numbers are copied without normalization. Review each bucket total before enabling; incomplete totals remain visible and block only calculations that require them.')
        _target_summary(edited, proposed)
        if st.button('Enable reviewed allocation', help='Save and enable the allocation hierarchy after reviewing its targets.', type='primary'):
            try:
                candidate = _config_from_rows(rows, proposed)
                preview['bucket_id'] = edited.bucket_id.fillna('')
                preview['within_bucket_target'] = edited.within_bucket_target / 100
                migrate(path, candidate, preview, expected_revision=snapshot.revision)
            except (DataError, OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.rerun()
        return
    with st.container(key='tour_category_targets'):
        st.subheader('Categories')
        category_key = f'targets_categories_{stamp}_{snapshot.revision}'
        rows = _bucket_editor(config, category_key, snapshot.holdings)
        save, discard, _ = st.columns([1, 1, 3])
        discard.button('Discard changes', help='Restore the saved values and discard this editor’s changes.', key=category_key + '_discard', on_click=_discard_draft, args=(category_key,))
        if save.button('Save categories', help='Validate and save the category hierarchy and its targets.', type='primary'):
            try:
                if revision(path) != snapshot.revision:
                    raise DataError('Positions changed. Reload before saving categories.')
                save_allocation(config_path, _config_from_rows(rows, config), snapshot.holdings, stamp)
            except (DataError, OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                _discard_draft(category_key)
                st.rerun()
    with st.container(key='tour_position_targets'):
        st.subheader('Position targets')
        render_bulk_bucket_assignment(path, snapshot, config, stamp)
        positions = snapshot.holdings.copy()
        positions['bucket_id'] = positions.get('bucket_id', '')
        positions['within_bucket_target'] = positions.get('within_bucket_target', float('nan')) * 100
        position_key = f'targets_positions_{snapshot.revision}_{stamp}'
        edited = category_position_editor(positions, config, key=position_key, extra=['account'], filtered=True)
        _target_summary(edited, config)
        save, discard, _ = st.columns([1, 1, 3])
        discard.button('Discard changes', help='Restore the saved values and discard this editor’s changes.', key=position_key + '_discard', on_click=_discard_draft, args=(position_key,))
        if save.button('Save position targets', help='Save the displayed position targets within their assigned categories.', type='primary'):
            try:
                if revision(config_path) != stamp:
                    raise DataError('Category settings changed. Reload before saving.')
                candidate = snapshot.holdings.copy()
                changes = edited.set_index('position_id')
                candidate['bucket_id'] = candidate.position_id.map(changes.bucket_id).fillna('')
                validate_allocation(config, candidate)
                patch_holdings(path, {r.position_id: {'bucket_id': r.bucket_id or '', 'within_bucket_target': r.within_bucket_target / 100}
                                      for r in edited.itertuples()}, expected_revision=snapshot.revision)
            except (DataError, OSError) as exc:
                st.error(str(exc))
            else:
                _discard_draft(position_key)
                st.rerun()


def render_bulk_bucket_assignment(path, snapshot, config, stamp):
    if snapshot.holdings.empty or not config.leaves():
        return
    with st.popover('Assign positions', help='Choose positions and a destination category; Assign saves the changes to holdings.'):
        labels = {r.position_id: f'{r["name"]} · {r.account}' if r.account else r['name']
                  for _, r in snapshot.holdings.iterrows()}
        key = f'bulk_bucket_positions_{path}_{snapshot.revision}'
        all_button, clear_button, _ = st.columns([1, 1, 3])
        all_button.button('Select all', help='Select every displayed position for this operation.', on_click=lambda: st.session_state.update({key: list(labels)}))
        clear_button.button('Clear selection', help='Deselect all positions without changing saved data.', on_click=lambda: st.session_state.update({key: []}))
        selected = st.multiselect('Positions to assign', list(labels), help='Choose the holdings whose category assignment should change.', format_func=labels.get, key=key)
        names = category_labels(config)
        destination = st.selectbox('Destination category', sorted(config.leaves()),
                                   format_func=lambda value: names[value],
                                   help='Only the category changes. Quantities, costs, labels and within-category target percentages stay unchanged.')
        if st.button(f'Assign {len(selected)} positions', help='Save the selected category assignment to these holdings immediately.', disabled=not selected, type='primary'):
            try:
                if revision(path.parent / 'allocation.yaml') != stamp:
                    raise DataError('Category settings changed. Reload before assigning positions.')
                candidate = snapshot.holdings.copy()
                candidate.loc[candidate.position_id.isin(selected), 'bucket_id'] = destination
                validate_allocation(config, candidate)
                patch_holdings(path, {key: {'bucket_id': destination} for key in selected},
                               expected_revision=snapshot.revision)
            except (DataError, OSError) as exc:
                st.error(str(exc))
            else:
                st.rerun()


def _category_summary(rows):
    issues = []
    complete = 0
    for parent, children in rows.groupby('Parent', sort=False, dropna=False):
        missing = int(children['Target (% of parent)'].isna().sum())
        total = children['Target (% of parent)'].sum()
        if not missing and abs(total - 100) < 1e-7:
            complete += 1
        else:
            issues.append(f'{parent}: {total:.2f}% assigned' + (f' · {missing} missing targets' if missing else ' · needs 100%'))
    if issues:
        st.caption(' / '.join(issues))
    elif complete:
        st.caption('Category targets total 100% within each parent.')


def _target_summary(positions, config):
    names = category_labels(config)
    issues = []
    for bucket in sorted(config.leaves(), key=names.get):
        rows = positions.loc[positions.bucket_id.eq(bucket)]
        missing = int(rows.within_bucket_target.isna().sum())
        total = float(rows.within_bucket_target.sum())
        if rows.empty:
            issues.append(f'{names[bucket]}: no positions · planned capacity')
        elif missing or abs(total - 100) >= 1e-7:
            issues.append(f'{names[bucket]}: {total:.2f}% assigned' + (f' · {missing} missing targets' if missing else ' · needs 100%'))
    unassigned = int(positions.bucket_id.eq('').sum())
    if unassigned:
        issues.append(f'{unassigned} unassigned position(s) · choose a category')
    if issues:
        st.caption(' / '.join(issues))
    elif config.leaves():
        st.caption('Position targets total 100% in each category.')
