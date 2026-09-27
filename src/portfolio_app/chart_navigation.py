"""Forward native Plotly sunburst navigation to a Streamlit category control."""

import streamlit as st

JS = """
export default function({data, setTriggerValue}) {
  let plot;
  const clicked = event => {
    const point = event.points?.[0]?.id;
    if (Object.hasOwn(data.positions, point)) {
      setTriggerValue('position', {id: data.positions[point]});
      return false;
    }
    // The server maps the current center to its parent. Plotly's nextLevel
    // describes its own zoom state, which we suppress in favour of app scope.
    const node = point ?? event.nextLevel;
    if (Object.hasOwn(data.categories, node)) {
      setTriggerValue('category', {node});
      return false;
    }
    return false;
  };
  const attach = () => {
    const candidate = document.querySelector('.st-key-' + CSS.escape(data.chartKey) + ' .js-plotly-plot');
    if (candidate === plot || !candidate?.on) return;
    plot?.removeListener(data.eventName, clicked);
    plot = candidate;
    plot.on(data.eventName, clicked);
  };
  const observer = new MutationObserver(attach);
  observer.observe(document.body, {childList: true, subtree: true});
  attach();
  return () => {
    observer.disconnect();
    plot?.removeListener(data.eventName, clicked);
  };
}
"""


def sync_chart_category(chart_key: str, categories: dict, control_key: str, *, positions=None, open_position=None, event_name='plotly_sunburstclick') -> None:
    component = st.components.v2.component("strategic_chart_navigation", js=JS)
    bridge_key = f"strategic_navigation_{control_key}"

    def navigate():
        event = st.session_state.get(bridge_key, {}).get("category")
        if event and event.get('node') in categories:
            st.session_state[control_key] = categories[event['node']]

    def show_position():
        event = st.session_state.get(bridge_key, {}).get('position')
        if open_position and event and event.get('id') in (positions or {}).values():
            open_position(event['id'])

    component(key=bridge_key, data={"chartKey": chart_key, "categories": categories, 'positions': positions or {}, 'eventName': event_name},
              on_category_change=navigate, on_position_change=show_position)
