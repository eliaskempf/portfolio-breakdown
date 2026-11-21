"""Reserve stable sibling paths before rendering state-dependent UI sections."""
import streamlit as st


def render_sections(prefix: str, *names: str):
    """Allocate every section, in display order, even when its content is empty.

    Streamlit reconciles streamed elements by their numeric path. An optional
    message inserted before a control moves that control and can leave its old
    copy visible until the rerun finishes. Reserve sibling blocks first, then
    render variable content inside the appropriate section. A key alone on a
    conditionally created block does not reserve its position.

    Use within the owning fragment, never from a timer writing into a caller's
    container. Keep existing widget keys and ownership guards. These blocks do
    not hide stale content or clear the whole view on ticks.
    """
    return {name: st.container(key=prefix+name) for name in names}
