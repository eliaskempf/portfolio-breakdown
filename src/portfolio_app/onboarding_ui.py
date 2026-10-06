"""First-use routing into existing workspace, position and import workflows."""
import streamlit as st


def _start(choice):
    from portfolio_app.tour import request_tour
    request_tour(choice)
    if choice == 'demo':
        st.session_state['active_portfolio'] = 'Demo portfolio'
        return
    st.session_state['onboarding_started'] = True
    st.session_state['onboarding_step'] = 'categories'
    st.session_state['main_tabs'] = 'Positions'
    st.session_state['positions_workflow'] = 'Positions'


def render_welcome(*, demo_available: bool) -> bool:
    """Return whether the welcome view replaces the empty analysis views."""
    if st.session_state.get('onboarding_started'):
        return False

    @st.dialog('Welcome to Portfolio Breakdown', width='large', dismissible=False)
    def welcome():
        st.write('A clearer view of what you own. Choose where to begin.')
        columns = st.columns(2 if demo_available else 1)
        if demo_available:
            with columns[0], st.container(key='welcome_demo', border=True):
                st.html('<div class="welcome-symbol" aria-hidden="true">◔</div>')
                st.markdown('### Explore a demo')
                st.write('Discover allocations, ETF holdings and rebalancing with a ready-made portfolio.')
                st.caption('Try freely. Demo edits reset on restart.')
                if st.button('Explore demo', help='Explore invented holdings in a separate demonstration workspace.', on_click=_start, args=('demo',), type='primary', width='stretch'):
                    st.rerun()
        with columns[-1], st.container(key='welcome_personal', border=True):
            st.html('<div class="welcome-symbol" aria-hidden="true">＋</div>')
            st.markdown('### Start my portfolio')
            st.write('Set up optional categories, then add your first position. You can skip setup.')
            st.caption('Your portfolio stays saved on this computer.')
            if st.button('Start my portfolio', help='Set up your own categories and first holding in the local workspace.', on_click=_start, args=('manual',), width='stretch'):
                st.rerun()
    welcome()
    return True


def demo_guide(*, offline=True):
    with st.container():
        st.markdown('**Try the demo**')
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


def _first_position():
    from portfolio_app.position_ui import _clear_editor
    _clear_editor()
    st.session_state.update(onboarding_step='position', main_tabs='Positions',
                            positions_workflow='Positions', position_edit_action='Add position',
                            position_edit_dialog=True)


def render_guided_setup(directory, snapshot, allocation):
    if st.session_state.get('onboarding_step') != 'categories':
        return False
    if allocation is not None:
        _first_position()
        st.rerun()

    from portfolio_app.category_setup_ui import render_category_setup
    render_category_setup(directory, snapshot, _first_position)
    return True
