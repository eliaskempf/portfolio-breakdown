"""First-use routing into existing workspace, position and import workflows."""
import streamlit as st


def _start(choice):
    if choice == 'demo':
        st.session_state['active_portfolio'] = 'Demo portfolio'
        return
    st.session_state['onboarding_started'] = True
    st.session_state['main_tabs'] = 'Positions'
    st.session_state['positions_workflow'] = 'Import portfolio' if choice == 'import' else 'Positions'
    if choice == 'manual':
        st.session_state.update(position_edit_action='Add position', position_edit_dialog=True)


def render_welcome(*, demo_available: bool) -> bool:
    """Return whether the welcome view replaces the empty analysis views."""
    if st.session_state.get('onboarding_started'):
        if st.sidebar.button('Getting started'):
            st.session_state['onboarding_started'] = False
            st.rerun()
        return False
    st.subheader('Welcome to Portfolio Breakdown')
    st.write('Explore an example or start your own portfolio. Your portfolio stays on this computer.')
    columns = st.columns(3 if demo_available else 2)
    if demo_available:
        with columns[0]:
            st.markdown('**Explore a demo**')
            st.write('Try allocation, ETF look-through and rebalancing with an invented portfolio.')
            st.caption('Separate from your portfolio. Demo edits reset when the app restarts.')
            st.button('Explore demo', on_click=_start, args=('demo',), type='primary')
    with columns[-2]:
        st.markdown('**Start manually**')
        st.write('Add your first position, then build your portfolio at your own pace.')
        st.caption('Your saved positions remain available after the app restarts.')
        st.button('Start manually', on_click=_start, args=('manual',))
    with columns[-1]:
        st.markdown('**Import holdings**')
        st.write('Review a CSV or Excel holdings report before saving any positions.')
        st.caption('Experimental · Includes provisional FinanzManager column recognition.')
        st.button('Import holdings', on_click=_start, args=('import',))
    return True


def demo_guide(*, offline=True):
    panel = st.expander('Try the demo', key='demo_guide', on_change='rerun')
    if not panel.open:
        return
    with panel:
        st.markdown(
            '1. **Overview:** compare the 60% equity, 25% money market, 10% gold and 5% crypto targets '
            'with the deliberately uneven holdings. Select Equities to explore its 70/30 World/EM split.\n'
            '2. **Exposure:** toggle **Break down ETFs** to see the two equity funds turn into example '
            'constituents and residual Other exposure.\n'
            '3. **Positions:** inspect the made-up buy-ins and their gains and losses.\n'
            '4. **Rebalance:** calculate a plan against the saved targets, or try allocating new money.'
        )

        st.caption('Offline example: prices, history and partial ETF weights are invented.' if offline else
                   'Prices and history come from market providers; ETF weights come from issuer downloads. '
                   'Buy-ins are invented, not historical transactions. Missing downloads are shown explicitly.')
