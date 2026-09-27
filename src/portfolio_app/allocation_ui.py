"""Strategic allocation setup and balance replacement controls."""
from datetime import date
from uuid import uuid4
from portfolio_app.strategic import category_labels
from pathlib import Path

import pandas as pd
import streamlit as st

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
    total_buy_in = st.radio('Buy-in entry', ['Average per unit', 'Total buy-in'], horizontal=True,
                           key=f'balance_buy_in_mode_{path}') == 'Total buy-in'
    cost_field = 'total_buy_in' if total_buy_in else 'acquisition_price'
    if total_buy_in:
        rows['total_buy_in'] = rows.shares * rows.acquisition_price
    key = f'balance_editor_{path}_{snapshot.revision}_{cost_field}'
    fields = ['shares', cost_field, 'acquisition_currency', 'holdings_confirmed_on']
    with st.form(key):
        edited = st.data_editor(rows[['position_id', 'name', 'account', *fields]], hide_index=True,
                                disabled=['position_id', 'name', 'account'], width='stretch',
                                column_config={'position_id': None, 'shares': st.column_config.NumberColumn('Quantity', min_value=0., format='%.10f'),
                                               cost_field: st.column_config.NumberColumn('Total buy-in (optional)' if total_buy_in else 'Average buy-in (optional)', min_value=0., format='%.8f',
                                                                                        help='Cost of the quantity currently held, in the buy-in currency.' if total_buy_in else None),
                                               'acquisition_currency': 'Buy-in currency',
                                               'holdings_confirmed_on': 'Holdings confirmed (YYYY-MM-DD)'})
        confirm_today = st.checkbox('Confirm all displayed balances as of today')
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



def _bucket_editor(config, key):
    labels = category_labels(config)
    st.caption('Edit Target (% of parent) to change a category’s allocation. Use the + row at the bottom to add a category, then save. Save new categories before choosing them as parents.')
    frame = pd.DataFrame([{'ID': b.id, 'Name': b.name, 'Parent': labels.get(b.parent, 'Portfolio'),
                           'Target (% of parent)': None if b.target is None else 100 * b.target,
                           'Protect from selling': b.sell_protected} for b in config.buckets],
                         columns=['ID', 'Name', 'Parent', 'Target (% of parent)', 'Protect from selling'])
    frame['Target (% of parent)'] = pd.to_numeric(frame['Target (% of parent)'])
    return st.data_editor(frame, num_rows='dynamic', hide_index=True, width='stretch', key=key,
        column_config={'ID': None, 'Parent': st.column_config.SelectboxColumn(options=['Portfolio', *labels.values()]),
                       'Target (% of parent)': st.column_config.NumberColumn(min_value=0., max_value=100.),
                       'Protect from selling': st.column_config.CheckboxColumn()})


def _config_from_rows(rows, config):
    labels = {value: key for key, value in category_labels(config).items()}
    def text(value):
        return '' if pd.isna(value) else str(value).strip()
    return Allocation(tuple(Bucket(text(r['ID']) or uuid4().hex, text(r['Name']), labels.get(text(r['Parent']), ''),
        None if pd.isna(r['Target (% of parent)']) else float(r['Target (% of parent)']) / 100,
        False if pd.isna(r['Protect from selling']) else bool(r['Protect from selling']))
        for _, r in rows.iterrows() if text(r['Name'])))


def category_position_editor(positions, config, *, key, extra=()):
    labels = category_labels(config)
    choices = {label: identity for identity, label in labels.items() if identity in config.leaves()}
    display = positions.copy()
    display['Category'] = display.bucket_id.map(labels).fillna('Unassigned')
    columns = ['position_id', 'name', *extra, 'Category', 'within_bucket_target']
    edited = st.data_editor(display[columns], hide_index=True, width='stretch',
        disabled=['position_id', 'name', *extra], key=key,
        column_config={'position_id': None, 'name': 'Investment',
            'Category': st.column_config.SelectboxColumn(options=['Unassigned', *choices]),
            'within_bucket_target': st.column_config.NumberColumn('Target (% of category)', min_value=0., max_value=100.)})
    edited['bucket_id'] = edited.Category.map(choices).fillna('')
    return edited


