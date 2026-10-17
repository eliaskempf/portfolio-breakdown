"""Category membership uses invented holdings and account components only."""
from dataclasses import replace
from decimal import Decimal as D
import pandas as pd

from portfolio_app.account_journal import Account, CashLink, Event, Journal
from portfolio_app.allocation import Allocation, Bucket
from portfolio_app.history_scope import category_history_scope
from portfolio_app.security_history import SecurityBook


def fixture():
    config=Allocation((Bucket('core','Core'),Bucket('equity','Equity',parent='core'),Bucket('cash','Cash',parent='core'),Bucket('other','Other')))
    holdings=pd.DataFrame([dict(position_id='stock',bucket_id='equity'),dict(position_id='cash',bucket_id='cash'),dict(position_id='closed',bucket_id='other')])
    journal=Journal(version=2,accounts=(Account('account','Invented broker'),),
        events=(Event('opening','2024-01-01','cash_open','account','EUR',D(100)),),
        cash_links=(CashLink('account','EUR','cash'),),
        securities=(SecurityBook('stock','invented',('1','10','EUR')),SecurityBook('removed','removed',('0','0','EUR'))))
    return journal,holdings,config


def test_root_retains_closed_books_and_category_includes_descendants_without_double_cash():
    journal,holdings,config=fixture()
    root=category_history_scope(journal,holdings,config)
    assert root.positions == ('closed','removed','stock') and root.accounts == ('account',)
    equity=category_history_scope(journal,holdings,config,'equity')
    assert equity.positions == ('stock',) and not equity.accounts
    core=category_history_scope(journal,holdings,config,'core')
    assert core.positions == ('stock',) and core.accounts == ('account',)
    cash=category_history_scope(journal,holdings,config,'cash')
    assert not cash.positions and cash.accounts == ('account',)


def test_account_spanning_categories_is_not_silently_added_whole_or_dropped():
    journal,holdings,config=fixture()
    journal=replace(journal,events=(*journal.events,Event('usd','2024-01-01','cash_open','account','USD',D(50))))
    scope=category_history_scope(journal,holdings,config,'cash')
    assert not scope.accounts and scope.unresolved
    assert 'span categories' in scope.unresolved[0]
    assert not category_history_scope(journal,holdings,config).unresolved
