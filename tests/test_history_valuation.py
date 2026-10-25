"""Invented positions, with public exchange calendars and no market requests."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from portfolio_app.history_observations import Observation, ObservationStore, CoverageReview, HistoryScope, from_dict
from portfolio_app.history_valuation import closed_interval, price_at, listing_calendar
from portfolio_app.personal_performance import value_at
from portfolio_app.history_view import history_view
from portfolio_app.account_journal import Journal
from portfolio_app.security_history import SecurityBook, SecurityEvent as E, history_fingerprint


def quote(stamp='2024-12-27', **kw):
    return Observation('quote', 'price', 'synthetic', stamp, D(110), 'EUR',
                       listing='INVENTED', calendar='XETR', exchange_timezone='Europe/Berlin', **kw)


def test_closed_sessions_and_real_outage_are_different():
    store = ObservationStore((quote(),))
    assert price_at(store, ('synthetic',), '2024-12-29') == quote()
    assert price_at(store, ('synthetic',), '2024-12-30') is None
    assert closed_interval('XNYS', '2024-07-03', '2024-07-04')
    assert not closed_interval('XNYS', '2024-07-03', '2024-07-05')
    assert not closed_interval('unknown', '2024-07-03', '2024-07-04')


def test_no_inferred_calendar_or_carry_across_split():
    assert listing_calendar({'exchangeName': 'GER', 'exchangeTimezoneName': 'Europe/Berlin'}) == ('XETR', 'Europe/Berlin')
    assert listing_calendar({'exchangeName': 'GER', 'exchangeTimezoneName': 'UTC'}) == ('', '')
    assert price_at(ObservationStore((replace(quote(), calendar=''),)), ('synthetic',), '2024-12-29') is None
    split = E('split', 'split', '2024-12-28', quantity=2)
    assert price_at(ObservationStore((quote(),)), ('synthetic',), '2024-12-29', events=(split,)) is None


def test_weekend_trade_is_included_with_original_quote_provenance():
    scope = HistoryScope(('synthetic',))
    events = (E('open', 'opening', '2024-12-27', quantity=1, amount=100, currency='EUR'),
              E('buy', 'buy', '2024-12-28', quantity=1, price=100, currency='EUR', charges_known=True))
    journal = Journal(version=2, securities=(SecurityBook('synthetic', 'INVENTED', ('2','100','EUR'),
        records=events, active_ids=tuple(e.id for e in events)),))
    store = ObservationStore((quote(),), (CoverageReview('review', scope, '2024-12-27', '2024-12-29', history_fingerprint(journal)),))
    value = value_at(journal, scope, store, '2024-12-29', 'EUR')
    assert value.amount == 220
    assert value.evidence[0].as_of == '2024-12-27' and value.evidence[0].method == 'closed_market'
    view = history_view(journal, scope, store, 'All', today=date(2024,12,29))
    assert view.all_end == '2024-12-29' and view.all_result.money_weighted is not None
    assert view.all_result.net_contributions == 100 and view.all_result.gain == 10
    assert len(store.observations) == 1  # No invented Saturday/Sunday observation.
    assert view.points[-1]['Price as of'] == '2024-12-27'
    usd = replace(store, observations=(replace(quote(), currency='USD'),))
    assert value_at(journal, scope, usd, '2024-12-29', 'EUR').amount is None


def test_legacy_observations_load_without_listing_metadata():
    raw = dict(version=1, observations=[dict(id='old', kind='price', subject='synthetic',
        date='2024-12-27', amount='110', currency='EUR')])
    store = from_dict(raw)
    assert price_at(store, ('synthetic',), '2024-12-27').amount == 110
    assert price_at(store, ('synthetic',), '2024-12-29') is None


def test_metadata_refresh_is_a_revision_and_manual_observations_win():
    from portfolio_app.personal_market import merge_download
    old = replace(quote(source='Invented feed'), listing='', calendar='', exchange_timezone='')
    updated = merge_download(ObservationStore((old,)), (replace(quote(source='Invented feed'), id='new'),))
    assert len(updated.observations) == 2 and updated.observations[-1].replaces == old.id
    manual = ObservationStore((replace(old, source='manual'),))
    assert merge_download(manual, (replace(quote(), id='new'),)) == manual


def test_every_supported_venue_has_a_calendar():
    import exchange_calendars
    from portfolio_app.history_valuation import VENUES
    assert all(calendar in exchange_calendars.get_calendar_names() for calendar, _ in VENUES.values())


def test_bulk_download_validates_one_batch_and_retains_every_correction(monkeypatch):
    from datetime import timedelta
    import portfolio_app.history_observations as module
    from portfolio_app.personal_market import merge_download
    original=tuple(replace(quote(),id=f'old-{i}',date=(date(2024,1,1)+timedelta(days=i)).isoformat(),source='Invented feed') for i in range(1000))
    incoming=tuple(replace(o,id=f'new-{i}',amount=D(120)) for i,o in enumerate(original))
    store=ObservationStore(original)
    calls=[]
    validate=module.active_observations
    def counted(store):
        calls.append(True)
        return validate(store)
    monkeypatch.setattr(module,'active_observations',counted)
    result=merge_download(store,incoming)
    assert len(calls)==1  # Exactly one new-store graph validation.
    assert result.observations[:1000]==original
    assert all(o.replaces==previous.id for o,previous in zip(result.observations[1000:],original))
    assert len(validate(result))==1000
    assert merge_download(result,incoming) is result
