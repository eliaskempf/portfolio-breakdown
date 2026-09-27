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
    assert sources['% of asset exposure'].tolist() == [40, 60]


def test_source_percentages_show_each_etf_and_account_without_portfolio_dilution():
    selected = pd.DataFrame([
        dict(position_id='direct', name='Invented Company', account='First'),
        dict(position_id='fund-a', name='Invented Global ETF', account='First'),
        dict(position_id='fund-b', name='Invented Regional ETF', account='First'),
        dict(position_id='fund-b-2', name='Invented Regional ETF', account='Second'),
    ])
    exposures = pd.DataFrame([
        dict(asset_id='company', value=value, source_position_id=position, direct_or_indirect=kind)
        for position, kind, value in [('direct', 'direct', 40.), ('fund-a', 'indirect', 30.),
                                      ('fund-b', 'indirect', 20.), ('fund-b-2', 'indirect', 10.)]
    ] + [dict(asset_id='unrelated', value=900., source_position_id='fund-a', direct_or_indirect='indirect')])
    sources = exposure_sources(exposures, selected, 'company')
    assert sources['Value (EUR)'].tolist() == [40, 30, 20, 10]
    assert sources['% of asset exposure'].tolist() == [40, 30, 20, 10]
    assert sources['Account'].tolist() == ['First', 'First', 'First', 'Second']
    exposures.loc[3, 'value'] = float('nan')
    partial = exposure_sources(exposures, selected, 'company')
    assert partial['% of asset exposure'].isna().all()
    assert partial['Value (EUR)'].iloc[:3].tolist() == [40, 30, 20]
    exposures['value'] = 0.
    assert exposure_sources(exposures, selected, 'company')['% of asset exposure'].isna().all()


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


def test_grouped_totals_keep_residuals_zero_and_unknown_sources_distinct():
    rows = [('a', 'direct', 20.), ('a', 'indirect', 30.), ('a', 'indirect', 40.),
            ('b', 'direct', 10.), ('b', 'indirect', float('nan')),
            ('other', 'indirect', 15.), ('empty', 'direct', 0.)]
    frame = pd.DataFrame([dict(asset_id=a, asset_name=a, ticker='', direct_or_indirect=k, value=v) for a, k, v in rows])
    result = asset_exposure_table(frame, complete=False).set_index('asset_id')
    assert result.loc['a', ['Direct (EUR)', 'ETF-derived (EUR)', 'Total (EUR)']].tolist() == [20, 70, 90]
    assert result.loc['b', 'Direct (EUR)'] == 10
    assert result.loc['b', ['ETF-derived (EUR)', 'Total (EUR)']].isna().all()
    assert result.loc['other', ['Direct (EUR)', 'ETF-derived (EUR)', 'Total (EUR)']].tolist() == [0, 15, 15]
    assert result.loc['empty', ['Direct (EUR)', 'ETF-derived (EUR)', 'Total (EUR)']].tolist() == [0, 0, 0]
    assert result['Allocation %'].isna().all()
