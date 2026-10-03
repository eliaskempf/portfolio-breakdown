"""Draft category rows and confirmation for first-use setup; no implicit saves."""
from html import escape
import json
import math

import streamlit as st

from portfolio_app.holdings import DataError
from portfolio_app.onboarding import initial_categories, save_initial_categories


CATEGORY_HELP = ('Examples: Equities, Bonds or Gold. Choose labels that fit your portfolio. '
                 'Each position can belong to one category. You can change these later in Rebalance → Targets.')
TARGET_HELP = ('Share of the whole portfolio. Leave blank if undecided; blank is not zero. '
               'Targets within a category are set separately when adding positions.')


def _rows():
    return st.session_state.setdefault('onboarding_rows', [])


def _values():
    rows = _rows()
    return [row['name'] for row in rows], [row['target'] for row in rows]


def _valid():
    try:
        initial_categories(*_values())
    except DataError:
        return False
    return True


def _complete():
    _, targets = _values()
    return (_valid() and all(value is not None for value in targets)
            and math.isclose(sum(targets), 100., abs_tol=.0001))


def _signature():
    return tuple((row['name'], row['target']) for row in _rows())


def _add(index):
    name = st.session_state[f'onboarding_new_name_{index}'].strip()
    target = st.session_state[f'onboarding_new_target_{index}']
    if not name:
        st.session_state['onboarding_row_error'] = 'Enter a category name.'
        return
    if name.casefold() in {row['name'].strip().casefold() for row in _rows()}:
        st.session_state['onboarding_row_error'] = 'Use a different name for each category.'
        return
    _rows().append(dict(id=index, name=name, target=target))
    st.session_state['onboarding_next_row'] = index + 1
    st.session_state.pop('onboarding_row_error', None)


def _focus_new(index):
    # A bounded, one-shot focus adapter. Never interpolate user labels into JS.
    st.html('''<script>(() => {
      // Keep pointer help, but remove help buttons from sequential keyboard navigation.
      // Associate the same explanations with inputs for assistive technology.
      window.__categorySetupHelpObserver?.disconnect();
      const root = document.querySelector('.st-key-onboarding_new_category');
      function updateHelp() {
        root?.querySelectorAll('button[aria-label^="Help for "]').forEach(button => {
          button.tabIndex = -1;
        });
        const name = root?.querySelector('[data-testid="stTextInput"] input');
        const target = root?.querySelector('[data-testid="stNumberInput"] input');
        name?.setAttribute('aria-description', CATEGORY_DESCRIPTION);
        target?.setAttribute('aria-description', TARGET_DESCRIPTION);
      }
      updateHelp();
      if (root) {
        const observer = new MutationObserver(updateHelp);
        observer.observe(root, {childList: true, subtree: true});
        window.__categorySetupHelpObserver = observer;
      }
      const selector = '.st-key-onboarding_new_name_INDEX input';
      const token = 'ROW_TOKEN';
      if (window.__categorySetupFocus === token) return;
      let attempts = 0;
      function focus() {
        const input = document.querySelector(selector);
        if (input && input.getClientRects().length && !input.disabled) {
          input.focus();
          if (document.activeElement === input) { window.__categorySetupFocus = token; return; }
        }
        if (++attempts < 40) setTimeout(focus, 50);
      }
      focus();
    })();</script>'''.replace('INDEX', str(index)).replace('ROW_TOKEN',
        f"{st.session_state['onboarding_focus_session']}-{index}").replace(
            'CATEGORY_DESCRIPTION', json.dumps(CATEGORY_HELP)).replace(
            'TARGET_DESCRIPTION', json.dumps(TARGET_HELP)), unsafe_allow_javascript=True)


