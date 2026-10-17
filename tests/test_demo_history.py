"""Synthetic demo calculations verified without Streamlit or network access."""
from datetime import date, timedelta
from decimal import Decimal as D
import math
import pytest

from portfolio_app.demo_history import create_history_demo
from portfolio_app.account_journal import load_journal, replay
from portfolio_app.history_observations import load_observations, HistoryScope
from portfolio_app.personal_performance import calculate_history
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import effective_holdings
from portfolio_app.holdings import DataError


def test_demo_has_reconciled_holdings_and_independent_portfolio_return(tmp_path):
    end=date(2025,10,1);start=end-timedelta(days=730)
    directory=create_history_demo(tmp_path/'invented-demo',end=end)
    journal,_=load_journal(directory);observations,_=load_observations(directory)
    holdings=effective_holdings(read_snapshot(directory/'holdings.csv').holdings,journal.securities)
    assert holdings.shares.tolist()==[110,140]
    trades={e.id:e for b in journal.securities for e in b.records}
    buy=trades['demo-equity-buy'];sale=trades['demo-equity-sale'];fund_buy=trades['demo-cash-fund-buy']
    expected_cash=D(10000+5000+120-1000)-20*buy.price-5+10*sale.price-3-40*fund_buy.price
    assert replay(journal).cash['demo-brokerage','EUR']==expected_cash.quantize(D('.01'))
    scope=HistoryScope(tuple(b.position_id for b in journal.securities),('demo-brokerage',))
    result=calculate_history(journal,scope,observations,start.isoformat(),end.isoformat())
    expected_end=110*D(140)+140*D(52)+expected_cash.quantize(D('.01'))
    assert result.opening_value==25000 and result.ending_value==expected_end
    assert result.net_contributions==4000
    assert result.gain==expected_end-29000
    assert result.money_weighted is not None and result.time_weighted is not None
    # Reconcile current unrealized gains with the recorded-period gain:
    # the snapshot already contains gains; sales and distributions add later P&L.
    remaining_cost=90*D(80)+20*buy.price+5+100*D(50)+40*fund_buy.price
    unrealized=110*D(140)+140*D(52)-remaining_cost
    realized=10*sale.price-3-10*D(80)
    opening_gain=100*(D(100)-D(80))
    assert result.gain == (unrealized+realized+120-opening_gain).quantize(D(".01"))
    assert result.gain != unrealized
    prices={(o.subject,o.date):o.amount for o in observations.observations if o.kind=='price'}
    deposit=(start+timedelta(days=180)).isoformat();withdrawal=(start+timedelta(days=600)).isoformat()
    before_deposit=100*prices['demo-world',deposit]+100*prices['demo-cash-fund',deposit]+10000
    before_withdrawal=110*prices['demo-world',withdrawal]+140*prices['demo-cash-fund',withdrawal]+expected_cash.quantize(D('.01'))+1000
    expected_twr=before_deposit/D(25000)*before_withdrawal/(before_deposit+5000)*expected_end/(before_withdrawal-1000)-1
    assert result.time_weighted==pytest.approx(float(expected_twr))
    # Independently discount the explicitly invented deposit, withdrawal and
    # endpoint to verify XIRR's equation, rather than reusing the flow transform.
    r=result.money_weighted
    npv=-25000-5000/(1+r)**(180/365)+1000/(1+r)**(600/365)+float(expected_end)/(1+r)**2
    assert abs(npv)<.001
    for book in journal.securities:
        result=calculate_history(journal,HistoryScope((book.position_id,)),observations,start.isoformat(),end.isoformat())
        assert result.money_weighted is not None and math.isfinite(result.money_weighted)
        assert result.gain is not None
    with pytest.raises(DataError,match='existing work'):
        create_history_demo(directory,end=end)


def test_injected_market_series_is_used_without_filling_missing_dates(tmp_path):
    # Explicitly invented provider-shaped data: no network or private data.
    days=[(date(2024,1,1)+timedelta(days=i)).isoformat() for i in range(730) if (date(2024,1,1)+timedelta(days=i)).weekday()<5]
    prices={'world':{day:D(100)+D(i)/10 for i,day in enumerate(days)},
            'money-market':{day:D(150)+D(i)/100 for i,day in enumerate(days)}}
    root=create_history_demo(tmp_path/'market-shaped-demo',end=date(2026,1,1),market_prices=prices)
    store,_=load_observations(root)
    actual=[o for o in store.observations if o.kind=='price']
    assert all(date.fromisoformat(o.date).weekday()<5 for o in actual)
    assert next(o.amount for o in actual if o.subject=='demo-world' and o.date==days[-1])==prices['world'][days[-1]]
    assert (root/'.market-history-demo').exists()
    journal,_=load_journal(root)
    scope=HistoryScope(tuple(b.position_id for b in journal.securities),('demo-brokerage',))
    result=calculate_history(journal,scope,store,min(o.date for o in actual),max(o.date for o in actual))
    assert result.money_weighted is not None and result.time_weighted is not None
