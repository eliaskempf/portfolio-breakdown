"""Explicit-run funding studies and event inspection."""
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio_app.futures_funding import FundingPolicy, FundingStudy, trailing_volatility
from portfolio_app.futures_funding_data import load_funding_history, load_funding_settings, save_funding_settings
from portfolio_app.futures_funding_study import paired_window, run_funding_study, run_identity, short_horizon_moves
from portfolio_app.holdings import DataError

CHART_CONFIG = dict(displayModeBar=True, displaylogo=False,
                    modeBarButtons=[['zoom2d', 'pan2d', 'resetScale2d']], doubleClick='reset')


@st.cache_data(show_spinner=False, max_entries=3)
def cached_study(position, settings, study, history):
    return run_funding_study(position, settings, study, history)


def policy_label(policy):
    return f"{'Target' if policy.mode == 'target' else 'Loss buffer'} €{policy.amount:,.0f}" + (' · scaled' if policy.proportional else '')


def _rules(prefix, initial):
    a, b, c = st.columns(3)
    delay = a.number_input('Transfer delay (observations)', min_value=1, max_value=21,
                          value=initial.delay, key=prefix+'delay')
    frequency = b.selectbox('Cash reset', ['monthly', 'quarterly'],
                           index=int(initial.frequency == 'quarterly'), key=prefix+'frequency')
    early = c.checkbox('Early replenishment', value=initial.trigger is not None, key=prefix+'early')
    trigger = c.slider('Cushion remaining at request (%)', 0, 100,
                       value=round(100*(initial.trigger if initial.trigger is not None else .5)), key=prefix+'trigger') if early else None
    scaling = a.checkbox('Scale target / buffer with notional', value=initial.proportional, key=prefix+'scaling')
    return dict(delay=delay, frequency=frequency, trigger=trigger/100 if trigger is not None else None, proportional=scaling)


def funding_controls(directory, context):
    try:
        saved, revision = load_funding_settings(directory)
    except DataError as exc:
        st.error(str(exc)); return None, None
    study, source = saved if saved else (None, '')
    revision_key = f'futures_funding_revision_{context}'
    if revision_key not in st.session_state:
        st.session_state[revision_key] = revision
    key = f'futures_input_{context}_funding_'
    st.caption('Experimental reserve only · no deposits, transfers or study results are written into your portfolio.')
    left, right = st.columns(2)
    reserve = left.number_input('Total study reserve (€)', min_value=1., value=study.reserve if study else None,
                                step=1000., key=key+'reserve', placeholder='Enter broker + bank cash')
    baseline_amount = right.number_input('Baseline cash target or loss buffer (€)', min_value=0.,
        value=study.baseline.amount if study else None, step=1000., key=key+'baseline_amount', placeholder='Enter an explicit baseline')
    with st.expander('Baseline policy', expanded=False):
        st.caption('This policy stays fixed across candidate comparisons. Market, costs, rates and margin stress apply equally to both sides.')
        base = study.baseline if study else FundingPolicy(0.)
        mode = st.radio('Baseline target type', ['target', 'buffer'], format_func=lambda v: 'Broker cash target' if v == 'target' else 'Maintenance + loss buffer',
                         index=int(base.mode == 'buffer'), horizontal=True, key=key+'baseline_mode')
        baseline_rules = _rules(key+'baseline_', base)
    candidates = study.candidates if study else ()
    left, right = st.columns(2)
    targets = left.text_input('Candidate broker targets (€; comma-separated)',
        value=', '.join(str(p.amount) for p in candidates if p.mode == 'target'), key=key+'targets')
    buffers = right.text_input('Candidate loss buffers (€; comma-separated)',
        value=', '.join(str(p.amount) for p in candidates if p.mode == 'buffer'), key=key+'buffers')
    with st.expander('Candidate funding rules'):
        rules = _rules(key+'candidate_', candidates[0] if candidates else FundingPolicy(0.))
        st.caption('A loss-buffer policy adds its buffer to current maintenance. Initial-margin funding floors can increase actual cash usage. '
                   'Outgoing sweeps are immediate; incoming transfers arrive after the margin check and earn no interest in transit.')
    with st.expander('History, cohort & margin stress', expanded=not bool(source)):
        if not source:
            for name in ('funding-history.csv', 'futures-return-history.json'):
                if (Path(directory)/name).is_file():
                    source = str(Path(directory)/name); break
        source = st.text_input('History CSV, EUR return snapshot, or handoff directory', value=source, key=key+'source')
        st.caption('CSV columns: date, eur_index. Reads local data without changing it. Archived scripts are never executed.')
        a, b = st.columns(2)
        years = a.selectbox('Historical window (years)', [1, 5], index=int(bool(study and study.years == 5)), key=key+'years')
        step = b.selectbox('Start spacing (observations)', [1, 21], index=int(bool(study and study.step == 21)), key=key+'step')
        low_vol = a.checkbox('Low-volatility starts only', value=study.low_vol_only if study else False, key=key+'cohort')
        threshold = b.number_input('Opening volatility threshold (%)', min_value=.1, max_value=100.,
            value=100*study.vol_threshold if study else 15., disabled=not low_vol, key=key+'threshold')
        vol_margin = st.checkbox('Stress margins with trailing volatility', value=study.volatility_margin if study else False, key=key+'vol_margin')
        st.caption('Volatility stress uses 20 trailing simple returns × √260, with margin multiplier clipped to 1–3× relative to 15% volatility. '
                   'It multiplies the shared margin stress. Full warm-up is required. These are assumed requirements, not past broker margins.')
    if reserve is None or baseline_amount is None or not (targets.strip() or buffers.strip()):
        st.info('Enter a total reserve, one baseline amount and at least one candidate target or loss buffer.')
        return None, source
    try:
        policies = tuple(FundingPolicy(float(value.strip()), mode=kind, **rules)
            for kind, values in [('target', targets), ('buffer', buffers)] for value in values.split(',') if value.strip())
        study = FundingStudy(reserve, FundingPolicy(baseline_amount, mode=mode, **baseline_rules), policies,
                             years, step, low_vol, threshold/100, vol_margin)
    except (ValueError, TypeError) as exc:
        st.error(f'Check study inputs: {exc}'); return None, source
    if st.button('Save funding study settings', key=f'futures_funding_save_{context}'):
        try:
            save_funding_settings(directory, study, source, st.session_state[revision_key])
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            _, st.session_state[revision_key] = load_funding_settings(directory)
            st.success('Funding study settings saved. Portfolio cash and position unchanged.')
    return study, source