def render_category_setup(directory, snapshot, first_position):
    from uuid import uuid4
    st.session_state.setdefault('onboarding_focus_session', uuid4().hex)

    def save():
        try:
            save_initial_categories(directory, *_values(), snapshot.revision)
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            first_position()
            st.rerun()

    @st.dialog('All set?', dismissible=False)
    def review():
        rows = ''.join(f'<tr><th scope="row">{escape(row["name"])}</th>'
                       f'<td>{row["target"]:g}%</td></tr>' for row in _rows())
        st.html('<style>.category-review {width:100%;border-collapse:collapse;}'
                '.category-review th,.category-review td {padding:.5rem .25rem;'
                'border-bottom:1px solid color-mix(in srgb,currentColor 15%,transparent);}'
                '.category-review th {text-align:left;overflow-wrap:anywhere;}'
                '.category-review tbody th {font-weight:400;}'
                '.category-review td,.category-review th:last-child {text-align:right;'
                'font-variant-numeric:tabular-nums;white-space:nowrap;}'
                '</style><table class="category-review" aria-label="Category targets">'
                '<thead><tr><th scope="col">Category</th><th scope="col">Target</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>')
        st.caption('Continuing saves these categories. Your first position is saved separately.')
        if st.button('Continue to first position', type='primary', width='stretch'):
            save()
        if st.button('Keep editing', width='stretch'):
            st.session_state['onboarding_reviewed'] = _signature()
            st.session_state['onboarding_review'] = False
            st.rerun()

    @st.dialog('Set up your portfolio', width='large', dismissible=False)
    def setup():
        st.caption('1 of 2 · Categories and optional targets')
        st.write('Group your holdings into categories. Targets are optional.')
        st.caption('Portfolio currency: EUR. Holdings in other currencies are converted to euros.')
        for number, row in enumerate(_rows(), 1):
            with st.container(border=True):
                name, target, remove = st.columns([3, 2, 1], vertical_alignment='bottom')
                row['name'] = name.text_input(f'Category {number}', value=row['name'],
                    key=f"onboarding_name_{row['id']}")
                row['target'] = target.number_input(f'Target {number} (%)', value=row['target'],
                    min_value=0., max_value=100., step=1., key=f"onboarding_target_{row['id']}")
                if remove.button('Remove', key=f"onboarding_remove_{row['id']}"):
                    _rows().remove(row)
                    st.rerun()

        index = st.session_state.get('onboarding_next_row', 0)
        with st.container(border=True, key='onboarding_new_category'):
            st.markdown('**Add a category**')
            with st.form(f'onboarding_add_{index}', border=False):
                name, target = st.columns([3, 2])
                name.text_input('Category name', key=f'onboarding_new_name_{index}', help=CATEGORY_HELP,
                                placeholder='Choose a name')
                target.number_input('Target (%) · optional', min_value=0., max_value=100., value=None,
                                    step=1., key=f'onboarding_new_target_{index}', help=TARGET_HELP)
                added = st.form_submit_button('Add category', on_click=_add, args=(index,))
            if st.session_state.get('onboarding_row_error'):
                st.error(st.session_state['onboarding_row_error'])
        if added and st.session_state.get('onboarding_next_row', 0) != index:
            st.rerun()
        st.html('<style>.st-key-onboarding_new_category:focus-within {outline:2px solid '
                'var(--st-primary-color, #6366f1);outline-offset:2px;border-radius:0.5rem;}</style>')
        _focus_new(index)
        _, targets = _values()
        total = sum(value for value in targets if value is not None)
        if any(value is not None for value in targets):
            st.caption(f'Target total: {total:g}% of portfolio')
            if not _complete():
                st.caption('You can finish targets later. Missing targets stay unknown; '
                           'rebalancing needs complete targets for its selected scope.')
        else:
            st.caption('No targets assumed. You can add them later in Rebalance → Targets.')
        if _complete() and st.session_state.get('onboarding_reviewed') != _signature():
            st.session_state['onboarding_review'] = True
            st.rerun()
        proceed, skip = st.columns(2)
        if proceed.button('Save categories & continue', type='primary', width='stretch', disabled=not _rows()):
            save()
        if skip.button('Skip setup', width='stretch'):
            st.session_state['onboarding_step'] = 'done'
            st.rerun()

    if st.session_state.get('onboarding_review'):
        review()
    else:
        setup()
