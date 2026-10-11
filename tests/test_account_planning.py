"""Synthetic integer plans with actual-account funding boundaries."""
from dataclasses import replace
import pytest
from portfolio_app.account_journal import Event, append_events, MarginRequirement
from portfolio_app.account_planning import plan_account_futures
from portfolio_app.actual_portfolio import journal_positions
from portfolio_app.allocation import Allocation, Bucket
from portfolio_app.exposure_targets import ExposureTargets
from portfolio_app.portfolio import prepare_portfolio
from portfolio_app.positions import read_snapshot
from portfolio_app.portfolio_exposure import calculate_portfolio_exposure
from test_actual_portfolio import invented


def setup(tmp_path,currency='EUR'):
    service,journal,_=invented(tmp_path)
    journal=replace(journal,positions=(replace(journal.positions[0],bucket_id='core'),))
    valued=prepare_portfolio(read_snapshot(tmp_path/'holdings.csv').holdings,service,reporting_currency=currency)
    valued=journal_positions(valued,journal,service,reporting_currency=currency)
    allocation=Allocation((Bucket('core','Invented core'),))
    result=calculate_portfolio_exposure(valued,allocation=allocation,reporting_currency=currency)
    target=ExposureTargets(1.5,'composition',{'core':1.},version=2,universe='market')
    return journal,valued,allocation,result,target


@pytest.mark.parametrize('currency',['EUR','USD','GBP'])
def test_integer_plan_uses_actual_ids_native_margin_and_reporting_units(tmp_path,currency):
    journal,valued,allocation,result,target=setup(tmp_path,currency)
    before=journal
    plan=plan_account_futures(result,target,allocation,valued,journal,permitted=['p'],margins={'p':4000},fees={'p':4},limits={'p':5})
    assert plan.trades.Contracts.tolist()==[1]
    assert plan.trades['Final contracts'].tolist()==[3]
    assert plan.equity==pytest.approx(result.net_equity-4*valued.loc[valued.position_id.eq('p'),'current_fx_rate'].iloc[0])
    assert journal==before


def test_unsettled_losses_and_pending_debits_are_not_spendable(tmp_path):
    journal,valued,allocation,result,target=setup(tmp_path)
    journal=append_events(journal,[Event('bill','2026-01-03','fee',account_id='a',currency='EUR',amount=10000,value_date='2027-01-01')])
    valued.loc[valued.position_id.eq('p'),'mark_price']=900.
    plan=plan_account_futures(result,target,allocation,valued,journal,permitted=['p'],margins={'p':4000},fees={'p':4},limits={'p':5})
    assert plan.trades['Final contracts'].tolist()==[1]


def test_account_total_requirement_is_floor_not_added_twice(tmp_path):
    journal,valued,allocation,result,target=setup(tmp_path)
    journal=replace(journal,margin_requirements=(MarginRequirement('total','a','EUR',10000,8000,'2026-01-02'),))
    plan=plan_account_futures(result,target,allocation,valued,journal,permitted=['p'],margins={'p':4000},limits={'p':5})
    assert plan.trades['Final contracts'].tolist()==[3]


def test_unselected_open_contract_requires_explicit_margin(tmp_path):
    from portfolio_app.account_journal import PositionSpec
    from portfolio_app.holdings import DataError
    journal,valued,allocation,result,target=setup(tmp_path)
    journal=replace(journal,positions=(*journal.positions, PositionSpec('other','a',journal.contracts[0].id,'core')))
    journal=append_events(journal,[Event('other-open','2026-01-02','trade',account_id='a',position_id='other',quantity=1,price=1000)])
    with pytest.raises(DataError,match='every unselected'):
        plan_account_futures(result,target,allocation,valued,journal,permitted=['p'],margins={'p':4000})