def render_funding(position, settings, provenance, directory, context, settings_area, *, stress=False):
    st.subheader('Stress & history' if stress else 'Funding policies')
    st.caption('Historical moves applied to the current integer-contract position, including quarterly rolls. '
               'All candidates have the same reserve and starting exposure as the fixed baseline.')
    if stress:
        with settings_area.expander('Funding study inputs'):
            study, source = funding_controls(directory, context)
    else:
        study, source = funding_controls(directory, context)
    history, error, identity = None, None, None
    if source:
        try:
            history = load_funding_history(source)
        except DataError as exc:
            error = str(exc)
    else:
        error = 'Choose a local historical price source to run the study.'
    if provenance.get('financing_error') or provenance.get('benchmark_error'):
        error = provenance.get('financing_error') or provenance.get('benchmark_error')
    if study and history:
        identity, _ = run_identity(position, settings, study, history.digest)
    if error:
        st.info(error)
    run_key = f'futures_funding_result_{context}'
    if st.button('Run funding study', type='primary', disabled=study is None or history is None or bool(error), key=f'futures_funding_run_{context}'):
        try:
            with st.spinner('Replaying paired historical windows…'):
                result = cached_study(position, settings, study, history)
            st.session_state[run_key] = (result, position, settings, study, history)
        except (DataError, ValueError, OverflowError) as exc:
            st.error(str(exc))
    saved_run = st.session_state.get(run_key)
    if not saved_run:
        return
    result, run_position, run_settings, run_study, run_history = saved_run
    if identity != result.metadata['identity'] or error:
        st.warning('Results are stale: inputs or source changed. Showing the previous run; run the study again to apply edits.')
    st.caption(f"Run {result.metadata['identity'][:10]} · {result.metadata['model']} · "
        f"{result.metadata['window_count']:,} overlapping windows · {run_study.years} year(s) · "
        f"{result.metadata['excluded_warmup']} excluded for warm-up, {result.metadata['excluded_cohort']} for opening volatility. "
        'Historical frequencies describe this cohort; they are not forecast liquidation probabilities.')
    st.caption(f"{run_history.source} · {result.metadata['source_start']} → {result.metadata['source_end']}")
    if stress:
        render_stress(result, run_position, run_settings, run_study, run_history, context)
    else:
        render_summary(result, run_study)
    with st.expander('Run assumptions & model'):
        st.caption(run_history.qualification)
        st.write(f'Bank yield {run_settings.savings_rate:.2%}; broker yield {run_settings.cash_rate:.2%}; financing {run_settings.financing_rate:.2%}. '
                 'Dated starting rates stay constant during replay, with ACT/360 cash accrual. No historical rate reconstruction is implied.')
        st.write('P&L and interest precede the strict-below-maintenance check; due transfers arrive afterwards. '
                 'Rolls close the old contract before receipts and entry into the next maturity. Full re-entry requires initial margin plus costs. '
                 'No new exposure opens at the endpoint; open exposure is closed and cash in transit remains wealth. '
                 'No external deposits, partial liquidation, intraday prices, debit interest, taxes or broker netting are modeled.')
        st.write('Calendar spacing is preserved on a translated timeline. Quarterly rolls use the preceding available observation, '
                 'without exchange-holiday or banking-calendar reconstruction. Existing Monte Carlo views retain permanent exits and no top-ups.')
        st.json(result.metadata)


