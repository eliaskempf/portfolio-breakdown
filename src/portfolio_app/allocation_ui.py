"""Strategic allocation setup and balance replacement controls."""
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from portfolio_app.allocation import (Allocation, Bucket, migrate, migration_preview, save_allocation,
                                     validate_allocation)
from portfolio_app.balances import patch_holdings
from portfolio_app.holdings import DataError
from portfolio_app.storage import revision


def render_balances(path, snapshot):
    st.subheader('Update balances')
    st.caption('Replace current quantities and optional broker average buy-ins. Holdings confirmation is separate from quote freshness.')
    if snapshot.holdings.empty:
        return
    rows = snapshot.holdings.copy()
    for column, default in [('holdings_confirmed_on', ''), ('acquisition_currency', '')]:
        if column not in rows:
            rows[column] = default
    key = f'balance_editor_{path}_{snapshot.revision}'
    fields = ['shares', 'acquisition_price', 'acquisition_currency', 'holdings_confirmed_on']
    with st.form(key):
        edited = st.data_editor(rows[['position_id', 'name', 'account', *fields]], hide_index=True,
                                disabled=['position_id', 'name', 'account'], width='stretch',
                                column_config={'position_id': None, 'shares': st.column_config.NumberColumn('Quantity', min_value=0., format='%.10f'),
                                               'acquisition_price': st.column_config.NumberColumn('Average buy-in (optional)', min_value=0., format='%.8f'),
                                               'acquisition_currency': 'Buy-in currency',
                                               'holdings_confirmed_on': 'Holdings confirmed (YYYY-MM-DD)'})
        confirm_today = st.checkbox('Confirm all displayed balances as of today')
        submit = st.form_submit_button('Save replacement balances')
    if submit:
        changes = {}
        for _, row in edited.iterrows():
            values = {column: row[column] for column in fields}
            if confirm_today:
                values['holdings_confirmed_on'] = date.today().isoformat()
            changes[row.position_id] = values
        try:
            patch_holdings(path, changes, expected_revision=snapshot.revision, replacement=True)
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            st.success('Replacement balances saved.')
            st.rerun()


def _bucket_editor(config, key):
    frame = pd.DataFrame([{'ID': b.id, 'Name': b.name, 'Parent ID': b.parent,
                           'Target (% of parent)': None if b.target is None else 100 * b.target,
                           'Protect from selling': b.sell_protected} for b in config.buckets],
                         columns=['ID', 'Name', 'Parent ID', 'Target (% of parent)', 'Protect from selling'])
    frame['Target (% of parent)'] = pd.to_numeric(frame['Target (% of parent)'])
    return st.data_editor(frame, num_rows='dynamic', hide_index=True, width='stretch', key=key,
                          column_config={'Target (% of parent)': st.column_config.NumberColumn(min_value=0., max_value=100.),
                                         'Protect from selling': st.column_config.CheckboxColumn()})


def _config_from_rows(rows):
    def text(value):
        return '' if pd.isna(value) else str(value).strip()
    return Allocation(tuple(Bucket(text(r['ID']), text(r['Name']), text(r['Parent ID']),
                                   None if pd.isna(r['Target (% of parent)']) else float(r['Target (% of parent)']) / 100,
                                   False if pd.isna(r['Protect from selling']) else bool(r['Protect from selling']))
                            for _, r in rows.iterrows() if text(r['ID']) or text(r['Name'])))


def render_allocation_editor(path: Path, snapshot, config):
    st.subheader('Strategic buckets and targets')
    st.caption('One source position belongs to one leaf bucket. Bucket targets use their parent; position targets use their bucket. Blank targets are unknown, zero is explicit.')
    config_path = path.parent / 'allocation.yaml'
    stamp = revision(config_path)
    if config is None:
        proposed, preview = migration_preview(snapshot.holdings)
        st.info('Enable strategic allocation with a reviewed migration. Existing whole-portfolio targets stay preserved in the CSV. No macro percentages are prefilled.')
        rows = _bucket_editor(proposed, f'migration_buckets_{snapshot.revision}')
        st.caption('Use existing sleeve assignments below or enter new bucket IDs above. Parent IDs allow nested buckets; leave Parent ID blank for top-level buckets.')
        preview = preview.copy()
        preview['Legacy target (% of portfolio)'] = preview.get('target_allocation', float('nan')) * 100
        preview['within_bucket_target'] *= 100
        edited = st.data_editor(preview[['position_id', 'name', 'portfolio', 'Legacy target (% of portfolio)', 'bucket_id', 'within_bucket_target']],
                                hide_index=True, disabled=['position_id', 'name', 'portfolio', 'Legacy target (% of portfolio)'],
                                column_config={'position_id': None, 'within_bucket_target': st.column_config.NumberColumn('Target (% of bucket)', min_value=0., max_value=100.)},
                                key=f'migration_positions_{snapshot.revision}')
        st.caption('Existing target numbers are copied without normalization. Review each bucket total before enabling; incomplete totals remain visible and block only calculations that require them.')
        _target_summary(edited)
        if st.button('Enable reviewed allocation', type='primary'):
            try:
                candidate = _config_from_rows(rows)
                preview['bucket_id'] = edited.bucket_id.fillna('')
                preview['within_bucket_target'] = edited.within_bucket_target / 100
                migrate(path, candidate, preview, expected_revision=snapshot.revision)
            except (DataError, OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.rerun()
        return
    rows = _bucket_editor(config, f'bucket_editor_{stamp}')
    if st.button('Save bucket settings'):
        try:
            save_allocation(config_path, _config_from_rows(rows), snapshot.holdings, stamp)
        except (DataError, OSError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.rerun()
    positions = snapshot.holdings.copy()
    positions['bucket_id'] = positions.get('bucket_id', '')
    positions['within_bucket_target'] = positions.get('within_bucket_target', float('nan')) * 100
    edited = st.data_editor(positions[['position_id', 'name', 'account', 'bucket_id', 'within_bucket_target']], hide_index=True,
                            disabled=['position_id', 'name', 'account'], key=f'position_targets_{snapshot.revision}_{stamp}',
                            column_config={'position_id': None, 'bucket_id': st.column_config.SelectboxColumn('Bucket', options=['', *sorted(config.leaves())]),
                                           'within_bucket_target': st.column_config.NumberColumn('Target (% of bucket)', min_value=0., max_value=100.)})
    _target_summary(edited)
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


def _target_summary(positions):
    records = []
    for bucket, rows in positions.groupby('bucket_id', dropna=False, sort=False):
        missing = int(rows.within_bucket_target.isna().sum())
        total = float(rows.within_bucket_target.sum())
        records.append({'Bucket': bucket or 'Unassigned', 'Known target subtotal (%)': total,
                        'Missing targets': missing,
                        'Status': 'Complete' if missing == 0 and abs(total - 100) < 1e-7 else 'Needs targets totaling 100%'})
    if records:
        st.dataframe(pd.DataFrame(records), hide_index=True, width='stretch')
