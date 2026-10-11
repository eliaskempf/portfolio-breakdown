"""Apply accepted exact-contract daily settlements once through the account ledger."""
from datetime import date, timedelta

from portfolio_app import account_journal as ledger
from portfolio_app.derivative_market import _known_product
from portfolio_app.holdings import DataError
from portfolio_app.settlement_archive import SettlementArchive
from portfolio_app.settlement_backfill import propose_settlements, settlement_days
from portfolio_app.workspace_lock import workspace_lock


def propose_automatic_settlements(journal, archive, *, today=None):
    """Use the same snapshot/reconciliation/quantity guards as reviewed backfill.

    Existing, corrected and voided settlements remain user-owned. A source
    revision never replaces a recorded price. Unknown dates are never invented.
    """
    today = today or date.today()
    candidate = journal
    state = ledger.replay(journal)
    for position in state.positions.values():
        contract = position.contract
        try:
            _known_product(contract)
        except DataError:
            continue
        # Include original facts, not just effective ones: voiding or moving a
        # settlement to another date must not cause it to be silently recreated.
        touched = {e.date for e in journal.events if e.position_id == position.spec.id
                   and e.kind in {'settlement', 'final_settlement'}}
        available = {stamp: quote for stamp, quote in archive.entries(contract).items()
                     if stamp < today.isoformat() and stamp not in touched
                     and archive.latest(contract, stamp).price == quote.price}
        stamps = sorted(available)
        while stamps:
            start = stamps[0]
            end = min(stamps[-1], (date.fromisoformat(start)+timedelta(days=30)).isoformat())
            eligible = {day.date for day in settlement_days(candidate, position.spec.id, start, end)
                        if day.status == 'Ready to retrieve'}
            observations = [available[stamp] for stamp in stamps if stamp <= end and stamp in eligible]
            if observations:
                candidate = propose_settlements(candidate, position.spec.id, start, end, observations, automatic=True)
            stamps = [stamp for stamp in stamps if stamp > end]
    return candidate


def apply_archived_settlements(directory, *, today=None):
    """Reload inside the workspace fence, merge, and atomically save one batch."""
    with workspace_lock(directory):
        journal, revision = ledger.load_journal(directory)
        candidate = propose_automatic_settlements(journal, SettlementArchive(directory), today=today)
        if candidate == journal:
            return 0
        ledger.save_journal(directory, candidate, revision)
        return len(candidate.events)-len(journal.events)
