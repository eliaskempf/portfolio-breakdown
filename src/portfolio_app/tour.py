"""Optional guided tour in a fresh, isolated, synthetic workspace."""
import json
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

import streamlit as st

from portfolio_app.settings import state_path
from portfolio_app.tour_steps import STEPS


def dismissed() -> bool:
    try:
        saved = json.loads((state_path() / 'app-tour.json').read_text(encoding='utf-8'))
        return isinstance(saved, dict) and saved.get('dismissed') is True
    except (OSError, ValueError):
        return False


def save_dismissal() -> None:
    directory = state_path()
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                prefix='.app-tour-', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({'dismissed': True}, handle)
            handle.write('\n')
        temporary.replace(directory / 'app-tour.json')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def active() -> bool:
    return 'tour_step' in st.session_state


def demo_plan_requested() -> bool:
    return active() and STEPS[st.session_state['tour_step']].calculate_plan


def request_tour(workspace: str, *, replay: bool = False) -> None:
    for key in ('help_menu', 'settings_menu'):
        st.session_state[key] = False
    st.session_state['tour_request'] = {'workspace': workspace, 'replay': replay}


def workspace_changed(*, demo: bool) -> None:
    if active():
        return
    st.session_state.pop('tour_welcome', None)
    request = st.session_state.get('tour_request')
    if request and request['workspace'] != ('demo' if demo else 'manual'):
        st.session_state.pop('tour_request', None)


def prepare_tour() -> None:
    if active() or st.session_state.get('position_edit_dialog') or st.session_state.get('onboarding_step') in {'categories', 'position'}:
        return
    request = st.session_state.pop('tour_request', None)
    if not request:
        return
    if request['replay']:
        _start()
        st.rerun()
    st.session_state['main_tabs'] = 'Overview'
    if not (st.session_state.get('tour_dismissed') or dismissed()):
        st.session_state['tour_welcome'] = True


def _remember_dismissal() -> None:
    st.session_state['tour_dismissed'] = True
    try:
        save_dismissal()
    except OSError:
        st.session_state['tour_notice'] = 'Tour dismissed for this session. Could not save that preference for next time.'


def _dismiss_welcome() -> None:
    st.session_state.pop('tour_welcome', None)
    _remember_dismissal()


def _start() -> None:
    from portfolio_app.demo import create_demo_data
    # Retain removed state without assigning widget values on return. Session
    # state distinguishes widget state (including read-only button triggers)
    # from durable values, so snapshot the restorable view inputs explicitly.
    from portfolio_app.tour_state import capture_view, clear_view
    st.session_state['tour_return'] = capture_view()
    owner = TemporaryDirectory(prefix='portfolio-guided-demo-')
    st.session_state['tour_directory'] = owner
    create_demo_data(Path(owner.name))
    clear_view()
    st.session_state.pop('tour_welcome', None)
    st.session_state.update(active_portfolio='Demo portfolio', hide_empty_positions=False)
    _move(0)


def demo_directory() -> Path | None:
    return Path(st.session_state['tour_directory'].name) if active() else None


def _move(index: int) -> None:
    step = STEPS[index]
    st.session_state.update(tour_step=index, main_tabs=step.tab)
    if step.tab == 'Overview':
        st.session_state.update(strategic_category='', strategic_view=step.view or 'Allocation')
    elif step.tab == 'Exposure':
        from hashlib import sha256
        context = sha256(str(demo_directory().resolve()).encode()).hexdigest()[:16]
        st.session_state.update(exposure_scope='', exposure_search='', exposure_view=step.view,
                                exposure_group='taxonomy:sector', geography_level='Regions', geography_detail=())
        st.session_state[f'etf_lookthrough_{context}'] = step.lookthrough
    elif step.tab == 'Positions':
        st.session_state['positions_workflow'] = 'Positions'
    else:
        st.session_state.update(rebalance_tabs=step.view, planning_scope='Portfolio contribution', planning_amount=500.)


def _end() -> None:
    from portfolio_app.tour_state import clear_view
    saved = st.session_state.pop('tour_return', None)
    st.session_state.pop('tour_step', None)
    if saved is not None:
        clear_view()
        st.session_state.update(saved)
    owner = st.session_state.pop('tour_directory', None)
    if owner is not None:
        owner.cleanup()
    _remember_dismissal()


def render_tour(*, empty: bool = False) -> None:
    if notice := st.session_state.pop('tour_notice', None):
        st.caption(notice)
    if st.session_state.get('position_edit_dialog'):
        return
    if st.session_state.get('tour_welcome'):
        @st.dialog('Welcome to Breakdown!', on_dismiss=_dismiss_welcome)
        def welcome():
            st.write('Explore the app with a sample portfolio. Afterwards, we’ll bring you back to your portfolio.')
            st.caption('About 2–3 minutes · Skip anytime · Replay from Help')
            start, later = st.columns(2)
            if start.button('Take the tour', key='tour_start', type='primary', on_click=_start, width='stretch'):
                st.rerun()
            if later.button('Not now', key='tour_not_now', on_click=_dismiss_welcome, width='stretch'):
                st.rerun()
        welcome()
        return
    if not active():
        return
    from portfolio_app.tour_style import CSS
    index = st.session_state['tour_step']
    step = STEPS[index]
    st.html('<style>' + CSS + '</style>')
    with st.container(key='app_tour', border=True):
        st.caption(f'TOUR · DEMO PORTFOLIO  /  {step.tab}  /  {index + 1} of {len(STEPS)}')
        st.markdown(f'### {step.title}')
        st.write(step.summary)
        with st.container(key='tour_actions', horizontal=True):
            st.button('Skip tour', key='tour_skip', on_click=_end)
            st.button('Back', key='tour_back', disabled=index == 0, on_click=_move, args=(index - 1,))
            if index == len(STEPS) - 1:
                st.button('Finish', key='tour_finish', type='primary', on_click=_end)
            else:
                st.button('Next', key='tour_next', type='primary', on_click=_move, args=(index + 1,))
        next_chapter = next((n for n in range(index + 1, len(STEPS)) if STEPS[n].tab != step.tab), None)
        if next_chapter is not None:
            st.button(f'Skip to {STEPS[next_chapter].tab} →', key='tour_chapter', type='tertiary',
                      on_click=_move, args=(next_chapter,))
    from portfolio_app.tour_spotlight import JS
    bridge = st.components.v2.component('tour_spotlight', js=JS)
    bridge(key='tour_spotlight', data={'step': index, 'anchors': [anchor for group in step.highlight_groups for anchor in group], 'groups': step.highlight_groups, 'interactive': step.interactive}, on_exit_change=_end)
