"""Scope-aware exposure policy comparisons for the strategic Overview."""

from portfolio_app.exposure_targets import exposure_gaps
from portfolio_app.holdings import DataError


TARGET_COLUMN_HELP = {
    'Exposure': 'Current signed market exposure in reporting currency.',
    '% of exposure': 'Current exposure divided by the selected category’s current total exposure.',
    'Target exposure': 'Desired exposure in reporting currency, using the saved whole-portfolio net-equity target.',
    'Target (%)': 'Desired exposure divided by the selected category’s total desired exposure.',
    'Gap': 'Target minus current exposure in reporting currency. This exposure gap is not a cash purchase budget.',
}


def market_target_summary(result, targets, allocation, bucket=''):
    """Compare disjoint categories; composition uses the selected scope.

    Monetary targets retain the saved whole-portfolio net-equity basis. A leaf
    shows its category total because the exposure policy has no position targets.
    Signed amounts remain signed; nonpositive totals have no composition share.
    """
    if targets.universe != 'market':
        raise DataError('Equity-only targets cannot be compared with total market exposure.')
    if bucket == 'unassigned':
        gaps = exposure_gaps(result, targets, allocation)
        gaps = gaps.loc[gaps['Bucket ID'].eq('')].copy()
    else:
        gaps = exposure_gaps(result, targets, allocation, parent=bucket)
    currency = result.reporting_currency
    table = gaps.rename(columns={
        f'Current exposure ({currency})': 'Exposure',
        f'Desired exposure ({currency})': 'Target exposure',
        f'Gap ({currency})': 'Gap',
        'Coverage': 'Status',
    })[['Category', 'Exposure', 'Target exposure', 'Gap', 'Status']].copy()
    current_total = table.Exposure.sum()
    target_total = table['Target exposure'].sum()
    table['% of exposure'] = table.Exposure * 100 / current_total if current_total > 0 else float('nan')
    table['Target (%)'] = table['Target exposure'] * 100 / target_total if target_total > 0 else float('nan')
    table['Status'] = table.Status.replace('Known', '')
    return table[['Category', 'Exposure', '% of exposure', 'Target exposure', 'Target (%)', 'Gap', 'Status']].sort_values(
        ['Exposure', 'Category'], ascending=[False, True], ignore_index=True)


def render_market_target_summary(result, allocation, data_dir, bucket, *, open_targets=None):
    """Return a saved-policy table, or explain why only current exposure is shown."""
    import streamlit as st
    from portfolio_app.exposure_targets import load_exposure_targets

    if data_dir is None:
        return None
    try:
        targets, _ = load_exposure_targets(data_dir, allocation)
    except DataError as exc:
        st.warning(str(exc))
        targets = None
    table = None
    if targets is None:
        st.caption('Exposure targets are unset. Set desired total exposure and category composition separately from capital targets.')
    elif targets.universe != 'market':
        st.caption(f'Saved targets cover equity only ({targets.multiple:g}× portfolio net equity). '
                   'This view includes other market exposure, so those targets are not compared here. '
                   'Edit exposure targets to explicitly choose Total market.')
    else:
        try:
            table = market_target_summary(result, targets, allocation, bucket)
        except DataError as exc:
            st.warning(str(exc))
        st.caption(f'Target: {targets.multiple:g}× whole-portfolio net equity · amounts in {result.reporting_currency}. '
                   'Gap = target − current exposure, not a cash purchase budget. '
                   'Current and target percentages each use their selected category’s exposure total; '
                   'target amounts retain the whole-portfolio policy.')
        if bucket and bucket != 'unassigned' and not allocation.children(bucket):
            st.caption('Exposure targets apply to this category as a whole; its positions are listed below.')
    if open_targets is not None:
        st.button('Edit exposure targets' if targets else 'Set exposure targets',
                  key='overview_exposure_targets', on_click=open_targets, type='tertiary')
    else:
        st.caption('Configure this policy under Rebalance → Targets → Exposure targets.')
    return table