def render_summary(result, study):
    frame = result.summary.copy()
    frame['Policy'] = [policy_label(study.candidates[int(i)]) for i in frame.candidate]
    risk = go.Figure()
    for column, label in [('liquidation_frequency', 'Any liquidation'), ('roll_failure_frequency', 'Cannot fund a roll'), ('shortfall_frequency', 'Reserve shortfall')]:
        risk.add_bar(x=frame.Policy, y=frame[column], name=label)
    risk.update_layout(barmode='group', yaxis_title='Paired historical windows (%)', yaxis_range=[0, 100], height=330)
    st.plotly_chart(risk, width='stretch', config=CHART_CONFIG)
    returns = go.Figure()
    for column, label in [('mean_delta', 'Mean difference'), ('median_delta', 'Median difference'), ('worst_delta', 'Worst difference'), ('lower_tail_mean', 'Worst 1% mean')]:
        returns.add_scatter(x=frame.Policy, y=frame[column], mode='lines+markers', name=label)
    returns.add_hline(y=0, line_color='#94a3b8')
    returns.update_layout(yaxis_title='Final wealth difference versus fixed baseline (€)', height=350)
    st.plotly_chart(returns, width='stretch', config=CHART_CONFIG)
    st.caption('Differences include the entire broker + bank + transit reserve. Tail values are historical observations, not future loss bounds. '
               'Each row uses its eligible paired windows; compare feasibility counts before ranking policies.')
    names = dict(paired_windows='Paired windows', infeasible_windows='Infeasible windows', opening_cash='Mean actual opening cash (€)',
        average_broker='Mean cash held at broker (€)', missed_intervals='Mean missed intervals', mean_delta='Mean difference (€)',
        worst_delta='Worst difference (€)', lower_tail_mean='Worst 1% mean (€)')
    st.dataframe(frame[['Policy', *names]].rename(columns=names), hide_index=True, width='stretch')
    attribution = go.Figure()
    for column, label, sign in [('delta_market_pnl', 'Market exposure', 1), ('delta_carry_pnl', 'Carry / basis', 1),
        ('delta_bank_interest', 'Bank interest', 1), ('delta_broker_interest', 'Broker interest', 1), ('delta_costs', 'Extra execution costs', -1)]:
        attribution.add_bar(x=frame.Policy, y=sign*frame[column], name=label)
    attribution.update_layout(barmode='relative', yaxis_title='Mean contribution to relative wealth (€)', height=330)
    st.plotly_chart(attribution, width='stretch', config=CHART_CONFIG)
    st.caption('Carry / basis is the exact residual between contract settlement P&L and unfinanced index movement; it is not an additional deduction.')


