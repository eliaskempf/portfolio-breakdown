"""Inspect shared read-only lists in synthetic Streamlit tests."""
import json
import pandas as pd


def list_data(app, title):
    return next(data for item in app.get('bidi_component')
                if item.proto.component_name == 'portfolio_data_list'
                and (data := json.loads(item.proto.json))['title'] == title)


def list_frame(app, title):
    rows = list_data(app, title)['rows']
    if isinstance(rows, dict):
        frame = pd.DataFrame(json.loads(rows['values']), columns=rows['fields'])
        return frame.assign(id=frame[rows['idColumn']].astype(str))
    return pd.DataFrame(rows)


def exposure_breakdown(app):
    """Inspect full financial contributions for the rows present in the rendered list."""
    visible = list_frame(app, 'Exposure assets')
    table, _ = app.session_state['parsed_inputs']['exposure_assets_table'][1]
    return table.set_index('asset_id', drop=False).loc[visible.asset_id].reset_index(drop=True)
