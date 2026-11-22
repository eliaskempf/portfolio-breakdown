"""Synthetic mixed ownership cases, independent of private portfolio data."""
from dataclasses import replace

import pandas as pd
import pytest

from portfolio_app.exposure.exposure_planning import plan_exposure
from portfolio_app.exposure.exposure_target_presentation import market_target_hierarchy
from portfolio_app.exposure.exposure_targets import ExposureTargets, exposure_gaps, load_exposure_targets, save_exposure_targets, validate_targets
from portfolio_app.exposure.mixed_exposure_targets import target_key
from portfolio_app.exposure.portfolio_exposure import calculate_portfolio_exposure
from portfolio_app.portfolio.allocation import Allocation, Bucket
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.strategic import strategic_summary
from test_flexible_categories import example


def mixed(values=(20.,20.,60.)):
    config, positions = example(values)
    positions = positions.assign(instrument_type='equity', exposure_kind='equity', ticker='', isin='', reporting_currency='EUR')
    targets = ExposureTargets(1., 'composition', {'sleeve':1., 'group':.5}, version=3, universe='market',
                              weights='mixed', members={'sleeve':{'p3':.5}})
    result = calculate_portfolio_exposure(positions, allocation=config)
    return config, positions, targets, result


def test_mixed_scopes_conserve_exposure_and_roundtrip(tmp_path):
    config, positions, targets, result = mixed()
    save_exposure_targets(tmp_path, targets, config, None)
    assert load_exposure_targets(tmp_path, config)[0] == targets
    for scope, expected in [(None,2), ('',1), ('sleeve',2), ('group',1)]:
        gaps = exposure_gaps(result, targets, config, parent=scope)
        assert len(gaps) == expected
        assert gaps['Current exposure (EUR)'].sum() == (40 if scope == 'group' else 100)
        assert gaps['Desired exposure (EUR)'].sum() == (50 if scope == 'group' else 100)
    gaps = exposure_gaps(result, targets, config).set_index('Bucket ID')
    assert gaps.loc[target_key('sleeve','p3'), 'Gap (EUR)'] == -10.
    assert gaps.loc[target_key('group'), 'Gap (EUR)'] == 10.
    # Reading and comparing exposure targets does not change capital targets.
    assert positions.within_bucket_target.eq(.5).all()


def test_expanded_and_flat_exposure_keep_direct_targets_and_group_blanks():
    config, positions, targets, result = mixed()
    for flat in (False, True):
        composition = strategic_summary(positions, config, 'sleeve', flat=flat, hierarchy=not flat, identities=True).rename(
            columns={'Value':'Exposure', 'Current (%)':'% of exposure'})
        table = market_target_hierarchy(result, targets, config, composition, 'sleeve', flat=flat).set_index('row_id')
        assert table.loc['position:p3','Target (%)'] == 50.
        assert table.loc['position:p3','Gap (pp)'] == 10.
        assert pd.isna(table.loc['position:p1','Target (%)'])
        if not flat:
            assert table.loc['category:group','Target (%)'] == 50.
    nested = Allocation((*config.buckets, Bucket('other','Invented other',target=.6)),3)
    nested = replace(nested,buckets=(replace(nested.buckets[0],target=.4),*nested.buckets[1:]))
    positions = pd.concat([positions, positions.iloc[[2]].assign(position_id='p4', bucket_id='other',current_value_reporting=150.)],ignore_index=True)
    targets = replace(targets,composition={'sleeve':.4,'group':.5,'other':.6})
    result = calculate_portfolio_exposure(positions,allocation=nested)
    gaps = exposure_gaps(result,targets,nested)
    assert gaps['Desired exposure (EUR)'].sum() == 250.
    assert gaps.set_index('Bucket ID').loc[target_key('sleeve','p3'),'Desired exposure (EUR)'] == 50.


def test_planner_rebalances_combined_group_and_direct_investment():
    config, positions, targets, result = mixed()
    plan = plan_exposure(result, targets, config, positions, permitted=['p1','p2','p3'],allow_sales=True)
    assert plan.complete
    trades = plan.trades.set_index('Position ID')['Exposure change (EUR)']
    assert trades['p3'] == pytest.approx(-10.,abs=1e-5)
    assert trades.reindex(['p1','p2']).sum() == pytest.approx(10.,abs=1e-5)
    # Internal member drift never triggers a trade for a combined target.
    config, positions, targets, result = mixed((49.,1.,50.))
    plan = plan_exposure(result,targets,config,positions,permitted=['p1','p2','p3'],allow_sales=True)
    assert plan.complete and plan.trades.empty


def test_contribution_fees_permissions_and_protection_keep_financial_basis():
    config, positions, targets, result = mixed()
    plan = plan_exposure(result, targets, config, positions, permitted=['p1'],contribution=20.,fee_per_unit=.1)
    assert set(plan.trades['Position ID']) == {'p1'}
    assert plan.equity == pytest.approx(120.-plan.fees)
    assert plan.gaps['Desired exposure (EUR)'].sum() == pytest.approx(plan.equity)
    assert plan.unallocated_cash >= 0.
    protected = replace(config,buckets=(replace(config.buckets[0],sell_protected=True),config.buckets[1]))
    plan = plan_exposure(result, targets, protected, positions, permitted=['p1','p2','p3'],allow_sales=True)
    assert plan.trades.empty and not plan.complete


