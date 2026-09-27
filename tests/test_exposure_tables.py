import pandas as pd

from portfolio_app.exposure_tables import asset_exposure_table, complete_exposures, exposure_sources


def test_complete_asset_view_merges_sources_and_keeps_unclassified():
    exposures = pd.DataFrame([
        dict(asset_id='a', asset_name='Invented A', ticker='SYN-A', value=20., direct_or_indirect='direct', source_position_id='p1'),
        dict(asset_id='a', asset_name='Invented A', ticker='SYN-A', value=30., direct_or_indirect='indirect', source_position_id='p2'),
        dict(asset_id='b', asset_name='Invented B', ticker='', value=50., direct_or_indirect='indirect', source_position_id='p2'),
    ])
    result = asset_exposure_table(exposures, classifications={'a': {'labels': (('Theme',),)}})
    assert result['Total (EUR)'].sum() == 100
    assert result['Allocation %'].sum() == 100
    assert result.loc[result.asset_id.eq('a'), ['Direct (EUR)', 'ETF-derived (EUR)']].iloc[0].tolist() == [20, 30]
    assert 'Unclassified' in result.loc[result.asset_id.eq('b'), 'Labels'].iloc[0]
    selected = pd.DataFrame([dict(position_id='p1', name='Direct position', account='First'),
                             dict(position_id='p2', name='Invented ETF', account='Second')])
    sources = exposure_sources(exposures, selected, 'a')
    assert sources['Value (EUR)'].tolist() == [20, 30]
    assert sources.Exposure.tolist() == ['Direct', 'Through ETF']


def test_unpriced_positions_and_partial_totals_remain_unknown():
    selected = pd.DataFrame([dict(position_id='p2', id='a', name='Invented A', ticker='SYN-A',
                                 analysis_asset_id=float('nan'), analysis_asset_name=float('nan'), current_value_eur=float('nan'))])
    exposures = pd.DataFrame([dict(asset_id='a', asset_name='Invented A', ticker='SYN-A', value=20., direct_or_indirect='direct', source_position_id='p1')])
    complete = complete_exposures(exposures, selected)
    assert len(complete) == 2
    result = asset_exposure_table(complete, complete=False)
    assert pd.isna(result.iloc[0]['Total (EUR)'])
    assert pd.isna(result.iloc[0]['Direct (EUR)'])
    assert pd.isna(result.iloc[0]['Allocation %'])
    assert result.iloc[0]['ETF-derived (EUR)'] == 0
