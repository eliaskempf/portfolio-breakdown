"""Shared, explicitly invented tax fixture. Never accesses a working portfolio."""
from decimal import Decimal as D
import json

from portfolio_app.account_journal import Journal, save_journal
from portfolio_app.security_history import SecurityBook, SecurityEvent as E
from portfolio_app.positions import read_snapshot
from portfolio_app.cost_basis import fingerprint
from portfolio_app.tax_evidence import save_inputs


def create_tax_sample(directory):
    directory.mkdir(exist_ok=True, parents=True)
    (directory/'holdings.csv').write_text('id,name,ticker,isin,instrument_type,shares,acquisition_price,acquisition_currency,account\n'
        'fund-a,Invented Alpha ETF,SYN-A,SYNTHETIC001,etf,100,100,EUR,Invented broker A\n'
        'fund-b,Invented Beta ETF,SYN-B,SYNTHETIC002,etf,100,100,EUR,Invented broker A\n'
        'fund-a,Invented Alpha ETF,SYN-A,SYNTHETIC001,etf,50,100,EUR,Invented broker B\n')
    rows = read_snapshot(directory/'holdings.csv').holdings
    books = []
    for row in rows.to_dict('records'):
        e = E('opening-'+row['position_id'], 'opening', '2024-12-31', quantity=D(str(row['shares'])),
              amount=D(str(row['shares']))*100, currency='EUR')
        books.append(SecurityBook(row['position_id'], row['id'], fingerprint(row), records=(e,), active_ids=(e.id,)))
    journal = Journal(version=2, securities=tuple(books))
    save_journal(directory, journal, None)
    raw = {'version': 1, 'funds': {}, 'settings': {}}
    for year in (2025, 2026):
        for identity in ('SYNTHETIC001', 'SYNTHETIC002'):
            raw['funds'][f'{year}:{identity}'] = dict(start_price='100', end_price='110',
                distribution_mode='Accumulating / no distributions', exemption='.30', source='Invented tax fixture')
    save_inputs(directory, raw, None)
    (directory/'demo_prices.json').write_text(json.dumps({'prices': {ticker: {
        'price': 110, 'currency': 'EUR', 'observed_at': '2026-11-06T12:00:00+00:00'} for ticker in ('SYN-A', 'SYN-B')}, 'fx': {}}))
    return rows, journal
