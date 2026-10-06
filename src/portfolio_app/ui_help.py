"""Shared explanations for columns used by several portfolio views."""

COLUMN_HELP = {
    'Category': 'Category in the saved allocation hierarchy.',
    'Investment': 'Instrument whose holding or suggested purchase is shown.',
    'Asset': 'Underlying asset, combining direct holdings and indirect fund exposure.',
    'name': 'Name of the underlying holding reported by the fund source.',
    'Ticker': 'Exchange-qualified identifier used to retrieve market prices.',
    'ticker': 'Exchange-qualified identifier reported for this underlying holding.',
    'Account': 'Account or storage label of the contributing portfolio position.',
    'Source': 'Portfolio position contributing exposure to this asset.',
    'Sources': 'Contributing positions; expand to see their individual contributions.',
    'Exposure': 'Whether exposure comes from a direct holding or a fund breakdown.',
    'Labels': 'Saved classifications for the selected taxonomy; unclassified assets remain visible.',
    'Total (EUR)': 'Combined direct and indirect exposure to this asset in EUR.',
    'Allocation %': 'Asset exposure divided by the value of the selected portfolio positions.',
    'Position value (EUR)': 'Full value of the source position before assigning its underlying asset weight.',
    'Asset weight (%)': 'Share of the source position invested in this underlying asset.',
    '% of asset exposure': 'This source’s contribution divided by total exposure to this asset.',
    'Cost (EUR)': 'Known recorded purchase cost in EUR; incomplete cost coverage remains indicated.',
    'Gain (EUR)': 'Unrealized gain on holdings with known EUR purchase cost.',
    'Return (%)': 'Unrealized gain divided by known recorded cost, expressed as a percentage.',
    'Coverage': 'Extent to which recorded purchase costs support the displayed performance.',
    'Status': 'Missing data or other qualifications affecting interpretation of this row.',
    'Gap (pp)': 'Current allocation minus target allocation, in percentage points.',
    'Trade (EUR)': 'Suggested purchase amount in EUR; no trade is placed.',
    'Current (EUR)': 'Position value before the suggested contribution.',
    'After (EUR)': 'Estimated position value after the suggested contribution.',
    'Fund allocation %': 'Reported share of the fund; partial holdings are not scaled to 100%.',
    'Selected ETF exposure (EUR)': 'Underlying holding weight multiplied by the selected fund positions’ value.',
}


def column_help(column: str, *, scope: str = 'the selected portfolio') -> str:
    if column in {'Current (%)', 'Target (%)'}:
        return ('Current value' if column == 'Current (%)' else 'Desired allocation') + f' as a percentage of {scope}.'
    if column == 'Value (EUR)':
        return f'Current value or exposure in EUR within {scope}.'
    if column.startswith('classification:'):
        return 'Classification reported for this underlying holding; missing labels remain unclassified.'
    return COLUMN_HELP.get(column, '')
