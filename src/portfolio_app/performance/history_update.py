"""Prepare a replacement for an already visible chart from immutable ledger evidence."""
from concurrent.futures import Future
from dataclasses import dataclass

from portfolio_app.market.market_data import coordinator
from portfolio_app.performance.history_observations import load_observations
from portfolio_app.performance.history_view import HistoryView
from portfolio_app.performance.personal_performance import HistoryCalculation


@dataclass(frozen=True)
class HistoryUpdate:
    revision: str | None
    calculation: HistoryCalculation
    view: HistoryView


def start_update(directory, journal, previous_revision, scope, period, target, today):
    """Return a session-owned future. The shared coordinator bounds and deduplicates workspace work."""
    result = Future()
    def prepare():
        try:
            from portfolio_app.performance.history_view import history_view
            observations, revision = load_observations(directory)
            if revision == previous_revision:
                result.set_result(None)
                return
            calculation = HistoryCalculation(journal, observations)
            from portfolio_app.performance.history_view_cache import load_or_compute
            signature = calculation.fingerprint, revision, scope, period, target, today, True, True
            view = load_or_compute(directory, signature, lambda: history_view(journal, scope,
                observations, period, today=today, target=target, calculation=calculation, period_annualized=False))
            result.set_result(HistoryUpdate(revision, calculation, view))
        except Exception as exc:
            result.set_exception(exc)
    if coordinator.request(directory, 'prepare-portfolio-history', prepare):
        return result
    return None
