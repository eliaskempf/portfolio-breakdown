"""Common planning controls; saved targets are never changed here."""
import streamlit as st

from portfolio_app.allocation import analysis_targets, ignore_empty_by_bucket
from portfolio_app.rebalancing import ignore_empty_positions


def planning_positions(valued, allocation=None):
    redistribute = st.toggle('Exclude empty positions and redistribute targets', key='ignore_empty_positions',
                             help='Temporarily redistribute targets among held positions within each category. Saved targets stay unchanged.')
    if not redistribute:
        return valued
    return analysis_targets(ignore_empty_by_bucket(valued), allocation) if allocation else ignore_empty_positions(valued)


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