def render_allocation_editor(path: Path, snapshot, config):
    st.subheader('Categories and targets')
    st.caption('Set meta allocation targets and create categories here. Each position belongs to one category with no subcategories. Category targets use their parent; position targets use their category. A blank target is unspecified; zero is an explicit target.')
    config_path = path.parent / 'allocation.yaml'
    stamp = revision(config_path)
    if config is None:
        proposed, preview = migration_preview(snapshot.holdings)
        st.info('Enable strategic allocation with a reviewed migration. Existing whole-portfolio targets stay preserved in the CSV. No macro percentages are prefilled.')
        rows = _bucket_editor(proposed, f'migration_buckets_{snapshot.revision}')
        st.caption('Review the proposed categories and position assignments before enabling strategic allocation.')
        preview = preview.copy()
        preview['Legacy target (% of portfolio)'] = preview.get('target_allocation', float('nan')) * 100
        preview['within_bucket_target'] *= 100
        edited = category_position_editor(preview, proposed, key=f'migration_positions_{snapshot.revision}', extra=['portfolio', 'Legacy target (% of portfolio)'])
        st.caption('Existing target numbers are copied without normalization. Review each bucket total before enabling; incomplete totals remain visible and block only calculations that require them.')
        _target_summary(edited, proposed)
        if st.button('Enable reviewed allocation', type='primary'):
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
    rows = _bucket_editor(config, f'bucket_editor_{stamp}')
    if st.button('Save category settings'):
        try:
            save_allocation(config_path, _config_from_rows(rows, config), snapshot.holdings, stamp)
        except (DataError, OSError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.rerun()
    render_bulk_bucket_assignment(path, snapshot, config, stamp)
    positions = snapshot.holdings.copy()
    positions['bucket_id'] = positions.get('bucket_id', '')
    positions['within_bucket_target'] = positions.get('within_bucket_target', float('nan')) * 100
    edited = category_position_editor(positions, config, key=f'position_targets_{snapshot.revision}_{stamp}', extra=['account'])
    _target_summary(edited, config)
    if st.button('Save position targets and buckets'):
        try:
            if revision(config_path) != stamp:
                raise DataError('Bucket settings changed. Reload before saving.')
            candidate = snapshot.holdings.copy()
            edited['bucket_id'] = edited.bucket_id.fillna('')
            candidate['bucket_id'] = edited.bucket_id.fillna('')
            validate_allocation(config, candidate)
            patch_holdings(path, {r.position_id: {'bucket_id': r.bucket_id or '', 'within_bucket_target': r.within_bucket_target / 100}
                                  for r in edited.itertuples()}, expected_revision=snapshot.revision)
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            st.rerun()


def render_bulk_bucket_assignment(path, snapshot, config, stamp):
    if snapshot.holdings.empty or not config.leaves():
        return
    with st.expander('Assign positions in bulk'):
        labels = {r.position_id: f'{r["name"]} · {r.account}' if r.account else r['name']
                  for _, r in snapshot.holdings.iterrows()}
        key = f'bulk_bucket_positions_{path}_{snapshot.revision}'
        all_button, clear_button, _ = st.columns([1, 1, 3])
        all_button.button('Select all', on_click=lambda: st.session_state.update({key: list(labels)}))
        clear_button.button('Clear selection', on_click=lambda: st.session_state.update({key: []}))
        selected = st.multiselect('Positions to assign', list(labels), format_func=labels.get, key=key)
        names = category_labels(config)
        destination = st.selectbox('Destination bucket', sorted(config.leaves()),
                                   format_func=lambda value: names[value],
                                   help='Only the bucket changes. Quantities, costs, labels and within-bucket target percentages stay unchanged.')
        if st.button(f'Assign {len(selected)} positions', disabled=not selected, type='primary'):
            try:
                if revision(path.parent / 'allocation.yaml') != stamp:
                    raise DataError('Bucket settings changed. Reload before assigning positions.')
                candidate = snapshot.holdings.copy()
                candidate.loc[candidate.position_id.isin(selected), 'bucket_id'] = destination
                validate_allocation(config, candidate)
                patch_holdings(path, {key: {'bucket_id': destination} for key in selected},
                               expected_revision=snapshot.revision)
            except (DataError, OSError) as exc:
                st.error(str(exc))
            else:
                st.rerun()


def _target_summary(positions, config):
    names = category_labels(config)
    records = []
    for bucket, rows in positions.groupby('bucket_id', dropna=False, sort=False):
        missing = int(rows.within_bucket_target.isna().sum())
        total = float(rows.within_bucket_target.sum())
        records.append({'Category': names.get(bucket, 'Unassigned'), 'Known target subtotal (%)': total,
                        'Missing targets': missing,
                        'Status': 'Complete' if missing == 0 and abs(total - 100) < 1e-7 else 'Needs targets totaling 100%'})
    if records:
        st.dataframe(pd.DataFrame(records), hide_index=True, width='stretch')