@pytest.mark.parametrize('change', ['missing','moved','extra','nan','bad_sum','flexible_members','duplicate'])
def test_invalid_or_stale_targets_require_explicit_review(change):
    config, positions, targets, result = mixed()
    if change == 'missing':
        targets = replace(targets,members={'sleeve':{'absent':.5}})
    elif change == 'moved':
        positions.loc[positions.position_id.eq('p3'),'bucket_id'] = 'group'
        result = calculate_portfolio_exposure(positions,allocation=config)
    elif change == 'extra':
        positions = pd.concat([positions,positions.iloc[[2]].assign(position_id='new',current_value_reporting=0.)])
        result = calculate_portfolio_exposure(positions,allocation=config)
    elif change == 'nan':
        targets = replace(targets,members={'sleeve':{'p3':float('nan')}})
    elif change == 'bad_sum':
        targets = replace(targets,members={'sleeve':{'p3':.7}})
    elif change == 'flexible_members':
        targets = replace(targets,members={'sleeve':{'p3':.5},'group':{'p1':1.}})
    else:
        config = replace(config,buckets=(config.buckets[0],replace(config.buckets[1],allocation_mode='targets')))
        targets = replace(targets,members={'sleeve':{'p3':.5},'group':{'p3':1.}})
    with pytest.raises(DataError):
        exposure_gaps(result,targets,config)


def test_missing_coverage_and_old_policies_remain_guarded():
    config, positions, targets, result = mixed()
    with pytest.raises(DataError,match='coverage'):
        exposure_gaps(replace(result,exposure_complete=False),targets,config)
    old = ExposureTargets(1.,'composition',{'group':1.},version=2,universe='market')
    validate_targets(old,config)
    with pytest.raises(DataError,match='Review exposure targets'):
        exposure_gaps(result,old,config)


from test_flexible_category_ui import workspace  # noqa: E402,F401


@pytest.fixture
def exposure_workspace(workspace):
    from portfolio_app.funds.leverage_settings import LeverageSettings, save_leverage_settings
    config, _, targets, _ = mixed()
    rows = pd.read_csv(workspace/'holdings.csv').assign(instrument_type='equity',exposure_kind='equity')
    rows.to_csv(workspace/'holdings.csv',index=False)
    save_leverage_settings(workspace,LeverageSettings(enabled=True,active_basis='exposure'),None)
    save_exposure_targets(workspace,targets,config,None)
    return workspace


def test_mixed_overview_editor_and_plan_ui(exposure_workspace):
    from test_ui import launch, activate, by_label
    from list_helpers import list_frame
    app = launch(exposure_workspace,tab='Overview')
    by_label(app.selectbox,'Category').set_value('sleeve').run()
    assert not app.exception
    table = list_frame(app,'Market exposure').set_index('Category')
    assert table.loc['Invented investment 2','Target (%)'] == 50.
    by_label(app.checkbox,'Flatten groups').set_value(True).run()
    flat = list_frame(app,'Market exposure')
    assert flat['Target (%)'].isna().sum() == 2
    activate(app,'Rebalance','Targets')
    by_label(app.get('button_group'),'Target measure').set_value('Exposure targets').run()
    assert not app.exception and not app.error
    assert by_label(app.selectbox,'Composition weights').value == 'mixed'
    assert not any(button.label == 'Save exposure targets' for button in app.button)
    by_label(app.selectbox,'Target hierarchy').set_value('sleeve').run()
    by_label(app.button,'Edit targets').click().run()
    frame = next(item.value for item in app.dataframe if 'identity' in item.value)
    assert len(frame) == 2
    key = next(key for key in app.session_state.to_dict() if key.startswith('overview_target_editor_'))
    changes = {i:{'Target':60. if row.identity == 'group' else 40.} for i,row in frame.iterrows()}
    app.session_state[key] = {'edited_rows':changes,'added_rows':[],'deleted_rows':[]}
    app.button(key='overview_target_save').click().run()
    assert not app.exception and not app.error
    saved,_ = load_exposure_targets(exposure_workspace,mixed()[0])
    assert saved.composition['group'] == .6 and saved.members['sleeve']['p3'] == .4
    activate(app,'Rebalance','Plan')
    by_label(app.multiselect,'Permitted instruments').set_value(['p1','p2','p3']).run()
    by_label(app.button,'Calculate exposure plan').click().run()
    assert not app.exception and not app.error
    assert any(heading.value == 'Suggested exposure trades' for heading in app.subheader)
    app.run()
    assert any(heading.value == 'Suggested exposure trades' for heading in app.subheader)
    by_label(app.number_input, 'External contribution (EUR)').set_value(25.).run()
    assert not any(heading.value == 'Suggested exposure trades' for heading in app.subheader)


