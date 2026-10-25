"""Invented prices and trades; public Stuttgart session facts only."""
from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

from portfolio_app.account_journal import Journal
from portfolio_app.history_observations import CoverageReview, HistoryScope, ObservationStore
from portfolio_app.history_valuation import closed_interval, listing_calendar, price_at
from portfolio_app.personal_market import fetch_security_prices, merge_download
from portfolio_app.personal_performance import calculate_history
from portfolio_app.security_history import SecurityBook, SecurityEvent, history_fingerprint


def stuttgart_prices():
    class InventedFeed:
        history_metadata = {'currency': 'EUR', 'exchangeName': 'STU', 'exchangeTimezoneName': 'Europe/Berlin'}

        def history(self, **kwargs):
            return pd.DataFrame({'Close': [100, 110], 'Stock Splits': [0, 0]},
                index=pd.to_datetime(['2024-10-04', '2024-10-07']))

    return fetch_security_prices('INVENTED.SG', 'synthetic', '2024-10-04', '2024-10-07',
        factory=lambda ticker: InventedFeed())


def test_stuttgart_metadata_refresh_and_independent_monday_return():
    downloaded = stuttgart_prices()
    assert all(o.calendar == 'XSTU' and o.exchange_timezone == 'Europe/Berlin' for o in downloaded)
    legacy = tuple(replace(o, id='old-'+o.id, calendar='', exchange_timezone='') for o in downloaded)
    store = merge_download(ObservationStore(legacy), downloaded)
    assert store.observations[:2] == legacy  # Append corrections, preserve old evidence.
    assert len(store.observations) == 4
    assert price_at(store, ('synthetic',), '2024-10-06').date == '2024-10-04'
    assert price_at(store, ('synthetic',), '2024-10-08') is None
    events = (SecurityEvent('opening', 'opening', '2024-10-04', quantity=2, amount=200, currency='EUR'),
        SecurityEvent('buy', 'buy', '2024-10-07', quantity=1, price=100, currency='EUR', charges_known=True))
    journal = Journal(version=2, securities=(SecurityBook('synthetic', 'INVENTED', ('3', '100', 'EUR'),
        records=events, active_ids=tuple(e.id for e in events)),))
    scope = HistoryScope(('synthetic',))
    store = replace(store, reviews=(CoverageReview('review', scope, '2024-10-04', '2024-10-07', history_fingerprint(journal)),))
    result = calculate_history(journal, scope, store, '2024-10-04', '2024-10-07')
    # Independently: opening 2*100, contribution 100, ending 3*110.
    assert result.gain == Decimal(30)
    assert result.time_weighted == pytest.approx(330 / (200 + 100) - 1)


def test_stuttgart_holidays_open_weekdays_and_wrong_timezone():
    # Published Stuttgart 2026 calendar: Easter Monday closed, Whit Monday open.
    assert closed_interval('XSTU', '2026-04-02', '2026-04-06')
    assert not closed_interval('XSTU', '2026-04-02', '2026-04-07')
    assert not closed_interval('XSTU', '2026-05-22', '2026-05-25')
    assert listing_calendar({'exchangeName': 'STU', 'exchangeTimezoneName': 'UTC'}) == ('', '')
    assert listing_calendar({'exchangeName': 'UNKNOWN', 'exchangeTimezoneName': 'Europe/Berlin'}) == ('', '')
