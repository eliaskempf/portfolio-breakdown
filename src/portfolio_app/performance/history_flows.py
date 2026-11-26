"""Lazy dated flow facts for one immutable calculation context."""
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal


def position_flow_inputs(calculation):
    """Index original posting and event references for single-security boundaries."""
    from portfolio_app.ledger.security_history import affected_positions
    inputs = defaultdict(lambda: (defaultdict(list), defaultdict(list)))
    for posting in calculation.securities().cash:
        cash, _ = inputs[posting.position_id]
        cash[posting.date].append(posting)
    for book in calculation.journal.securities:
        for event in calculation._events[book.position_id]:
            for identity in affected_positions(book, event):
                _, securities = inputs[identity]
                securities[event.date].append((book, event))
    return inputs


class DatedFlows:
    def __init__(self, calculation, scope, target, *, position_inputs=None):
        self.calculation, self.scope, self.target = calculation, scope, target
        if position_inputs is None:
            self.dates = calculation._flow_dates
            self.cash, self.securities, self.accounts = calculation.flow_inputs()
        else:
            self.cash, self.securities = position_inputs
            self.accounts = {}
            self.dates = sorted(self.cash.keys() | self.securities.keys())
        self.buckets = {}
        self.starts = {}
        self.last = None
        self.last_total = None

    def interval(self, start, end, *, include_account_start=True):
        from portfolio_app.performance.personal_performance import external_flows
        calculation = self.calculation
        dates = self.dates
        lower, upper = bisect_right(dates, start), bisect_right(dates, end)
        opening_allowed = include_account_start and start <= end
        key = start, upper, opening_allowed
        if self.last is not None and self.last[0] == key:
            return self.last[1]
        cash, securities, accounts = self.cash, self.securities, self.accounts
        missing = [i for i in range(lower, upper) if dates[i] not in self.buckets]
        groups = []
        for index in missing:
            if groups and groups[-1][-1]+1 == index:
                groups[-1].append(index)
            else:
                groups.append([index])
        for group in groups:
            stamps = [dates[i] for i in group]
            issues = defaultdict(list)
            before = (date.fromisoformat(stamps[0])-timedelta(days=1)).isoformat()
            flows, _ = external_flows(calculation.journal, self.scope, calculation.observations,
                before, stamps[-1], self.target, calculation=calculation,
                security_cash=tuple(c for stamp in stamps for c in cash.get(stamp, ())),
                security_events=tuple(e for stamp in stamps for e in securities.get(stamp, ())),
                account_events=tuple(e for stamp in stamps for e in accounts.get(stamp, ())),
                include_account_start=False, dated_issues=issues)
            by_date = defaultdict(list)
            for flow in flows:
                by_date[flow.date].append(flow)
            for stamp in stamps:
                self.buckets[stamp] = tuple(by_date[stamp]), tuple(issues[stamp])
        opening = (), ()
        if opening_allowed and any(calculation._account_starts.get(account) == start for account in self.scope.accounts):
            if start not in self.starts:
                self.starts[start] = external_flows(calculation.journal, self.scope, calculation.observations,
                    start, start, self.target, calculation=calculation, security_cash=cash.get(start, ()),
                    security_events=securities.get(start, ()),
                    account_events=accounts.get(start, ()))
            opening = self.starts[start]
        chunks = [opening, *(self.buckets[stamp] for stamp in dates[lower:upper])]
        result = (tuple(flow for flows, _ in chunks for flow in flows),
                  tuple(sorted({reason for _, reasons in chunks for reason in reasons})))
        # Only the latest assembled interval is retained, never every cumulative
        # prefix. Converted flows/issues themselves live once per actual date.
        self.last = key, result
        return result

    def total(self, start, end, *, amount_needed):
        amount, reasons, _ = self.total_with_estimates(start, end, amount_needed=amount_needed)
        return amount, reasons

    def total_with_estimates(self, start, end, *, amount_needed):
        """Extend a net contribution total in posting order as the chart advances."""
        key = start, amount_needed
        previous = self.last_total
        if previous and previous[0] == key and start <= previous[1] <= end:
            _, prior_end, (amount, reasons, estimated) = previous
            if prior_end == end:
                return amount, reasons, estimated
            # The prior close already includes this date, even if an account
            # opened then. New postings must still be checked while net is unknown.
            flows, missing = self.interval(prior_end, end, include_account_start=False)
        else:
            amount = Decimal(0) if amount_needed else None
            reasons = ()
            estimated = False
            flows, missing = self.interval(start, end)
        if missing:
            amount = None
            reasons = tuple(sorted(set((*reasons, *missing))))
        elif amount is not None:
            amount = sum((flow.amount for flow in flows), amount)
        if self.calculation.has_estimated_inputs and not estimated:
            estimated = any(flow.estimated for flow in flows)
        self.last_total = key, end, (amount, reasons, estimated)
        return amount, reasons, estimated
