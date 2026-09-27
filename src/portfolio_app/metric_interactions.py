"""Accessible unit toggle on the overview's native performance metric."""

import streamlit as st


JS = """
export default function({data, setTriggerValue}) {
  let target;
  const activate = () => setTriggerValue('toggle', {nonce: crypto.randomUUID()});
  const keydown = event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      activate();
    }
  };
  const detach = () => {
    if (!target) return;
    target.removeEventListener('click', activate);
    target.removeEventListener('keydown', keydown);
    for (const name of ['role', 'tabindex', 'aria-label', 'title']) target.removeAttribute(name);
  };
  const attach = () => {
    const candidate = document.querySelector('.st-key-overview_value [data-testid="stMetricDelta"]');
    if (candidate === target || !candidate) return;
    detach();
    target = candidate;
    target.setAttribute('role', 'button');
    target.setAttribute('tabindex', '0');
    target.setAttribute('aria-label', data.label);
    target.setAttribute('title', data.label);
    target.addEventListener('click', activate);
    target.addEventListener('keydown', keydown);
  };
  const observer = new MutationObserver(attach);
  observer.observe(document.body, {childList: true, subtree: true});
  attach();
  return () => { observer.disconnect(); detach(); };
}
"""


def toggle_gain_unit(*, percent: bool, on_toggle) -> None:
    component = st.components.v2.component('overview_gain_toggle', js=JS)
    component(key='overview_gain_toggle', data={
        'label': 'Show gain in euros' if percent else 'Show gain as percentage',
    }, on_toggle_change=on_toggle)
