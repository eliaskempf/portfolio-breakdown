"""Synthetic displayed snapshots age independently from prices and journals."""
from dataclasses import replace
from datetime import timedelta

import pandas as pd
import pytest

from portfolio_app.actual_portfolio import journal_positions
from portfolio_app.freshness_view import refresh_freshness
from portfolio_app.futures_investments import investment_positions
from portfolio_app.futures_valuation import freshness, select_valuations
from portfolio_app.portfolio import prepare_portfolio
from portfolio_app.positions import read_snapshot
from test_futures_investments import grouped
from test_futures_sessions import at, contract
from portfolio_app.derivative_types import PriceObservation


@pytest.mark.parametrize('group', [False, True])
def test_age_updates_metadata_only_and_retains_displayed_observation(tmp_path, group):
    service, journal, _ = grouped(tmp_path)
    c = contract()
    journal = replace(journal, contracts=(c,), positions=(replace(journal.positions[0], contract_id=c.id),))
    stamp = at('2026-01-09T20:45')
    quote = PriceObservation(c.id, '2026-01-09', 1010, kind='mark', quote_at=stamp.isoformat())
    valuations = select_valuations(journal, automatic={c.id: quote}, now=stamp)
    rows = journal_positions(prepare_portfolio(read_snapshot(tmp_path/'holdings.csv').holdings, service),
                             journal, service, valuations=valuations)
    if group:
        rows = investment_positions(rows, journal)
    before = rows.copy(deep=True)
    stale = refresh_freshness(rows, now=at('2026-01-12T00:26'))
    closed = refresh_freshness(rows, now=at('2026-01-10T12:00'))
    identity = 'investment:synthetic' if group else 'p'
    assert stale.set_index('position_id').loc[identity].valuation_status == 'stale'
    assert closed.set_index('position_id').loc[identity].valuation_status == 'market_closed'
    fields = ['position_id', 'shares', 'current_value_reporting', 'unrealized_gain_reporting',
              'signed_notional_reporting', 'quote_at', 'price_source']
    pd.testing.assert_frame_equal(before[fields], stale[fields])
    pd.testing.assert_frame_equal(before[fields], closed[fields])
    pd.testing.assert_frame_equal(rows, before)
    assert rows.attrs == before.attrs


def test_group_summary_excludes_closed_legs_and_keeps_readable_names():
    c = contract(); now = at('2026-01-07T12:00')
    q = PriceObservation(c.id, '2026-01-07', 100, kind='mark', quote_at=now.isoformat())
    contexts = {'old': freshness(q, contract=c, closed=True, now=now).context,
                'open': freshness(q, contract=c, now=now).context}
    rows = pd.DataFrame([dict(position_id='investment', valuation_status='delayed',
                             valuation_label='', valuation_detail='', valuation_qualified=False)])
    rows.attrs.update(futures_freshness=contexts, investment_members={'investment': ('old', 'open')},
                      futures_freshness_names={'old': 'Closed contract', 'open': 'Current contract'})
    assert refresh_freshness(rows, now=now).iloc[0].valuation_status == 'delayed'
    contexts['another'] = freshness(q, contract=c, now=now).context
    rows.attrs['investment_members']['investment'] = ('old', 'open', 'another')
    rows.attrs['futures_freshness_names']['another'] = 'Second current contract'
    stale = refresh_freshness(rows, now=now+timedelta(minutes=31)).iloc[0]
    assert stale.valuation_label == '2 dated futures valuations'
    assert 'Closed contract' not in stale.valuation_detail
    assert 'Current contract' in stale.valuation_detail
    assert 'Second current contract' in stale.valuation_detail


def test_no_futures_is_identity():
    rows = pd.DataFrame([dict(position_id='invented')])
    assert refresh_freshness(rows) is rows
