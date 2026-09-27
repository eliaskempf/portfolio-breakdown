"""Inspect shared read-only lists in synthetic Streamlit tests."""
import json
import pandas as pd


def list_data(app, title):
    return next(data for item in app.get('bidi_component')
                if item.proto.component_name == 'portfolio_data_list'
                and (data := json.loads(item.proto.json))['title'] == title)


def list_frame(app, title):
    return pd.DataFrame(list_data(app, title)['rows'])
