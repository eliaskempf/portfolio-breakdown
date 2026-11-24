"""Common planning controls; saved targets are never changed here."""
import streamlit as st

from portfolio_app.portfolio.allocation import analysis_targets, ignore_empty_by_bucket
from portfolio_app.portfolio.rebalancing import fixed_cash, ignore_empty_positions


def plan_preview(key, signature, requested, calculate):
    """Retain a calculated preview while its evidence and inputs still match."""
    saved = st.session_state.get(key)
    if requested:
        st.session_state.pop(key, None)
        plan = calculate()
        st.session_state[key] = (signature, plan)
        return plan
    if saved is not None:
        if saved[0] == signature:
            return saved[1]
        del st.session_state[key]
    return None


def planning_positions(valued, allocation=None):
    redistribute = st.toggle('Exclude empty positions and redistribute targets', key='ignore_empty_positions',
                             help='Temporarily redistribute targets among held positions within each category. Saved targets stay unchanged.')
    if not redistribute:
        return valued
    return analysis_targets(ignore_empty_by_bucket(valued, allocation), allocation) if allocation else ignore_empty_positions(valued)


def cash_plan_summary(positions, *, portfolio_value=None):
    fixed = fixed_cash(positions)
    if not fixed.any():
        return False
    from portfolio_app.interface.shared.currency_display import money
    values = positions.loc[fixed, 'current_value_reporting']
    balance = values.sum(skipna=False)
    amount = money(balance, missing='Unknown')
    share = f' · {balance / portfolio_value:.1%} of portfolio' if values.notna().all() and portfolio_value is not None and portfolio_value > 0 else ''
    st.metric('Cash held in accounts', amount)
    st.caption('Held at current balances in this plan' + share)
    return fixed.all()


def restriction_summary(*, no_new, caps, selected=None, maximum=None):
    parts = []
    if st.session_state.get('ignore_empty_positions'):
        parts.append('Empty positions excluded · targets redistributed')
    if no_new:
        parts.append('Existing positions only')
    if selected is not None:
        parts.append(f'{selected} positions selected')
    if maximum is not None:
        parts.append(f'Up to {maximum} trades')
    if caps:
        parts.append(f'{len(caps)} allocation caps')
    if parts:
        st.caption(' · '.join(parts))


PLANNING_HELP = ('Read-only preview; no orders are placed and saved holdings stay unchanged. '
                 'Plans allow fractional quantities and exclude fees, taxes, spreads and lot-size rules. '
                 'Overview and Exposure filters do not affect the planning scope.')
