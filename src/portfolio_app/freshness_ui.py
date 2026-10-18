"""Small timer-driven result regions, independent of navigation and editors."""
import streamlit as st

from portfolio_app.freshness_view import has_live_freshness, refresh_freshness


def render_freshness(rows, render):
    """Keep the displayed snapshot until a normal data/navigation rerun replaces it."""
    if not has_live_freshness(rows):
        render(rows)
        return

    @st.fragment(run_every=2)
    def result():
        render(refresh_freshness(rows))
    result()


def render_comparison_freshness(rows, members, categories, *, chart_key):
    """Qualify existing bars without sending a new Plotly spec (which remounts it)."""
    from portfolio_app.futures_valuation import summarize_freshness
    def render(current):
        rules, labels, details = [], [], []
        for index, category in enumerate(categories, 1):
            label, detail, qualified = summarize_freshness(
                current.loc[current.position_id.isin(members.get(category, ()))])
            if qualified:
                # One horizontal bar trace in the same order as `categories`.
                rules.append(f'.st-key-{chart_key} .barlayer .trace.bars .point:nth-child({index}) path'
                             + '{fill:#808890!important;}')
                labels.append(f'{category}: {label}')
                details.append(detail)
        st.html('<style>' + ''.join(rules) + '</style>')
        if labels:
            count = len(labels)
            st.caption(f'{count} category result' + ('s' if count != 1 else '') + ' with dated futures valuations',
                       help='\n\n'.join(f'{label}\n{detail}' for label, detail in zip(labels, details)))
    render_freshness(rows, render)
