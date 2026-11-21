"""Exposure targets for source investments and subcategories in the same scope."""
import pandas as pd
import streamlit as st

from portfolio_app.exposure.exposure_targets import ExposureTargets, save_exposure_targets
from portfolio_app.exposure.mixed_exposure_targets import validate_source_membership
from portfolio_app.interface.shared.view_state import persistent_editor
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.strategic import category_labels


def render_mixed_targets(directory, config, result, saved, revision, universe):
    labels = {'': 'Portfolio', **category_labels(config)}
    capital = result.capital
    sources = capital.loc[capital.bucket_id.ne('')].drop_duplicates('source_position_id')
    internal = {b.parent for b in config.buckets if b.parent}
    optional = [b.id for b in config.buckets if b.id not in internal and not config.flexible(b.id)
                and sources.bucket_id.eq(b.id).any()]
    compatible = saved is not None and saved.weights == 'mixed' and saved.universe == universe
    selected = st.multiselect('Categories with individual exposure targets', optional,
        default=[key for key in optional if key in saved.members] if compatible else [], format_func=labels.get,
        key=f'exposure_member_categories_{revision}_{universe}',
        help='Optional for categories without subcategories. Direct investments beside subcategories always need targets. Flexible groups keep one combined target.')
    member_categories = internal | set(selected)
    records = []
    for bucket in config.buckets:
        records.append({'owner': bucket.parent, 'category': bucket.id, 'position': '',
            'Within': labels[bucket.parent], 'Type': 'Category', 'Name': bucket.name,
            'Target (%)': saved.composition.get(bucket.id, float('nan'))*100 if compatible else None})
    accounts = result.exposures.drop_duplicates('source_position_id').set_index('source_position_id').get('account', pd.Series(dtype=str))
    for row in sources.loc[sources.bucket_id.isin(member_categories)].itertuples():
        account = accounts.get(row.source_position_id, '')
        records.append({'owner': row.bucket_id, 'category': '', 'position': row.source_position_id,
            'Within': labels[row.bucket_id], 'Type': 'Investment', 'Name': row.name + (f' · {account}' if account else ''),
            'Target (%)': saved.members.get(row.bucket_id, {}).get(row.source_position_id, float('nan'))*100 if compatible else None})
    frame = pd.DataFrame(records).sort_values(['Within', 'Type', 'Name'], kind='stable').reset_index(drop=True)
    with st.form(f'exposure_mixed_form_{revision}_{universe}'):
        multiple = st.number_input('Desired total exposure (× net equity)', min_value=0., value=saved.multiple if saved else None, step=.05)
        edited = persistent_editor(frame, key=f'exposure_mixed_editor_{revision}_{universe}_{sorted(selected)}',
            hide_index=True, disabled=[column for column in frame if column != 'Target (%)'], width='stretch',
            column_config={'owner': None, 'category': None, 'position': None,
                'Target (%)': st.column_config.NumberColumn(min_value=0., max_value=100.,
                    help='Share of the parent’s desired exposure. Rows within each parent must total 100%.')})
        save = st.form_submit_button('Save exposure targets')
        clear = st.form_submit_button('Clear exposure targets')
    if clear:
        from portfolio_app.funds.leverage_settings import load_leverage_settings
        try:
            if load_leverage_settings(directory)[0].active_basis == 'exposure':
                raise DataError('Activate the capital policy before clearing the active exposure policy.')
            save_exposure_targets(directory, None, config, revision)
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            st.rerun()
    if save:
        try:
            if multiple is None or edited['Target (%)'].isna().any():
                raise DataError('Set the total exposure multiple and every target explicitly, including zeros.')
            composition, members = {}, {}
            for row in edited.to_dict('records'):
                fraction = float(row['Target (%)']) / 100
                if row['position']:
                    members.setdefault(row['owner'], {})[row['position']] = fraction
                else:
                    composition[row['category']] = fraction
            target = ExposureTargets(float(multiple), 'composition', composition, version=3,
                universe=universe, weights='mixed', members=members)
            validate_source_membership(result, target, config)
            save_exposure_targets(directory, target, config, revision)
        except (DataError, OSError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.rerun()
