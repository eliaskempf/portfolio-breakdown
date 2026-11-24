"""Period selection and observed history series, independent of Streamlit."""
from calendar import monthrange
from portfolio_app.performance.history_diagnostics import HistoryIssue
from dataclasses import dataclass, replace
from datetime import date

from portfolio_app.performance.history_observations import index_observations
from portfolio_app.performance.personal_performance import PersonalPerformance, HistoryCalculation, calculate_history, known_negative_equity_dates
from portfolio_app.performance.history_twr import chart_returns
from portfolio_app.performance.gain_accounting import GainSummary, summarize_known_gain

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


def _valuation_stamps(calculation, scope, target, dates, recorded, beginning, first, last):
    flows, _ = calculation.flows(scope, beginning, last, target)
    return sorted({day for day in recorded.chart_dates if beginning <= day <= last}
                  | {flow.date for flow in flows} | {day for day in dates if beginning <= day <= last}
                  | {beginning, first, last})


def _period_result(calculation, scope, target, dates, start, end, negative_dates, annualized):
    result = calculate_history(calculation.journal, scope, calculation.observations, start, end, target,
                               calculation=calculation, annualized=annualized, negative_dates=negative_dates)
    if dates and dates[-1] > end:
        reason = (HistoryIssue('The latest recorded event has no supported ending valuation', 'endpoint'),)
        return replace(result, money_weighted=None, time_weighted=None,
                       money_weighted_reasons=reason, time_weighted_reasons=reason)
    return result


def _requires_full_valuation_scan(scope, recorded):
    # Accounts can turn negative between flows. Reciprocal FX quotes can
    # conflict on an intermediate closing date, including outside a chart window.
    return bool(scope.accounts) or recorded.requires_fx_validation


def _all_history(calculation, scope, target, dates, recorded, beginning, last, stamps,
                 *, annualized, valuations=None):
    if valuations is None and _requires_full_valuation_scan(scope, recorded):
        valuations = {day: calculation.value(scope, day, target) for day in stamps}

    def value(day):
        return valuations[day] if valuations is not None else calculation.value(scope, day, target)

    end = next((day for day in reversed(stamps) if value(day).amount is not None), last)
    if valuations is None:
        # FIFO inventory is long-only and validated prices are positive. Only
        # explicit scope valuations (including flow boundaries) can be negative.
        negative = tuple(sorted(day for day in recorded.negative_equity_dates if beginning <= day <= last))
    else:
        negative = calculation._get(('negative_equity_dates', scope, beginning, last, target), lambda:
            known_negative_equity_dates(calculation.journal, scope, calculation.observations, beginning, last,
                                        target, valuations=valuations, calculation=calculation))
    return end, negative, _period_result(calculation, scope, target, dates, beginning, end, negative, annualized)


def _full_interval(calculation, scope, today, target, *, annualized):
    dates = calculation.event_dates(scope)
    last = today.isoformat()
    beginning = dates[0] if dates else last
    recorded = calculation.observation_dates(scope, target)
    stamps = _valuation_stamps(calculation, scope, target, dates, recorded, beginning, beginning, last)
    end, _, result = _all_history(calculation, scope, target, dates, recorded, beginning, last, stamps,
                                  annualized=annualized)
    return beginning, end, result, stamps


def full_history_time_weighted(calculation, scope, today, target):
    """The All-period return for consumers that do not display a dated series."""
    _, _, result, _ = _full_interval(calculation, scope, today, target, annualized=False)
    return result.time_weighted


@dataclass(frozen=True)
class HistorySummary:
    all_start: str
    all_end: str
    all_result: PersonalPerformance
    useful: bool


def full_history_summary(calculation, scope, today, target):
    """Return the checklist metrics and whether two distinct dates have supported valuations."""
    start, end, result, stamps = _full_interval(calculation, scope, today, target, annualized=True)
    useful = result.ending_value is not None and any(
        calculation.value(scope, day, target).amount is not None for day in reversed(stamps) if day < end)
    return HistorySummary(start, end, result, useful)


