import pandas as pd
import pytest

from portfolio_app.exposure.exposure_tables import asset_exposure_table, asset_search_mask, complete_exposures, exposure_sources


def test_complete_asset_view_merges_sources_and_keeps_unclassified():
    exposures = pd.DataFrame([
        dict(asset_id='a', asset_name='Invented A', ticker='SYN-A', value=20., direct_or_indirect='direct', source_position_id='p1'),
        dict(asset_id='a', asset_name='Invented A', ticker='SYN-A', value=30., direct_or_indirect='indirect', source_position_id='p2'),
        dict(asset_id='b', asset_name='Invented B', ticker='', value=50., direct_or_indirect='indirect', source_position_id='p2'),
    ])
    result = asset_exposure_table(exposures, classifications={'a': {'labels': (('Theme',),)}})
    assert result['Total'].sum() == 100
    assert result['Allocation %'].sum() == 100
    assert result.loc[result.asset_id.eq('a'), ['Direct', 'ETF-derived']].iloc[0].tolist() == [20, 30]
    assert 'Unclassified' in result.loc[result.asset_id.eq('b'), 'Labels'].iloc[0]
    selected = pd.DataFrame([dict(position_id='p1', name='Direct position', account='First'),
                             dict(position_id='p2', name='Invented ETF', account='Second')])
    sources = exposure_sources(exposures, selected, 'a')
    assert sources['Value'].tolist() == [20, 30]
    assert sources.Exposure.tolist() == ['Direct', 'ETF']
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
    assert sources['Value'].tolist() == [40, 30, 20, 10]
    assert sources['% of asset exposure'].tolist() == [40, 30, 20, 10]
    assert sources['Account'].tolist() == ['First', 'First', 'First', 'Second']
    exposures.loc[3, 'value'] = float('nan')
    partial = exposure_sources(exposures, selected, 'company')
    assert partial['% of asset exposure'].isna().all()
    assert partial['Value'].iloc[:3].tolist() == [40, 30, 20]
    exposures['value'] = 0.
    assert exposure_sources(exposures, selected, 'company')['% of asset exposure'].isna().all()


def test_unpriced_positions_and_partial_totals_remain_unknown():
    selected = pd.DataFrame([dict(position_id='p2', id='a', name='Invented A', ticker='SYN-A',
                                 analysis_asset_id=float('nan'), analysis_asset_name=float('nan'), current_value_reporting=float('nan'))])
    exposures = pd.DataFrame([dict(asset_id='a', asset_name='Invented A', ticker='SYN-A', value=20., direct_or_indirect='direct', source_position_id='p1')])
    complete = complete_exposures(exposures, selected)
    assert len(complete) == 2
    result = asset_exposure_table(complete, complete=False)
    assert pd.isna(result.iloc[0]['Total'])
    assert pd.isna(result.iloc[0]['Direct'])
    assert pd.isna(result.iloc[0]['Allocation %'])
    assert result.iloc[0]['ETF-derived'] == 0


def test_source_position_value_and_asset_weight_keep_both_denominators_distinct():
    selected = pd.DataFrame([
        dict(position_id='direct', name='Invented Company', current_value_reporting=600.),
        dict(position_id='fund', name='Invented Broad ETF', current_value_reporting=24000.),
    ])
    exposures = pd.DataFrame([
        dict(asset_id='company', source_position_id='direct', direct_or_indirect='direct', value=600.),
        dict(asset_id='company', source_position_id='fund', direct_or_indirect='indirect', value=1000.),
        dict(asset_id='company', source_position_id='fund', direct_or_indirect='indirect', value=2000.),
        dict(asset_id='other', source_position_id='fund', direct_or_indirect='indirect', value=21000.),
    ])
    sources = exposure_sources(exposures, selected, 'company').set_index('Exposure')
    assert sources.loc['ETF', 'Position value'] == 24000
    assert sources.loc['ETF', 'Asset weight (%)'] == 12.5
    assert sources.loc['ETF', 'Value'] == 3000
    assert sources.loc['ETF', '% of asset exposure'] == pytest.approx(100 * 3000 / 3600)
    assert sources.loc['Direct', 'Asset weight (%)'] == 100
    selected.loc[1, 'current_value_reporting'] = 0.
    exposures.loc[exposures.source_position_id.eq('fund'), 'value'] = 0.
    zero = exposure_sources(exposures, selected, 'company').set_index('Exposure')
    assert pd.isna(zero.loc['ETF', 'Asset weight (%)'])
    assert zero.loc['ETF', '% of asset exposure'] == 0
    selected.loc[1, 'current_value_reporting'] = float('nan')
    exposures.loc[1, 'value'] = float('nan')
    missing = exposure_sources(exposures, selected, 'company').set_index('Exposure')
    assert pd.isna(missing.loc['ETF', 'Position value'])
    assert pd.isna(missing.loc['ETF', 'Asset weight (%)'])
    assert missing['% of asset exposure'].isna().all()


