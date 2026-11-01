"""Lazy dated flow facts for one immutable calculation context."""
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta


class DatedFlows:
    def __init__(self, calculation, scope, target):
        self.calculation, self.scope, self.target = calculation, scope, target
        self.buckets = {}
        self.starts = {}
        self.last = None

    def interval(self, start, end):
        from portfolio_app.personal_performance import external_flows
        calculation = self.calculation
        dates = calculation._flow_dates
        lower, upper = bisect_right(dates, start), bisect_right(dates, end)
        key = start, upper
        if self.last is not None and self.last[0] == key:
            return self.last[1]
        cash, securities, accounts = calculation.flow_inputs()
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
        if start <= end and any(calculation._account_starts.get(account) == start for account in self.scope.accounts):
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