def history_view(journal, scope, observations, period='1Y', *, today=None, target='EUR', include_points=True, calculation=None, annualized=True, period_annualized=True):
    calculation = calculation or HistoryCalculation(journal, index_observations(observations))
    observations = calculation.observations
    today = today or date.today()
    dates = calculation.event_dates(scope)
    earliest = date.fromisoformat(min(dates)) if dates else today
    first = period_start(period,today,earliest).isoformat()
    last = today.isoformat()
    recorded = calculation.observation_dates(scope, target)
    beginning = earliest.isoformat()
    stamps = _valuation_stamps(calculation, scope, target, dates, recorded, beginning, first, last)
    full_scan = first <= beginning or _requires_full_valuation_scan(scope, recorded)
    valuations = {stamp: calculation.value(scope, stamp, target) for stamp in stamps if full_scan or stamp >= first}
    values = {stamp: valuation.amount for stamp, valuation in valuations.items()}
    available = [stamp for stamp, value in values.items() if value is not None]
    all_start = beginning
    all_end, negative_dates, all_result = _all_history(calculation, scope, target, dates, recorded,
        beginning, last, stamps, annualized=annualized, valuations=valuations if full_scan else None)
    useful = len(available) >= 2
    if not useful and not full_scan and all_result.ending_value is not None:
        # The last supported endpoint is known; an earlier close can establish
        # chart availability even when the visible window is entirely empty.
        useful = any(calculation.value(scope, day, target).amount is not None
                     for day in reversed(stamps) if day < min(first, all_end))
    all_known = summarize_known_gain(journal,scope,observations,all_end,target,calculation=calculation)
    window = [stamp for stamp in available if first <= stamp <= last]
    start,end = (window[0],window[-1]) if window else (first,last)
    if first <= all_start:
        start, end = all_start, all_end
    result = (all_result if (start,end) == (all_start,all_end) else
              _period_result(calculation, scope, target, dates, start, end, negative_dates, annualized and period_annualized))
    # Capital and hover gains keep the same full-history basis as the user zooms.
    # Period gain remains a separate endpoint/flow calculation. Never subtract
    # a neighboring day's capital from a hovered value at a missing observation.
    flows, missing = calculation.flows(scope,all_start,all_end,target)
    returns = (chart_returns(calculation, scope, all_start, target,
        {day: values[day] for day in window}, all_result, negative_dates) if include_points else {})
    points = []
    flow_index, contributions = 0, 0
    for stamp in stamps if include_points else ():
        if not first <= stamp <= all_end:
            continue
        while flow_index < len(flows) and flows[flow_index].date <= stamp:
            if flows[flow_index].amount is not None:
                contributions += flows[flow_index].amount
            flow_index += 1
        invested = (all_result.opening_capital + contributions
                    if stamp >= all_start and all_result.opening_capital is not None
                    and all_result.net_contributions is not None and not missing else None)
        gain = values[stamp] - invested if values[stamp] is not None and invested is not None else None
        if invested is None:
            known = summarize_known_gain(journal,scope,observations,stamp,target,calculation=calculation)
            invested, gain = known.capital, known.amount
        points.append({'Date':stamp, 'Value':float(values[stamp]) if values[stamp] is not None else None,
            'Invested capital':float(invested) if invested is not None else None,
            'Total gain':float(gain) if gain is not None else None,
            'Price as of': ', '.join(sorted({e.as_of for e in valuations[stamp].evidence if e.kind == 'price' and e.as_of != stamp})),
            'FX as of': ', '.join(sorted({e.as_of for e in valuations[stamp].evidence if e.kind == 'fx' and e.as_of != stamp})),
            'TWR':returns.get(stamp)})
    # Rendering may join neighbors; missing observations stay missing in math.
    gaps = tuple((stamp,valuations[stamp].reasons) for stamp in stamps if start <= stamp <= end and values[stamp] is None)
    return HistoryView(first,last,start,end,tuple(points),result,all_start,all_end,all_result,useful,all_known,gaps)
