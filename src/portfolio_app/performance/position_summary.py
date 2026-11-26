"""Stable personal-position headlines, independent of the selected chart."""
from datetime import date

import streamlit as st

from portfolio_app.interface.shared.currency_display import money, reporting_currency
from portfolio_app.interface.shared.number_format import return_metric


def render_position_summary(row, directory, journal, *, has_history, input_revisions=None, demo=False, is_active=None):
    from portfolio_app.interface.views.history.history_ui import prepared_history_view, prepared_history_calculation
    from portfolio_app.performance.history_observations import FILE, HistoryScope
    from portfolio_app.interface.shared.input_cache import file_stamp, observation_snapshot
    from portfolio_app.performance.personal_history_refresh import refresher
    from portfolio_app.interface.shared.dialog_runtime import local_dialog_updates
    scope = HistoryScope((row.position_id,))
    target = reporting_currency()
    journal_revision = input_revisions[1] if input_revisions else None
    if has_history and not demo:
        from portfolio_app.interface.views.history.history_ui import prepared_history_fingerprint
        refresher.request(directory, journal, scope, target, input_revisions=input_revisions,
                          fingerprint=prepared_history_fingerprint(directory, journal, journal_revision))

    from portfolio_app.performance.performance import is_cash_balance
    balance_only = not has_history and is_cash_balance(row)

    @st.fragment
    @local_dialog_updates()
    def summary():
        if is_active is not None and not is_active():
            return
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
        return_help = 'Requires applied transaction history and dated valuations. Market-price returns are not personal returns.'
        if balance_only:
            gain_note = ''
            gain_help = row.get('performance_note') or 'Cash balance without an acquisition basis.'
            return_help = 'A cash balance alone cannot establish a money-weighted return.'
        if has_history:
            from portfolio_app.performance.gain_accounting import summarize_known_gain, amount
            from portfolio_app.interface.views.history.history_data_ui import brief_reasons, readable_reasons
            stamp, today = file_stamp(directory / FILE), date.today()
            observations, revision = observation_snapshot(directory)
            calculation = prepared_history_calculation(directory, journal, observations, revision, target=target,
                                                       journal_revision=journal_revision)
            view = prepared_history_view(directory, journal, scope, observations, revision, 'All',
                target=target, include_points=False, calculation=calculation)
            known = summarize_known_gain(journal, scope, observations, today.isoformat(), target,
                current_values={row.position_id: amount(value)}, summary_rows=row.to_frame().T, calculation=calculation)
            gain, capital = known.amount, known.capital
            gain_note = known.coverage_note if known.partial and known.coverage_note != 'Estimated gain' else ''
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
            if estimated_return:
                return_help += ' Estimated return: uses supplied estimates in dated transaction inputs.'
            if annualized is None:
                reasons = readable_reasons(directory, journal, scope, full.money_weighted_reasons)
                return_help += ' ' + brief_reasons(reasons)
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
            third.metric('Annualized return', return_metric(annualized, cash_balance=balance_only), help=return_help)
            if has_history and annualized is None:
                from portfolio_app.interface.views.history.history_data_ui import render_return_inputs
                with third:
                    render_return_inputs(directory, journal, scope, observations, revision,
                                         full.money_weighted_reasons, 'position_edit_return_'+row.position_id+'_', indexed=calculation.observations)

        if has_history and not demo:
            _watch_summary_evidence(directory, stamp, revision, today, is_active)
    summary()


def _watch_summary_evidence(directory, stamp, revision, today, is_active):
    from portfolio_app.interface.shared.background_ui import status_fragment
    from portfolio_app.interface.shared.dialog_runtime import local_fragment_updates
    from portfolio_app.interface.shared.input_cache import file_stamp, observation_snapshot
    from portfolio_app.performance.history_observations import FILE

    # A retry or an external correction can finish while Price is open.
    # Keeping the watcher separate lets unchanged polls leave all three cards in place.
    @status_fragment(run_every=2)
    @local_fragment_updates()
    def watch(poll):
        nonlocal stamp
        if is_active is not None and not is_active():
            poll(None)
            return
        if date.today() != today:
            st.rerun()
        current = file_stamp(directory / FILE)
        if current != stamp:
            if observation_snapshot(directory)[1] != revision:
                st.rerun()
            stamp = current
    watch()