def test_single_asset_sources_group_only_its_rows_and_keep_full_market_denominators(monkeypatch):
    from portfolio_app.exposure import exposure_tables as tables
    selected = pd.DataFrame([dict(position_id=key, name=key, account='Invented', current_value_reporting=100.)
                             for key in ('short', 'partial', 'unrelated')])
    exposures = pd.DataFrame([
        ('wanted', 'short', 'future', -6., 'Future'),
        ('other', 'short', 'future', -18., 'Future'),
        ('wanted', 'short', 'future', -2., 'Future'),
        ('wanted', 'partial', 'indirect', 12., 'ETF'),
        ('unknown', 'partial', 'indirect', float('nan'), 'ETF'),
        ('unrelated', 'unrelated', 'direct', 100., 'Direct'),
    ], columns=['asset_id', 'source_position_id', 'direct_or_indirect', 'value', 'source_channel'])
    for measure in (None, 'Market exposure'):
        full = tables.source_contributions(exposures, selected, measure=measure)
        for asset in (*exposures.asset_id.unique(), 'no-match'):
            shown = tables.exposure_sources(exposures, selected, asset, measure=measure)
            expected = full.loc[full.asset_id.eq(asset), shown.columns]
            pd.testing.assert_frame_equal(shown.reset_index(drop=True), expected.reset_index(drop=True))
    group = tables.asset_source_amounts
    def only_requested(rows, positions):
        assert rows.asset_id.tolist() == ['wanted'] * 3
        assert positions.position_id.tolist() == ['short', 'partial']
        return group(rows, positions)
    monkeypatch.setattr(tables, 'asset_source_amounts', only_requested)
    shown = tables.exposure_sources(exposures, selected, 'wanted', measure='Market exposure')
    assert shown.iloc[0]['Position value'] == -26.
    assert pd.isna(shown.iloc[1]['Position value'])


def test_grouped_totals_keep_residuals_zero_and_unknown_sources_distinct():
    rows = [('a', 'direct', 20.), ('a', 'indirect', 30.), ('a', 'indirect', 40.),
            ('b', 'direct', 10.), ('b', 'indirect', float('nan')),
            ('other', 'indirect', 15.), ('empty', 'direct', 0.)]
    frame = pd.DataFrame([dict(asset_id=a, asset_name=a, ticker='', direct_or_indirect=k, value=v) for a, k, v in rows])
    result = asset_exposure_table(frame, complete=False).set_index('asset_id')
    assert result.loc['a', ['Direct', 'ETF-derived', 'Total']].tolist() == [20, 70, 90]
    assert result.loc['b', 'Direct'] == 10
    assert result.loc['b', ['ETF-derived', 'Total']].isna().all()
    assert result.loc['other', ['Direct', 'ETF-derived', 'Total']].tolist() == [0, 15, 15]
    assert result.loc['empty', ['Direct', 'ETF-derived', 'Total']].tolist() == [0, 0, 0]
    assert result['Allocation %'].isna().all()


def test_compact_previews_cap_sources_without_changing_exposure_percentages():
    from portfolio_app.interface.views.exposure.exposure_assets_ui import preview_facts
    selected = pd.DataFrame([dict(position_id=f'p{i}', name=f'Invented Fund {i}', account='', current_value_reporting=1000.) for i in range(6)])
    exposures = pd.DataFrame([dict(asset_id='a', asset_name='Invented Company', ticker='', source_position_id=f'p{i}',
                                    direct_or_indirect='indirect', value=float((i + 1) * 10)) for i in range(6)])
    remainder = exposures.copy()
    remainder['asset_id'], remainder['asset_name'] = 'other', 'Invented Other'
    remainder['value'] = 1000. - remainder['value']
    exposures = pd.concat([exposures, remainder], ignore_index=True)
    facts = preview_facts(exposures, selected)
    more, *items = facts['assets']['a']
    assert more == 2
    assert len(items) == 4
    label, amount, share = items[0]
    assert (facts['labels'][label], amount, share) == ('Invented Fund 5', 60., 28.57)
    assert facts['labels'][items[-1][0]] == 'Invented Fund 2'
    assert asset_exposure_table(exposures).loc[lambda t: t.asset_id.eq('a'), 'Allocation %'].item() == 3.5
    selected = pd.concat([selected, pd.DataFrame([dict(position_id='unpriced', name='Invented Unpriced',
                                                      account='', current_value_reporting=float('nan'))])], ignore_index=True)
    # A missing unrelated valuation does not erase known asset-relative shares.
    assert preview_facts(exposures, selected) == facts
    exposures.loc[0, 'value'] = float('nan')
    _, *items = preview_facts(exposures, selected)['assets']['a']
    assert all(share is None for _, _, share in items)


