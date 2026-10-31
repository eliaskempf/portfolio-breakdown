"""Invented history playground with optional market prices; never reads user data."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
import json
import math
from pathlib import Path

import yaml

from portfolio_app.account_journal import Account, Event, Journal, save_journal
from portfolio_app.cost_basis import component
from portfolio_app.demo import create_demo_data, _write_csv
from portfolio_app.history_commands import book_for
from portfolio_app.history_observations import HistoryScope, Observation, ObservationStore, save_observations
from portfolio_app.personal_performance import external_flows, value_at
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import Charge, SecurityEvent, SecurityLot, replay_securities


def create_history_demo(directory: Path, *, end: date | None = None, market_prices=None) -> Path:
    """Generate two years of invented ownership, cash flows and reviewed returns.

    The start is a known holding, not an invented claim of complete lifetime
    transactions. Prices are generated or explicitly supplied market observations. Existing
    directories are refused so that edits in an earlier playground stay intact.
    """
    from portfolio_app.holdings import DataError
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise DataError('Use an empty directory for the history demo; existing work will not be replaced.')
    end = end or date.today()
    start = end-timedelta(days=730)
    common = []
    if market_prices is not None:
        common = sorted(set(market_prices['world']) & set(market_prices['money-market']))
        common = [d for d in common if start.isoformat() <= d <= end.isoformat()]
        if len(common) < 200:
            raise DataError('The market history demo needs at least 200 common observed market dates.')
        start,end = date.fromisoformat(common[0]),date.fromisoformat(common[-1])
    def stamp(offset):
        if common and offset >= 0:
            return common[round(offset/730*(len(common)-1))]
        return (start+timedelta(days=offset)).isoformat()
    def price(asset, offset):
        if market_prices is not None:
            return D(str(market_prices[asset][stamp(offset)]))
        fraction = offset/730
        value = (100+40*fraction+8*math.sin(4*math.pi*fraction) if asset == 'world'
                 else 50+2*fraction+.2*math.sin(2*math.pi*fraction))
        return D(str(value)).quantize(D('.0001'))
    create_demo_data(directory, live=False)
    _write_csv(directory/'holdings.csv',
        ['position_key','id','name','ticker','shares','acquisition_price','acquisition_currency','account','bucket_id','within_bucket_target','instrument_type','exposure_kind'],
        [('demo-world','world','World equity — demo holding','XDWD.DE',110,80,'EUR','Demo brokerage','equity',1,'etf','equity'),
         ('demo-cash-fund','money-market','Money market — demo holding','XEON.DE',140,50,'EUR','Demo brokerage','money-market',1,'etf','non_equity')])
    (directory/'allocation.yaml').write_text(yaml.safe_dump({'version':2,'buckets':[
        {'id':'equity','name':'Equities','target':.65},{'id':'money-market','name':'Money market','target':.35}]}))
    snapshot = read_snapshot(directory/'holdings.csv')
    account = 'demo-brokerage'
    journal = Journal(version=2, accounts=(Account(account,'Demo brokerage'),), events=(
        Event('demo-cash-start',stamp(0),'cash_open',account,'EUR',D(10000)),
        Event('demo-contribution',stamp(180),'cash_flow',account,'EUR',D(5000)),
        Event('demo-withdrawal',stamp(600),'cash_flow',account,'EUR',D(-1000)),
    ))
    books = []
    for row in snapshot.holdings.itertuples():
        initial_cost = (price(row.id,0)*100*D('.95')).quantize(D('.01')) if market_prices is not None else D(8000) if row.id == 'world' else D(5000)
        lot = SecurityLot(row.id+'-initial-lot',D(100),(component(100,initial_cost,'EUR',stamp(-30)),),date=stamp(-30))
        events = [SecurityEvent(row.id+'-start','opening',stamp(0),quantity=D(100),amount=initial_cost,currency='EUR',lots=(lot,),source='synthetic_demo')]
        if row.id == 'world':
            events.extend((
                SecurityEvent('demo-equity-buy','buy',stamp(181),quantity=D(20),price=price(row.id,181),currency='EUR',charges=(Charge(D(5),'EUR'),),charges_known=True,source='synthetic_demo'),
                SecurityEvent('demo-distribution','distribution',stamp(365),amount=D(120),currency='EUR',charges_known=True,source='synthetic_demo'),
                SecurityEvent('demo-equity-sale','sell',stamp(500),quantity=D(10),price=price(row.id,500),currency='EUR',charges=(Charge(D(3),'EUR'),),charges_known=True,source='synthetic_demo'),
            ))
        else:
            events.append(SecurityEvent('demo-cash-fund-buy','buy',stamp(365),quantity=D(40),price=price(row.id,365),currency='EUR',charges_known=True,source='synthetic_demo'))
        book = book_for(journal, snapshot.holdings.set_index('position_id',drop=False).loc[row.position_id])
        books.append(replace(book, account_id=account, records=tuple(events), active_ids=tuple(e.id for e in events)))
    journal = replace(journal, securities=tuple(books))
    # Match the seed summary to the invented current FIFO balance, keeping the
    # ordinary source fingerprint guard intact when history becomes authoritative.
    frame = snapshot.holdings
    states = replay_securities(journal.securities).positions
    from portfolio_app.cost_basis import resolve_cost, fingerprint
    for index,row in frame.iterrows():
        state = states[row.position_id]
        frame.at[index,'acquisition_price'] = resolve_cost(list(state.costs),'EUR').amount/float(state.quantity)
    # Keep only actual CSV columns; read_snapshot also supplies derived columns.
    columns = ['position_key','id','name','ticker','shares','acquisition_price','acquisition_currency','account','bucket_id','within_bucket_target','instrument_type','exposure_kind']
    frame[columns].to_csv(directory/'holdings.csv',index=False)
    rows = read_snapshot(directory/'holdings.csv').holdings.set_index('position_id')
    journal = replace(journal, securities=tuple(replace(b,source_summary=tuple(fingerprint(rows.loc[b.position_id]))) for b in books))
    save_journal(directory,journal,None)
    observations = []
    for b in books:
        values = ([(d,v) for d,v in sorted(market_prices[b.instrument_id].items()) if stamp(0) <= d <= stamp(730)]
                  if market_prices is not None else [(stamp(i),price(b.instrument_id,i)) for i in range(731)])
        for index,(day,value) in enumerate(values):
            observations.append(Observation(f'{b.instrument_id}-price-{index}','price',b.position_id,day,D(str(value)),'EUR',
                source='Yahoo Close, split normalization reversed' if market_prices is not None else 'Invented demo price'))
    scopes = [HistoryScope(tuple(b.position_id for b in books),(account,))]
    scopes.extend(HistoryScope((b.position_id,)) for b in books)
    store = ObservationStore(tuple(observations))
    # Exact demo boundaries for portfolio deposits/withdrawals and the fee-free
    # cash-fund purchase. Daily returns use the generated daily prices. Exact
    # equity returns additionally require distribution-time boundary values.
    for scope in (scopes[0], scopes[2]):
        flows, reasons = external_flows(journal,scope,store,stamp(0),stamp(730),'EUR')
        assert not reasons
        for flow in flows:
            after = value_at(journal,scope,store,flow.date,'EUR').amount
            for side, value in (('before',after-flow.amount),('after',after)):
                observations.append(Observation(f'{scope.key}-{flow.id}-{side}','value',scope.key,flow.date,value,'EUR',side=side,boundary=flow.id,source='Invented exact flow boundary'))
    save_observations(directory,replace(store,observations=tuple(observations)),None)
    (directory/'demo_prices.json').write_text(json.dumps({'prices':{
        'XDWD.DE':{'price':float(price('world',730)),'currency':'EUR','observed_at':end.isoformat()+'T12:00:00+00:00'},
        'XEON.DE':{'price':float(price('money-market',730)),'currency':'EUR','observed_at':end.isoformat()+'T12:00:00+00:00'},
    },'fx':{}}))
    if market_prices is not None:
        (directory/'.market-history-demo').write_text(json.dumps({'source':'Yahoo Finance', 'through':end.isoformat()}))
    (directory/'.history-demo').write_text('Invented holdings and transactions; edits persist in this directory.\n')
    return directory


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Create a separate history playground with invented holdings.')
    parser.add_argument('directory',type=Path)
    parser.add_argument('--market-prices',action='store_true',help='Download real market prices; holdings and transactions remain invented.')
    args = parser.parse_args()
    prices = None
    if args.market_prices:
        from portfolio_app.personal_market import fetch_security_prices
        start=(date.today()-timedelta(days=760)).isoformat()
        end=date.today().isoformat()
        prices={asset:{o.date:o.amount for o in fetch_security_prices(ticker,asset,start,end)}
                for asset,ticker in [('world','XDWD.DE'),('money-market','XEON.DE')]}
    create_history_demo(args.directory,market_prices=prices)
    print('History demo with invented holdings created. See the history playground command in docs/user/development.md to launch it.')


if __name__ == '__main__':
    main()
