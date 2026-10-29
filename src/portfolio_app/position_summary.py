"""Stable personal-position headlines, independent of the selected chart."""
from datetime import date

import streamlit as st

from portfolio_app.currency_display import money, reporting_currency
from portfolio_app.presentation import return_percent


def render_position_summary(row, directory, journal, *, has_history, demo=False):
    from portfolio_app.history_ui import prepared_history_view, prepared_history_calculation
    from portfolio_app.history_observations import HistoryScope, load_observations
    from portfolio_app.personal_history_refresh import refresher
    scope = HistoryScope((row.position_id,))
    target = reporting_currency()
    if has_history and not demo:
        refresher.request(directory, journal, scope, target)
    cached, signature = None, None

    # A retry can begin inside the chart after this region has mounted. Keep
    # watching evidence revisions so completion also updates headlines in Price.
    @st.fragment(run_every=2 if has_history and not demo else None)
    def summary():
        nonlocal cached, signature
        # The row is the current valuation; the chart never supplies a different
        # position value just because the user switches mode, range or hover.
        value = row.get('current_value_reporting')
        gain = row.get('unrealized_gain_reporting')
        capital = row.get('cost_basis_reporting')
        annualized = None
        estimated_return = False
        gain_note = 'Unrealized gain only'
        gain_help = 'Current value less recorded acquisition cost. Earlier realized gains, income and expenses are not reconstructed.'
        capital_help = 'Recorded acquisition cost of the current holding; earlier purchases and proceeds are not reconstructed.'
        return_help = 'Requires applied transaction history, dated valuations and confirmed completeness. Market-price returns are not personal returns.'
        if has_history:
            from portfolio_app.gain_accounting import summarize_known_gain, amount
            from portfolio_app.history_data_ui import readable_reasons
            from portfolio_app.input_cache import file_stamp
            current = file_stamp(directory/'history-observations.json'), date.today()
            if current != signature:
                observations, revision = load_observations(directory)
                calculation = prepared_history_calculation(directory, journal, observations, revision, target=target)
                view = prepared_history_view(directory, journal, scope, observations, revision, 'All',
                    target=target, include_points=False, calculation=calculation)
                known = summarize_known_gain(journal, scope, observations, date.today().isoformat(), target,
                    current_values={row.position_id: amount(value)}, summary_rows=row.to_frame().T, calculation=calculation)
                cached, signature = (view, known), current
            view, known = cached
            gain, capital = known.amount, known.capital
            gain_note = known.coverage_note if known.partial else ''
            gain_help = known.coverage_help
            capital_help = ('Known opening acquisition cost plus recorded net contributions through today. '
                            'Includes sale proceeds, income and expenses; can be negative after withdrawals. '
                            'This is not remaining acquisition cost. Unchanged by chart type, range or hover.')
            full = view.all_result
            estimated_return = full.estimated
            annualized = None if full.money_weighted is None else 100*full.money_weighted
            return_help = f'All history · {view.all_start} – {view.all_end}. Money-weighted; unchanged by chart type, range or hover.'
            if full.days < 365:
                return_help += f' Annualized from a short interval of {full.days} days.'
            if annualized is None:
                reasons = readable_reasons(directory, journal, scope, full.money_weighted_reasons)
                return_help += ' ' + ' '.join(reasons[:3])
        with st.container(key='position_summary'):
            first, second, third = st.columns([2, 1, 1])
            with first.container(key='position_summary_value'):
                st.metric('Position value', money(value), delta=money(gain, signed=True),
                    delta_color='normal' if gain is not None and gain == gain and gain != 0 else 'off',
                    delta_arrow='off', help='Current position value. Gain uses the same personal accounting basis in both history modes. '+gain_help)
            if gain_note:
                first.caption(gain_note, help=gain_help)
            second.metric('Invested capital', money(capital), help=capital_help)
            if not has_history and row.get('cost_estimated'):
                second.caption('Estimated cost', help=row.get('cost_note') or 'Acquisition cost uses an estimated conversion.')
            third.metric('Annualized return', return_percent(annualized), help=return_help)
            if estimated_return and annualized is not None:
                third.caption('Estimated', help='Uses supplied estimates in the dated transaction inputs.')
    summary()