def test_asset_search_matches_literal_text_across_name_and_ticker_preserving_indices():
    rows = pd.DataFrame({'name': ['Invented Straße', None, 'Other', None],
                         'ticker': ['ZZ.1', 'Z[2]', None, None]}, index=[8, 4, 8, 3])
    for query, indices in [(' STRASSE zz. ', [8]), ('[2]', [4]), ('other', [8]), ('ZZx1', [])]:
        assert rows.index[asset_search_mask(rows.name, rows.ticker, query)].tolist() == indices


def test_packed_asset_rows_preserve_nulls_labels_numbers_and_source_identity():
    import json
    from portfolio_app.interface.shared.list_ui import frame_rows, packed_frame_rows
    table = pd.DataFrame({'asset_id': ['a', 'b'], 'Total': [-0.125, None],
                          'Labels': [['Invented sector', 'A / B'], []]})
    rows = packed_frame_rows(table, id_column='asset_id')
    payload = rows.payload
    decoded = [dict(zip(payload['fields'], row)) for row in json.loads(payload['values'])]
    assert [dict(row, id=row[payload['idColumn']]) for row in decoded] == frame_rows(table, id_column='asset_id')
    assert rows.ids == {'a', 'b'}


def test_source_values_aggregate_only_repeated_position_legs(monkeypatch):
    from portfolio_app.exposure.exposure_tables import asset_source_values

    rows = pd.DataFrame([
        ('a', 'held', 'indirect', 20., None),
        ('a', 'other', 'future', -5., 'Future'),
        ('a', None, 'direct', 900., 'Direct'),
        ('b', 'held', 'indirect', 0., 'ETF'),
    ], columns=['asset_id', 'source_position_id', 'direct_or_indirect', 'value', 'source_channel'],
       index=[5, 2, 5, 2])
    grouped_pairs = []
    groupby = pd.DataFrame.groupby
    def track(frame, by, *args, **kwargs):
        if isinstance(by, list):
            grouped_pairs.append(tuple(by))
        return groupby(frame, by, *args, **kwargs)
    monkeypatch.setattr(pd.DataFrame, 'groupby', track)

    unique = asset_source_values(rows)
    assert not grouped_pairs
    assert unique['Value'].tolist() == [20., -5., 0.]
    assert unique['% of asset exposure'][:2].tolist() == pytest.approx([100 * 20 / 15, -100 * 5 / 15])
    assert pd.isna(unique['% of asset exposure'].iloc[2])

    extra = rows.iloc[[0, 3]].copy()
    extra['value'] = [5., float('nan')]
    extra['source_channel'] = 'Different second channel'
    combined = asset_source_values(pd.concat([rows, extra]))
    assert grouped_pairs == [('asset_id', 'source_position_id', 'direct_or_indirect')]
    assert combined['Value'][:2].tolist() == [25., -5.]
    assert combined['% of asset exposure'][:2].tolist() == [125., -25.]
    assert pd.isna(combined['Value'].iloc[2])
    assert pd.isna(combined['Exposure'].iloc[0])


def test_previews_share_labels_across_positions_and_keep_unknown_and_signed_values():
    from portfolio_app.interface.views.exposure.exposure_assets_ui import preview_facts

    selected = pd.DataFrame([
        dict(position_id=position, name='Invented shared', account='Invented account')
        for position in ('p1', 'p2')
    ])
    rows = pd.DataFrame([
        ('a', 'p1', 'direct', 2.675, 'Direct'),
        ('b', 'p2', 'direct', 1., 'Direct'),
        ('a', 'p1', 'future', -1., 'Future'),
        ('c', 'unknown', 'indirect', pd.NA, pd.NA),
    ], columns=['asset_id', 'source_position_id', 'direct_or_indirect', 'value', 'source_channel'])
    rows['value'] = rows.value.astype('Float64')
    facts = preview_facts(rows, selected)
    assert facts['labels'] == ['Direct · Invented shared · Invented account',
                               'Future · Invented shared · Invented account', 'Unavailable']
    assert facts['assets'] == {'a': [0, [0, 2.67, 159.7], [1, -1., -59.7]],
                               'b': [0, [0, 1., 100.]], 'c': [0, [2, None, None]]}
