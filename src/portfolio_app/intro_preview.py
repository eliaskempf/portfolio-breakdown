"""Animation workbench: uv run streamlit run src/portfolio_app/intro_preview.py."""
import streamlit as st

from portfolio_app.intro import breakdown_intro

st.set_page_config(page_title='Breakdown · Animation preview', layout='centered')
st.title('Startup animation')
st.caption('Local preview · No portfolio files or market requests')
hold = st.slider('Wordmark hold (ms)', 0, 3000, 950, 50)
swirl = st.slider('Swirl duration (ms)', 300, 4000, 1850, 50)
replay = st.button('Replay intro', type='primary')
done = breakdown_intro(key='preview_intro', hold_ms=hold, swirl_ms=swirl, replay=replay, height=360)
st.caption('Intro complete' if done else 'Intro playing…')
st.caption('Timing changes take effect on Replay. The system reduced-motion setting is respected.')
