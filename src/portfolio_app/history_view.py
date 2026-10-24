"""Period selection and observed history series, independent of Streamlit."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date

from portfolio_app.history_observations import active_observations, index_observations
from portfolio_app.personal_performance import PersonalPerformance, HistoryCalculation, calculate_history, known_negative_equity_dates
from portfolio_app.security_history import selected_events
from portfolio_app.gain_accounting import GainSummary, scope_dates, summarize_known_gain

PERIODS = ('1M', '3M', '6M', 'YTD', '1Y', '3Y', '5Y', 'All')


def period_start(period, today, earliest):
    if period == 'All':
        return earliest
    if period == 'YTD':
        return max(earliest, date(today.year, 1, 1))
    months = {'1M':1, '3M':3, '6M':6, '1Y':12, '3Y':36, '5Y':60}[period]
    number = today.year*12+today.month-1-months
    year, month = divmod(number,12)
    month += 1
    return max(earliest, date(year,month,min(today.day,monthrange(year,month)[1])))


@dataclass(frozen=True)
class HistoryView:
    requested_start: str
    today: str
    start: str
    end: str
    points: tuple[dict, ...]
    result: PersonalPerformance
    all_start: str
    all_end: str
    all_result: PersonalPerformance
    useful: bool
    all_known: GainSummary
    missing: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def covers_full_history(self):
        return self.requested_start <= self.all_start


def history_view(journal, scope, observations, period='1Y', *, today=None, target='EUR', include_points=True, calculation=None):
    calculation = calculation or HistoryCalculation(journal, index_observations(observations))
    observations = calculation.observations
    today = today or date.today()
    dates = scope_dates(journal, scope)
    earliest = date.fromisoformat(min(dates)) if dates else today
    first = period_start(period,today,earliest).isoformat()
    last = today.isoformat()
    subjects = set(scope.positions) | {b.instrument_id for b in journal.securities if b.position_id in scope.positions}
    subjects |= {p.id for p in journal.positions if p.account_id in scope.accounts}
    subjects |= {p.contract_id for p in journal.positions if p.account_id in scope.accounts}
    relevant = [o for o in active_observations(observations) if
        (o.kind == 'price' and o.subject in subjects) or o.kind == 'fx'
        or (o.kind == 'value' and o.subject == scope.key and o.side == 'close')]
    beginning = earliest.isoformat()
    flows, _ = calculation.flows(scope, beginning, last, target)
    stamps = sorted({o.date for o in relevant if beginning <= o.date <= last} | {f.date for f in flows}
                    | {d for d in dates if beginning <= d <= last} | {beginning,first,last})
    valuations = {stamp:calculation.value(scope,stamp,target) for stamp in stamps}
    values = {stamp:valuation.amount for stamp,valuation in valuations.items()}
    available = [stamp for stamp in stamps if values[stamp] is not None]
    all_start,all_end = beginning, (available[-1] if available else last)
    negative_dates = known_negative_equity_dates(journal,scope,observations,all_start,last,target,valuations=valuations)
    all_result = calculate_history(journal,scope,observations,all_start,all_end,target,negative_dates=negative_dates,calculation=calculation)
    # A later transaction cannot disappear merely because its valuation is absent.
    if dates and dates[-1] > all_end:
        from dataclasses import replace
        reason = ('The latest recorded event has no supported ending valuation',)
        all_result = replace(all_result, money_weighted=None, time_weighted=None,
                             money_weighted_reasons=reason, time_weighted_reasons=reason)
    all_known = summarize_known_gain(journal,scope,observations,all_end,target,calculation=calculation)
    window = [stamp for stamp in available if first <= stamp <= last]
    start,end = (window[0],window[-1]) if window else (first,last)
    if first <= all_start:
        start, end = all_start, all_end
    result = (all_result if (start,end) == (all_start,all_end) else
              calculate_history(journal,scope,observations,start,end,target,negative_dates=negative_dates,calculation=calculation))
    # Capital and hover gains keep the same full-history basis as the user zooms.
    # Period gain remains a separate endpoint/flow calculation. Never subtract
    # a neighboring day's capital from a hovered value at a missing observation.
    flows, missing = calculation.flows(scope,all_start,all_end,target)
    points = []
    for stamp in stamps if include_points else ():
        if not first <= stamp <= all_end:
            continue
        invested = (all_result.opening_capital + sum(f.amount for f in flows if f.date <= stamp)
                    if stamp >= all_start and all_result.opening_capital is not None
                    and all_result.net_contributions is not None and not missing else None)
        gain = values[stamp] - invested if values[stamp] is not None and invested is not None else None
        if invested is None:
            known = summarize_known_gain(journal,scope,observations,stamp,target,calculation=calculation)
            invested, gain = known.capital, known.amount
        inspected = calculate_history(journal,scope,observations,all_start,stamp,target,annualized=False,negative_dates=negative_dates,calculation=calculation)
        points.append({'Date':stamp, 'Value':float(values[stamp]) if values[stamp] is not None else None,
            'Invested capital':float(invested) if invested is not None else None,
            'Total gain':float(gain) if gain is not None else None,
            'TWR':inspected.time_weighted})
    # Rendering may join neighbors; missing observations stay missing in math.
    gaps = tuple((stamp,valuations[stamp].reasons) for stamp in stamps if start <= stamp <= end and values[stamp] is None)
    return HistoryView(first,last,start,end,tuple(points),result,all_start,all_end,all_result,len(available) >= 2,all_known,gaps)
