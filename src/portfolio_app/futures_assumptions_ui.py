"""One live, persisted set of financial assumptions for all futures views."""
from datetime import date
from dataclasses import replace
import math

import streamlit as st

from portfolio_app.futures_comparison import ComparisonSettings, financing_from_estr
from portfolio_app.futures_returns import load_return_estimate, refresh_return_history
from portfolio_app.futures_market import VOL_FILE, load_observation, refresh_volatility
from portfolio_app.futures_reference import estr_reference_for_start
from portfolio_app.futures_workspace import load_comparison, save_comparison
from portfolio_app.holdings import DataError


def render_shared_assumptions(position, directory, context, reference, settings_area):
    try:
        saved, revision = load_comparison(directory, savings_rate=reference['estr_rate'] if reference else .03)
    except DataError as exc:
        st.error(str(exc))
        return None, None
    draft_key = f'futures_shared_assumptions_{context}'
    draft = st.session_state.get(draft_key)
    if draft and draft[0] == revision:
        saved = draft[1]
        if len(draft) > 2 and draft[2] != (position.quote_date, position.contract_expiry):
            saved = replace(saved, start_date=position.quote_date, until_expiry=bool(position.contract_expiry))
    key = f'futures_input_{context}_comparison_{revision}_{position.quote_date}_{position.contract_expiry}_'
    pending = st.session_state.pop(f'futures_apply_estr_{context}', None)
    if pending is not None:
        st.session_state[key+'savings'] = pending
        st.session_state[key+'benchmark_source'] = 'ecb'
    pending_vol = st.session_state.pop(f'futures_apply_vol_{context}', None)
    if pending_vol is not None:
        st.session_state[key+'vol'] = pending_vol
    try:
        historical = load_observation(directory, VOL_FILE)
    except DataError as exc:
        st.warning(str(exc))
        historical = None
    capital = st.number_input('Starting cash (€)', min_value=1., max_value=1e9, value=saved.comparison_capital, step=1000., key=key+'capital')
    with settings_area:
        strategy_area = st.container()
    mode = strategy_area.radio('Holding strategy', ['Single-contract / custom horizon', 'Hold & roll quarterly'],
                    index=int(saved.rolling), horizontal=True, key=key+'strategy')
    rolling = mode == 'Hold & roll quarterly'
    rolling_years = strategy_area.number_input('Rolling holding period (years)', min_value=1, max_value=30,
                                   value=saved.rolling_years, key=key+'years') if rolling else saved.rolling_years
    with settings_area.expander('Shared futures assumptions'):
        a, b = st.columns(2)
        start = a.date_input('Simulation start date',
            value=date.fromisoformat(saved.start_date or position.quote_date or date.today().isoformat()), key=key+'start')
        reference_error = None
        try:
            dated_reference = estr_reference_for_start(directory, reference, start)
        except DataError as exc:
            dated_reference = None
            reference_error = str(exc)
        until_expiry = b.checkbox('End at contract last trading day', value=saved.until_expiry and bool(position.contract_expiry),
                                 disabled=rolling or not position.contract_expiry, key=key+'expiry')
        a, b, c = st.columns(3)
        minimum = a.number_input('Minimum collateral (€)', min_value=1., value=saved.capital_min, step=1000., key=key+'min')
        maximum = b.number_input('Maximum collateral (€)', min_value=1., value=saved.capital_max, step=1000., key=key+'max')
        months = c.number_input('Holding period (months)', min_value=1, max_value=60, value=saved.months,
                                disabled=rolling or until_expiry, key=key+'months')
        a, b, c = st.columns(3)
        benchmark_source = c.radio('Cash benchmark source', ['ecb', 'manual'],
            format_func=lambda value: 'ECB €STR' if value == 'ecb' else 'Manual rate',
            index=0 if saved.benchmark_source == 'ecb' else 1, key=key+'benchmark_source', horizontal=True)
        savings = a.number_input('Cash benchmark rate (% per year)', min_value=-10., max_value=20., value=100*(dated_reference['estr_rate'] if benchmark_source == 'ecb' and dated_reference else saved.savings_rate),
                                  step=.01, format='%.3f', disabled=benchmark_source == 'ecb',
                                  key=key+('savings' if benchmark_source == 'manual' else 'savings_ecb_'+str(dated_reference['estr_reference_date'] if dated_reference else 'missing')))
        benchmark_error = None
        if benchmark_source == 'ecb':
            if dated_reference:
                savings = 100*dated_reference['estr_rate']
                a.caption(f'Using {savings:.3f}% · ECB {dated_reference["estr_reference_date"]}')
            else:
                benchmark_error = reference_error or 'No ECB snapshot or saved history on or before the simulation start. Load historical comparison data or select a manual cash benchmark.'
        cash = b.number_input('Futures cash yield (% per year)', min_value=-10., max_value=20., value=100*saved.cash_rate,
                               step=.01, format='%.3f', key=key+'cash')
        fees = 0.  # Legacy aggregate fees are no longer a separate assumption.
        if reference:
            st.caption(f"ECB €STR snapshot: {reference['estr_rate']:.3%}, reference date {reference['estr_reference_date']}. "
                       'Select Manual rate to override the cash benchmark, e.g. a money-market rate net of costs.')
            if st.button('Use saved ECB €STR rate'):
                # Apply on the next run before the widget is constructed.
                st.session_state[f'futures_apply_estr_{context}'] = reference['estr_rate']*100
                st.rerun()
        else:
            st.caption('3% is a hypothetical fallback. Load ECB / IBKR reference data above to use published inputs.')
        st.caption('Both rates use a constant overnight ACT/360 approximation. This is a gross cash benchmark, not a bank offer '
                   'or an investable guarantee. Futures cash yield defaults to 0%; it is not inferred from €STR or IBKR deposit rates.')
        a, b = st.columns(2)
        volatility = a.number_input('Annual futures volatility (%)', min_value=0., max_value=100., value=100*saved.volatility, step=1., key=key+'vol')
        stress = b.number_input('Margin requirement multiplier', min_value=1., max_value=3., value=saved.margin_stress, step=.1, key=key+'stress')
        if st.button('Refresh historical volatility proxy'):
            try:
                with st.spinner('Loading adjusted EUR MSCI World ETF history…'):
                    refresh_volatility(directory, start.isoformat())
            except (OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.rerun()
        if historical:
            st.caption(f"EUR MSCI World proxy: {historical['ticker']} adjusted daily closes via Yahoo; "
                       f"{historical['start_date']} to {historical['end_date']}; {historical['observations']} returns; "
                       f"annualised volatility {historical['volatility']:.2%}. Retrieved {historical['fetched_at']}.")
            applicable = date.fromisoformat(historical['end_date']) <= start
            if st.button('Use historical volatility proxy', disabled=not applicable):
                st.session_state[f'futures_apply_vol_{context}'] = historical['volatility']*100
                st.rerun()
            calibrated = applicable and math.isclose(volatility/100, historical['volatility'], abs_tol=1e-8)
            st.caption('Using the saved historical volatility proxy.' if calibrated else 'Volatility is a manual assumption; the saved estimate is not applied.')
            if not applicable:
                st.warning('The volatility history extends beyond your start date. Refresh it for this date to avoid look-ahead.')
        else:
            st.warning('Volatility is an uncalibrated assumption. Load a historical proxy or supply your own estimate.')
        st.caption('Historical ETF volatility is a proxy, not implied FMWN volatility or a forecast. '
                   'Futures financing is embedded in prices; it is not an extra margin-loan interest charge. '
                   'Use the margin multiplier to stress a higher requirement throughout the experiment.')
        index_return, financing = saved.index_return, saved.financing_rate
        roll_fee, slippage, scale_margin = saved.roll_fee_per_side, saved.roll_slippage_points, saved.scale_margin
        historical_drift = saved.historical_drift
        return_estimate = None
        return_error = None
        st.markdown('**Index growth and financing · both strategies**')
        return_source = st.radio('Index growth source', ['Historical MSCI World EUR proxy', 'Manual assumption'],
            index=0 if saved.historical_drift else 1, key=key+'growth_source', horizontal=True)
        historical_drift = return_source == 'Historical MSCI World EUR proxy'
        if st.button('Refresh historical index returns'):
            try:
                with st.spinner('Loading up to ten years of adjusted EUR MSCI World history…'):
                    refresh_return_history(directory, start.isoformat())
            except (OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.rerun()
        try:
            return_estimate = load_return_estimate(directory, start.isoformat())
        except DataError as exc:
            return_error = str(exc)
        if historical_drift and return_estimate:
            index_return = return_estimate['index_return']
            st.caption(f"Sample {return_estimate['start_date']} → {return_estimate['end_date']}: "
                       f"{return_estimate['observations']} daily returns, CAGR {return_estimate['cagr']:.2%}, "
                       f"fitted GBM drift {index_return:.2%}. Adjusted EUNL.DE EUR closes via Yahoo. "
                       'This estimates index growth, not historical FMWN trading returns; financing is modelled separately.')
        a, b = st.columns(2)
        if historical_drift:
            a.metric('Historical index drift used', f'{index_return:.2%}' if return_estimate else 'Load history')
        else:
            index_return = a.number_input('Annual index net-total-return drift (%)', min_value=-50., max_value=50.,
                value=100*saved.index_return, step=1., key=key+'index_return')/100
        sources = {'ecb': 'ECB €STR + spread', 'manual': 'Manual rate'}
        financing_source = b.radio('Financing source', list(sources), format_func=sources.get,
            index=list(sources).index(saved.financing_source), key=key+'financing_source', horizontal=True)
        financing_error = None
        financing_label = sources[financing_source]
        spread = saved.financing_spread
        if financing_source == 'ecb':
            spread = st.number_input('Financing spread over €STR (percentage points)', min_value=-10., max_value=10.,
                value=100*spread, step=.05, format='%.2f', key=key+'financing_spread')/100
            financing_label += f' · {100*spread:+.2f} percentage points'
            if dated_reference:
                try:
                    financing = financing_from_estr(dated_reference['estr_rate'], spread)
                except DataError as exc:
                    financing_error = str(exc)
                financing_label += f" · ECB {dated_reference['estr_reference_date']} ({dated_reference['estr_rate']:.3%})"
            else:
                financing_error = reference_error or 'Load an ECB observation on or before the simulation start (reference or historical comparison data), or select a manual rate.'
            st.caption('Approximation: €STR plus a constant spread, default +0.50 percentage points (50 basis points). '
                       'The spread has only a short recent comparison with FMWN settlements; it is not validated over years. '
                       'Forward simulations hold the starting rate constant. Historical quarters use earlier observed €STR '
                       'on each date plus this same spread. The spread does not increase cash savings or collateral interest.')
        else:
            financing = st.number_input('Futures financing rate (% per year)', min_value=-10., max_value=20.,
                value=100*saved.financing_rate, step=.01, key=key+'financing')/100
        if financing_error:
            st.warning(financing_error)
        else:
            st.caption(f'Financing used: {financing:.3%} per year · {financing_label}. Shared by both forward strategies.')
        a, b = st.columns(2)
        roll_fee = a.number_input('Commission per contract per side (€)', min_value=0.,
            value=saved.roll_fee_per_side, step=1., key=key+'roll_fee')
        slippage = b.number_input('Slippage per contract per side (points)', min_value=0.,
            value=saved.roll_slippage_points, step=.01, key=key+'slippage')
        scale_margin = st.checkbox('Scale margin with contract value', value=saved.scale_margin, key=key+'scale_margin')
        st.caption('Historical growth uses up to ten years of adjusted EUR ETF prices, with at least three years required. '
                   'GBM drift equals annual log growth plus half annual log variance; historical CAGR is not inserted as drift. '
                   'The proxy includes fund costs and tracking differences. Historical performance is not a return forecast. '
                   'Financing follows the selected dated source or an explicit manual rate. Missing source data is never silently replaced by 3%. '
                   'Each roll closes and reopens the same quantity. Commission and slippage apply on every side, including entry and final exit. '
                   'All strategies use the same per-side costs and margin rule. Zero execution costs are an optimistic assumption. '
                   'Scaled margin holds today’s margin/notional ratio constant; it does not predict future IBKR requirements.')
    try:
        settings = ComparisonSettings(months, savings/100, cash/100, volatility/100, 0.,
                                      fees, stress, minimum, maximum, saved.levels,
                                      start.isoformat(), until_expiry, comparison_capital=capital,
                                      rolling=rolling, rolling_years=rolling_years, index_return=index_return,
                                      financing_rate=financing, roll_fee_per_side=roll_fee,
                                      roll_slippage_points=slippage, scale_margin=scale_margin, historical_drift=historical_drift,
                                      financing_source=financing_source, benchmark_source=benchmark_source,
                                      financing_spread=spread)
    except DataError as exc:
        st.error(str(exc))
        return None, None
    st.session_state[draft_key] = (revision, settings, (position.quote_date, position.contract_expiry))
    with settings_area:
        if st.session_state.pop(f'futures_shared_saved_{context}', False):
            st.success('Shared futures assumptions saved in the sandbox.')
        if st.button('Save shared futures assumptions'):
            try:
                save_comparison(directory, settings, revision)
            except (DataError, OSError) as exc:
                st.error(str(exc))
            else:
                st.session_state[f'futures_shared_saved_{context}'] = True
                st.rerun()
    return settings, dict(estimate=return_estimate, error=return_error, financing_error=financing_error,
                          financing_label=financing_label, benchmark_error=benchmark_error)