def test_legacy_policy_invalidated_by_grouping_can_be_replaced_in_editor(exposure_workspace):
    import json
    from dataclasses import asdict
    from test_ui import launch, by_label
    # A formerly leaf sleeve acquired a child: retain the old file until explicit save.
    old = ExposureTargets(1.,'composition',{'sleeve':1.},version=2,universe='market')
    path = exposure_workspace/'exposure-targets.json'
    path.write_text(json.dumps(asdict(old)))
    original = path.read_bytes()
    app = launch(exposure_workspace,tab='Rebalance',subtab='Targets')
    by_label(app.get('button_group'),'Target measure').set_value('Exposure targets').run()
    by_label(app.selectbox,'Composition weights').set_value('mixed').run()
    assert not app.exception
    assert path.read_bytes() == original
    frame = next(item.value for item in app.dataframe if 'Within' in item.value)
    key = next(key for key in app.session_state.to_dict() if key.startswith('exposure_mixed_editor_'))
    app.session_state[key] = {'edited_rows':{i:{'Target (%)':100. if row.category == 'sleeve' else 50.} for i,row in frame.iterrows()},'added_rows':[],'deleted_rows':[]}
    by_label(app.button,'Save exposure targets').click().run()
    assert not app.exception and not app.error
    assert load_exposure_targets(exposure_workspace,mixed()[0])[0].weights == 'mixed'


@pytest.mark.parametrize('values,expected', [((20.,20.,60.),'p1'), ((40.,30.,30.),'p2')])
def test_flexible_exposure_preferences_only_break_equivalent_plan_ties(values, expected):
    config, positions, targets, result = mixed(values)
    plan = plan_exposure(result,targets,config,positions,permitted=['p1','p2','p3'],allow_sales=True)
    group_trades = plan.trades.loc[plan.trades['Position ID'].isin(['p1','p2'])]
    assert group_trades['Position ID'].tolist() == [expected]
    assert plan.complete


def test_mixed_future_notional_and_fund_factor_stay_separate_from_capital():
    from portfolio_app.funds.leverage_settings import FundFactor
    from portfolio_app.studies.futures import FuturePosition
    config, positions, targets, _ = mixed()
    positions.loc[0,'instrument_type'] = 'etf'
    future = FuturePosition(contracts=1,entry_price=.1,mark_price=.1,bucket_id='sleeve',reference_capital_eur=10.)
    targets = replace(targets,multiple=1.2,composition={'sleeve':1.,'group':.5},members={'sleeve':{'p3':.4,'future:overlay':.1}})
    result = calculate_portfolio_exposure(positions,allocation=config,future=future,
        fund_factors=(FundFactor('p1',2.,'Invented fixed factor','2026-01-01'),))
    gaps = exposure_gaps(result,targets,config).set_index('Bucket ID')
    assert gaps.loc[target_key('group'),'Current exposure (EUR)'] == 60.
    assert gaps.loc[target_key('sleeve','future:overlay'),'Current exposure (EUR)'] == 10.
    assert result.net_equity == 110.
    plan = plan_exposure(result,targets,config,positions,permitted=['future:overlay'],future=future,
                        margin_per_contract=1.,collateral_limit=10.,max_contracts=5)
    assert plan.trades['Funded trade (EUR)'].sum() == 0.
    assert plan.gaps['Desired exposure (EUR)'].sum() == pytest.approx(plan.equity*1.2)
    assert plan.margin_required <= plan.collateral_remaining


def test_overlay_only_preserves_direct_funded_exposure_beside_subcategories():
    from portfolio_app.studies.futures import FuturePosition
    config, positions, _, _ = mixed()
    future = FuturePosition(contracts=1,entry_price=.1,mark_price=.1,bucket_id='sleeve',reference_capital_eur=10.)
    result = calculate_portfolio_exposure(positions,allocation=config,future=future)
    targets = ExposureTargets(1.1,'overlay_only',{},'sleeve',version=2,universe='market')
    gaps = exposure_gaps(result,targets,config,parent='sleeve').set_index('Bucket ID')
    assert gaps.loc['group','Current exposure (EUR)'] == 40.
    assert gaps.loc['sleeve','Current exposure (EUR)'] == 70.
    assert gaps['Desired exposure (EUR)'].sum() == pytest.approx(121.)
    plan = plan_exposure(result,targets,config,positions,permitted=['future:overlay'],future=future,
                        margin_per_contract=1.,collateral_limit=10.,max_contracts=5)
    assert plan.gaps['Desired exposure (EUR)'].sum() == pytest.approx(121.)
    assert plan.trades['Position ID'].tolist() == ['future:overlay']
    assert plan.trades['Units / contracts'].item() == 1
    assert plan.trades['Funded trade (EUR)'].sum() == 0.


def test_saving_legacy_policy_keeps_its_original_file_schema(tmp_path):
    import json
    config, _, _, _ = mixed()
    target = ExposureTargets(1.,'composition',{'group':1.},version=2,universe='market')
    save_exposure_targets(tmp_path,target,config,None)
    raw = json.loads((tmp_path/'exposure-targets.json').read_text())
    assert raw['version'] == 2 and 'members' not in raw
    assert load_exposure_targets(tmp_path,config)[0] == target
