"""Forward native Plotly sunburst navigation to a Streamlit category control."""

import streamlit as st

JS = """
export default function({data, setTriggerValue}) {
  let plot;
  const clicked = event => {
    const node = event.nextLevel ?? event.points?.[0]?.id;
    if (Object.hasOwn(data.categories, node)) {
      setTriggerValue('category', {id: data.categories[node]});
    }
  };
  const attach = () => {
    const candidate = document.querySelector('.st-key-' + CSS.escape(data.chartKey) + ' .js-plotly-plot');
    if (candidate === plot || !candidate?.on) return;
    plot?.removeListener('plotly_sunburstclick', clicked);
    plot = candidate;
    plot.on('plotly_sunburstclick', clicked);
  };
  const observer = new MutationObserver(attach);
  observer.observe(document.body, {childList: true, subtree: true});
  attach();
  return () => {
    observer.disconnect();
    plot?.removeListener('plotly_sunburstclick', clicked);
  };
}
"""


def sync_chart_category(chart_key: str, categories: dict[str, str], control_key: str) -> None:
    component = st.components.v2.component("strategic_chart_navigation", js=JS)
    bridge_key = f"strategic_navigation_{control_key}"

    def navigate():
        event = st.session_state.get(bridge_key, {}).get("category")
        if event and event.get("id") in categories.values():
            st.session_state[control_key] = event["id"]

    component(key=bridge_key, data={"chartKey": chart_key, "categories": categories},
              on_category_change=navigate)
