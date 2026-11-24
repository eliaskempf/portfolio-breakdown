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
    days = ({day for day in recorded.chart_dates if beginning <= day <= last}
            | {flow.date for flow in flows} | {day for day in dates if beginning <= day <= last}
            | {beginning, last})
    extra = first if first not in days else None
    days.add(first)
    return sorted(days), extra


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


@dataclass(frozen=True)
class _ValidatedDates:
    last_supported: str
    supported_count: int
    negative_dates: tuple[str, ...]


def _all_history(calculation, scope, target, dates, recorded, beginning, last, stamps,
                 *, annualized, coverage, valuations=None, extra=None, retain_for_chart=False):
    def value(day):
        return valuations[day] if valuations is not None else calculation.value(scope, day, target)

    new_coverage = None
    needs_scan = _requires_full_valuation_scan(scope, recorded)
    if needs_scan and coverage is None and valuations is None:
        valuations = {day: calculation.value(scope, day, target) for day in stamps}
    if coverage is not None:
        negative = coverage.negative_dates
    elif valuations is not None:
        negative = calculation._get(('negative_equity_dates', scope, beginning, last, target), lambda:
            known_negative_equity_dates(calculation.journal, scope, calculation.observations, beginning, last,
                                        target, valuations=valuations, calculation=calculation))
        if retain_for_chart and needs_scan:
            # A window boundary without a recorded close is specific to this range.
            supported = [day for day in stamps if day != extra and value(day).amount is not None]
            new_coverage = coverage = _ValidatedDates(supported[-1] if supported else '', len(supported), negative)
    else:
        # Long-only securities with positive prices need only explicit negative scope marks.
        negative = tuple(sorted(day for day in recorded.negative_equity_dates if beginning <= day <= last))
    if coverage is None:
        end = next((day for day in reversed(stamps) if value(day).amount is not None), last)
        useful = None
    else:
        supported_extra = extra if extra and value(extra).amount is not None else ''
        end = max(coverage.last_supported, supported_extra) or last
        useful = coverage.supported_count + bool(supported_extra) >= 2
    result = _period_result(calculation, scope, target, dates, beginning, end, negative, annualized)
    if new_coverage is not None:
        calculation._validated_history_dates = {(scope, target, beginning, last): new_coverage}
    return end, negative, result, useful


def _full_interval(calculation, scope, today, target, *, annualized):
    dates = calculation.event_dates(scope)
    last = today.isoformat()
    beginning = dates[0] if dates else last
    recorded = calculation.observation_dates(scope, target)
    stamps, _ = _valuation_stamps(calculation, scope, target, dates, recorded, beginning, beginning, last)
    end, _, result, _ = _all_history(calculation, scope, target, dates, recorded, beginning, last, stamps,
        annualized=annualized, coverage=calculation._validated_history_dates.get((scope, target, beginning, last)))
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
    journal, observations = calculation.journal, calculation.observations
    today = today or date.today()
    dates = calculation.event_dates(scope)
    earliest = date.fromisoformat(min(dates)) if dates else today
    first = period_start(period,today,earliest).isoformat()
    last = today.isoformat()
    recorded = calculation.observation_dates(scope, target)
    beginning = earliest.isoformat()
    stamps, extra = _valuation_stamps(calculation, scope, target, dates, recorded, beginning, first, last)
    coverage = calculation._validated_history_dates.get((scope, target, beginning, last))
    full_scan = first <= beginning or (_requires_full_valuation_scan(scope, recorded) and coverage is None)
    valuations = {stamp: calculation.value(scope, stamp, target) for stamp in stamps if full_scan or stamp >= first}
    values = {stamp: valuation.amount for stamp, valuation in valuations.items()}
    available = [stamp for stamp, value in values.items() if value is not None]
    all_start = beginning
    all_end, negative_dates, all_result, validated_useful = _all_history(calculation, scope, target, dates, recorded,
        beginning, last, stamps, annualized=annualized, coverage=coverage,
        valuations=valuations if full_scan or coverage is not None else None, extra=extra, retain_for_chart=include_points)
    useful = len(available) >= 2 if validated_useful is None else validated_useful
    if validated_useful is None and not useful and not full_scan and all_result.ending_value is not None:
        # The last supported endpoint is known; an earlier close can establish
        # chart availability even when the visible window is entirely empty.
        useful = any(calculation.value(scope, day, target).amount is not None
                     for day in reversed(stamps) if day < min(first, all_end))
    def known_gain(day):
        if day in valuations:
            calculation.retain_value(scope, day, target, valuations[day])
        return summarize_known_gain(journal, scope, observations, day, target, calculation=calculation)

    def gain_point(day):
        known = known_gain(day)
        return known.capital, known.amount

    all_known = known_gain(all_end)
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
            if stamp == all_end:
                invested, gain = all_known.capital, all_known.amount
            else:
                invested, gain = calculation._get((scope, stamp, target), lambda: gain_point(stamp),
                                                  cache=calculation._chart_gains)
        points.append({'Date':stamp, 'Value':float(values[stamp]) if values[stamp] is not None else None,
            'Invested capital':float(invested) if invested is not None else None,
            'Total gain':float(gain) if gain is not None else None,
            'Price as of': ', '.join(sorted({e.as_of for e in valuations[stamp].evidence if e.kind == 'price' and e.as_of != stamp})),
            'FX as of': ', '.join(sorted({e.as_of for e in valuations[stamp].evidence if e.kind == 'fx' and e.as_of != stamp})),
            'TWR':returns.get(stamp)})
    # Rendering may join neighbors; missing observations stay missing in math.
    gaps = tuple((stamp,valuations[stamp].reasons) for stamp in stamps if start <= stamp <= end and values[stamp] is None)
    return HistoryView(first,last,start,end,tuple(points),result,all_start,all_end,all_result,useful,all_known,gaps)
