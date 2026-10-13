"""Monitoring examples confined to the tour-owned temporary workspace."""
import streamlit as st

from portfolio_app.monitoring import Rule, SeriesSpec, evaluate_series, utc_now
from portfolio_app.monitoring_market import SyntheticMonitoringProvider
from portfolio_app.monitoring_store import load_monitoring, save_rule, record_evaluations, rule_from_dict


RULE_ID = 'tour-sma-example'


def _require_tour(directory):
    from portfolio_app.tour import demo_directory
    if directory is None or directory != demo_directory() or not (directory / '.synthetic-demo').is_file():
        raise ValueError('Monitoring tour requires its own synthetic workspace.')


def prepare_step(directory, view):
    _require_tour(directory)
    st.session_state['monitoring_view'] = 'Browse' if view in {'Browse', 'Configure'} else 'Tracked rules'
    st.session_state['monitoring_browse_threshold'] = 10. if view == 'Configure' else None
    if view in {'Tracked rules', 'Alert'}:
        doc, _ = load_monitoring(directory)
        if not any(r['id'] == RULE_ID for r in doc['rules']):
            rule = Rule(RULE_ID, 'MSCI World SMA 200',
                        series=SeriesSpec('MSCI:990100:NETR:EUR', 'MSCI World', 'EUR',
                                          source='MSCI', convention='Net total return'),
                        threshold=10., warning_band=1., created_at=utc_now().isoformat())
            save_rule(directory, rule)
            record_evaluations(directory, rule, [evaluate_series(rule, SyntheticMonitoringProvider().fetch(rule.series))])
        st.session_state['monitoring_selected'] = RULE_ID


def render_alert(directory):
    _require_tour(directory)
    from portfolio_app.monitoring_ui import render_alert_event
    doc, _ = load_monitoring(directory)
    rule = rule_from_dict(next(r for r in doc['rules'] if r['id'] == RULE_ID))
    event = next(e for e in doc['events'] if e['rule_id'] == RULE_ID)
    with st.container(key='tour_monitoring_alert', border=True):
        if event['acknowledged']:
            st.success('Alert acknowledged. The rule remains enabled; its condition is still Triggered.')
        else:
            render_alert_event(directory, rule, event)
