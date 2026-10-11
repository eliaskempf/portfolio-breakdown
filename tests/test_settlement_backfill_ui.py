"""Synthetic backfill drafts require review and survive unavailable dates."""
from datetime import date

from streamlit.testing.v1 import AppTest

from portfolio_app import account_journal as ledger
from portfolio_app.derivative_market import QuoteResult
from test_futures_investments import grouped
from test_settlement_backfill import observed
from test_ui import by_label


def test_retrieve_review_save_and_repeat_is_idempotent(tmp_path,monkeypatch):
    _,journal,contract = grouped(tmp_path)
    ledger.save_journal(tmp_path,journal,None)
    def quote(self,contract,day):
        if day == '2026-01-05': return QuoteResult(None,'unavailable','Invented missing price')
        return QuoteResult(observed(contract,day),'cached')
    monkeypatch.setattr('portfolio_app.derivative_market.SettlementCache.get',quote)
    app=AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.account_journal import load_journal
from portfolio_app.settlement_backfill_ui import render_settlement_backfill
root=Path({str(tmp_path)!r})
journal,revision=load_journal(root)
render_settlement_backfill(root,journal,revision,['p'],key='test_backfill_')
''').run()
    by_label(app.date_input,'From').set_value(date(2026,1,2))
    by_label(app.date_input,'Through').set_value(date(2026,1,6)).run()
    by_label(app.button,'Retrieve settlements').click().run()
    assert not app.exception
    assert ledger.load_journal(tmp_path)[0] == journal
    assert app.dataframe[0].value.Status.tolist() == ['Available','Unavailable','Available']
    assert 'Save settlements' not in [b.label for b in app.button]
    by_label(app.button,'Review settlements').click().run()
    assert ledger.load_journal(tmp_path)[0] == journal
    by_label(app.button,'Save settlements').click().run()
    assert not app.exception
    saved=ledger.load_journal(tmp_path)[0]
    settlements=[e for e in saved.events if e.kind == 'settlement']
    assert [e.date for e in settlements] == ['2026-01-02','2026-01-06']
    assert all(e.source == 'Invented settlement source' for e in settlements)
    by_label(app.button,'Retrieve settlements').click().run()
    assert app.dataframe[0].value.Status.tolist() == ['Already recorded','Unavailable','Already recorded']
    assert 'Review settlements' not in [b.label for b in app.button]
    assert ledger.load_journal(tmp_path)[0] == saved
