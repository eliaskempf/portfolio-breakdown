"""Accessible click/keyboard toggles on native metrics."""

import streamlit as st


JS = """
export default function({parentElement, data, setTriggerValue}) {
  // Streamlit invokes this again when data changes, keeping the component host.
  // Dispose the prior listeners before attaching the updated unit toggle.
  parentElement.disposeMetricToggle?.();
  // Lazy tabs can briefly retain the outgoing component host while mounting
  // its replacement. Only one host may own the metric's DOM listeners.
  const registry = window.__portfolioMetricToggles ??= {};
  const interaction = data.interactionKey || 'gain';
  registry[interaction]?.();
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
    target = undefined;
  };
  const attach = () => {
    const candidate = document.querySelector('.st-key-'+CSS.escape(data.containerKey)+' [data-testid="'+(data.target || 'stMetricDelta')+'"]');
    if (candidate === target || !candidate) return;
    detach();
    target = candidate;
    target.setAttribute('role', 'button');
    target.setAttribute('tabindex', '0');
    target.setAttribute('aria-label', data.label);
    target.setAttribute('title', data.tooltip || data.label);
    target.addEventListener('click', activate);
    target.addEventListener('keydown', keydown);
  };
  const observer = new MutationObserver(attach);
  observer.observe(document.body, {childList: true, subtree: true});
  attach();
  const dispose = () => {
    observer.disconnect(); detach();
    if (registry[interaction] === dispose) delete registry[interaction];
  };
  parentElement.disposeMetricToggle = dispose;
  registry[interaction] = dispose;
  return dispose;
}
"""


def toggle_gain_unit(*, percent: bool, on_toggle, container_key='overview_value', interaction_key='gain', tooltip=None) -> None:
    component = st.components.v2.component('overview_gain_toggle', js=JS)
    component_key = container_key.rstrip('_') + '_gain_toggle'
    component(key=component_key, data={
        'containerKey': container_key, 'interactionKey': interaction_key, 'tooltip': tooltip,
        'label': 'Show gain as amount' if percent else 'Show gain as percentage',
    }, on_toggle_change=on_toggle)


def toggle_metric_value(*, label: str, on_toggle, container_key: str, interaction_key: str) -> None:
    component = st.components.v2.component('portfolio_metric_toggle', js=JS)
    component(key=f'{container_key}_value_toggle', data={
        'containerKey': container_key, 'target': 'stMetricValue',
        'interactionKey': interaction_key, 'label': label,
    }, on_toggle_change=on_toggle)
