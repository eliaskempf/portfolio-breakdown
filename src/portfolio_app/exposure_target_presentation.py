"""Scope-aware exposure policy comparisons for the strategic Overview."""

from portfolio_app.exposure_targets import exposure_gaps
from portfolio_app.holdings import DataError


TARGET_COLUMN_HELP = {
    'Exposure': 'Current signed market exposure in reporting currency.',
    '% of exposure': 'Current exposure divided by the selected category’s current total exposure.',
    'Target exposure': 'Desired exposure in reporting currency, using the saved whole-portfolio net-equity target.',
    'Target (%)': 'Desired exposure divided by the selected category’s total desired exposure.',
    'Gap': 'Target minus current exposure in reporting currency. This exposure gap is not a cash purchase budget.',
    'Gap (pp)': 'Current exposure share minus target share, in percentage points. Positive means overweight.',
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
    if bucket != 'unassigned':
        unassigned = result.exposures.loc[result.exposures.bucket_id.eq(''), targets.field]
        capital = result.capital
        unassigned_capital = capital.loc[capital.bucket_id.eq(''), 'value'] if 'bucket_id' in capital else unassigned.iloc[:0]
        if unassigned.eq(0).all() and unassigned_capital.eq(0).all():
            gaps = gaps.loc[gaps['Bucket ID'].ne('')]
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
    table['Gap (pp)'] = table['% of exposure']-table['Target (%)']
    table['Status'] = table.Status.replace('Known', '')
    return table[['Category', 'Exposure', '% of exposure', 'Target exposure', 'Target (%)', 'Gap', 'Gap (pp)', 'Status']].sort_values(
        ['Exposure', 'Category'], ascending=[False, True], ignore_index=True)


def render_market_target_summary(result, allocation, data_dir, bucket, *, open_targets=None, controls=None, composition_table=None, open_composition=None):
    """Return a saved-policy table, or explain why only current exposure is shown."""
    from contextlib import nullcontext
    import streamlit as st
    from portfolio_app.exposure_targets import load_exposure_targets

    if data_dir is None:
        return None
    try:
        targets, _ = load_exposure_targets(data_dir, allocation)
    except DataError as exc:
        st.warning(str(exc))
        targets = None
        composition_table = None
    table = None
    callback = open_targets
    help_text = 'Set desired exposure and category targets. Capital targets are separate.'
    if targets is None and composition_table is not None:
        # Existing percentages describe a mix, independently of total leverage.
        # Do not invent an absolute exposure policy or change saved targets.
        table = composition_table.copy()
        callback = open_composition or open_targets
        help_text = 'Edit existing category and position target percentages. These compare with exposure shares; no total exposure multiple is assumed.'
    elif targets is None:
        pass
    elif targets.universe != 'market':
        help_text = 'Saved targets cover equity only. Edit the policy to compare Total market exposure.'
        st.badge('Equity-only targets', icon=':material/info:')
    else:
        try:
            table = market_target_summary(result, targets, allocation, bucket)
        except DataError as exc:
            st.warning(str(exc))
        help_text = f'Total exposure target: {targets.multiple:g}× portfolio net equity. Targets apply to categories, not individual positions.'
    if callback is not None:
        with controls if controls is not None else nullcontext():
            st.button('Edit', icon=':material/edit:', key='overview_exposure_targets',
                      on_click=callback, type='tertiary', help=help_text)
    return table
