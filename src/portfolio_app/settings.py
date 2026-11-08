"""Shared application identity, paths and launch presentation defaults."""
from importlib.metadata import version
import os
from pathlib import Path

from platformdirs import user_data_path, user_state_path

APP_ID = 'portfolio-breakdown'
APP_NAME = 'Portfolio Breakdown'
PRIMARY_COLOR = '#5470c6'
GAIN_COLOR = '#27836c'
LOSS_COLOR = '#b84655'
LIGHT_BACKGROUND = '#ffffff'
DARK_BACKGROUND = '#11151c'
BRANDING_ASSETS = ('portfolio-breakdown.svg', 'portfolio-breakdown.png', 'favicon.svg', 'favicon.ico')


def package_path(*parts: str) -> Path:
    """Locate shipped scripts/resources independently of a consumer's directory."""
    return Path(__file__).parent.joinpath(*parts)


def app_version() -> str:
    return version(APP_ID)


def workspace_path(explicit: Path | None = None) -> Path:
    return (explicit if explicit is not None else user_data_path(APP_ID, appauthor=False) / 'portfolio').expanduser().resolve()


def state_path() -> Path:
    override = os.environ.get('PORTFOLIO_STATE_DIR')
    return Path(override).expanduser().resolve() if override else user_state_path(APP_ID, appauthor=False)


THEME_SETTINGS = {
    'client.toolbarMode': 'viewer', 'theme.primaryColor': PRIMARY_COLOR,
    'theme.light.primaryColor': PRIMARY_COLOR, 'theme.dark.primaryColor': PRIMARY_COLOR,
    'theme.baseRadius': 'small', 'theme.light.backgroundColor': LIGHT_BACKGROUND,
    'theme.light.secondaryBackgroundColor': '#f4f5f7', 'theme.light.textColor': '#202632',
    'theme.dark.backgroundColor': DARK_BACKGROUND, 'theme.dark.secondaryBackgroundColor': '#1a202b',
    'theme.dark.textColor': '#e3e7ef',
}


def theme_options() -> list[str]:
    return [f'--{key}={value}' for key, value in THEME_SETTINGS.items()]


def ensure_app_theme() -> None:
    """Keep direct Streamlit launches consistent with the application launcher.

    Streamlit sends the theme before executing a script. If a launch omitted our
    settings, rerun before drawing widgets so the next session message carries
    the app theme. Preserve the user's light/dark choice.
    """
    import streamlit as st
    from streamlit import config
    changed = False
    for key, value in THEME_SETTINGS.items():
        if config.get_option(key) != value:
            config.set_option(key, value)
            changed = True
    if changed:
        st.rerun()


def icon_path(filename: str = 'portfolio-breakdown.png') -> Path | None:
    path = Path(__file__).with_name('assets') / filename
    return path if path.is_file() else None