def render_stress(result, position, settings, study, history, context):
    key = f'futures_input_{context}_funding_detail_'
    candidate = st.selectbox('Funding policy to inspect', list(range(len(study.candidates))),
        format_func=lambda i: policy_label(study.candidates[i]), key=key+'candidate')
    mode = st.radio('Historical replay selection', ['Study window', 'Selected period / crisis'], horizontal=True, key=key+'mode')
    if mode == 'Study window':
        subset = result.windows.loc[result.windows.candidate.eq(candidate)].sort_values('delta_wealth', na_position='last')
        choices = list(range(len(subset)))
        selected = st.selectbox('Window (worst relative result first)', choices,
            format_func=lambda i: f"{subset.iloc[i].start:%Y-%m-%d} → {subset.iloc[i].end:%Y-%m-%d}", key=key+'window_'+result.metadata['identity'])
        row = subset.iloc[selected]
        prices = history.prices.loc[row.start:row.end]
    else:
        periods = {'Custom': None, 'Financial crisis': ('2007-10-31', '2009-06-30'),
                   'COVID': ('2020-02-19', '2020-06-30'), '2022': ('2021-12-31', '2022-12-30')}
        period = st.selectbox('Historical period', list(periods), key=key+'period')
        initial = periods[period] or (history.prices.index[0].date(), history.prices.index[-1].date())
        a, b = st.columns(2)
        start = a.date_input('Source start date', pd.Timestamp(initial[0]).date(), key=key+'start_'+period)
        end = b.date_input('Source end date', pd.Timestamp(initial[1]).date(), key=key+'end_'+period)
        if pd.Timestamp(start) < history.prices.index[0] or pd.Timestamp(end) > history.prices.index[-1] or end <= start:
            st.info('The selected period must fit the available history and end after its start.'); return
        prices = history.prices.loc[str(start):str(end)]
    try:
        rows, logs, baseline = paired_window(position, settings, study, prices, trailing_volatility(history.prices), record=True, candidate=candidate)
        moves = short_horizon_moves(history.prices, study.candidates[candidate].delay, short=position.contracts < 0)
    except (DataError, ValueError) as exc:
        st.warning(str(exc)); return
    row, ledger = rows[0], logs[candidate]
    if not row['paired_eligible']:
        st.warning(f"Infeasible opening reserve. Candidate requires €{row['opening_cash']:,.2f}; baseline requires €{row['baseline_opening_cash']:,.2f}."); return
    a, b, c = st.columns(3)
    a.metric('Actual opening broker cash', f"€{row['opening_cash']:,.2f}")
    b.metric('Final difference versus baseline', f"€{row['delta_wealth']:,.2f}")
    c.metric('Missed exposure intervals', row['missed_intervals'])
    st.caption(f"Historical source {row['start']:%Y-%m-%d} → {row['end']:%Y-%m-%d}; "
               f"translated simulation {row['simulation_start']:%Y-%m-%d} → {row['simulation_end']:%Y-%m-%d}.")
    fig = go.Figure()
    for column, label in [('broker', 'Broker cash'), ('bank', 'Bank cash'), ('transit', 'In transit'), ('maintenance', 'Maintenance requirement'), ('headroom', 'Loss headroom')]:
        fig.add_scatter(x=ledger.date, y=ledger[column], name=label, mode='lines')
    for column, label, symbol in [('liquidation', 'Liquidation', 'x'), ('reentry', 'Re-entry', 'triangle-up')]:
        events = ledger.loc[ledger[column]]
        fig.add_scatter(x=events.date, y=events.broker, mode='markers', name=label, marker=dict(symbol=symbol, size=10))
    fig.update_layout(yaxis_title='Cash / requirement (€)', xaxis_title='Simulation date', height=370)
    st.plotly_chart(fig, width='stretch', config=CHART_CONFIG)
    relative = go.Figure()
    relative.add_scatter(x=ledger.date, y=ledger.wealth-baseline.wealth, name='Wealth difference', mode='lines')
    relative.add_hline(y=0)
    relative.update_layout(yaxis_title='Wealth versus baseline (€)', xaxis_title='Simulation date', height=280)
    st.plotly_chart(relative, width='stretch', config=CHART_CONFIG)
    st.caption(f"Across all raw proxy windows of {moves['observations']} observations, worst running adverse move: {moves['worst_running']:.2%}; "
        f"1st percentile: {moves['p01_running']:.2%}. {'Rises' if position.contracts < 0 else 'Falls'} are adverse. "
        'This separate raw-move summary excludes margin, financing and transfers; it is not a cash requirement.')
    with st.expander('Event ledger · candidate and baseline'):
        st.dataframe(ledger, hide_index=True, width='stretch')
        st.dataframe(baseline, hide_index=True, width='stretch')
