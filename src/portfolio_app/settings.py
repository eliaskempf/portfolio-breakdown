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


def app_version() -> str:
    return version(APP_ID)


def workspace_path(explicit: Path | None = None) -> Path:
    return (explicit if explicit is not None else user_data_path(APP_ID, appauthor=False) / 'portfolio').expanduser().resolve()


def state_path() -> Path:
    override = os.environ.get('PORTFOLIO_STATE_DIR')
    return Path(override).expanduser().resolve() if override else user_state_path(APP_ID, appauthor=False)


def theme_options() -> list[str]:
    # Chrome is sent when the browser session starts, before UI code executes.
    return ['--client.toolbarMode=viewer', f'--theme.primaryColor={PRIMARY_COLOR}', '--theme.baseRadius=small',
            f'--theme.light.backgroundColor={LIGHT_BACKGROUND}', '--theme.light.secondaryBackgroundColor=#f4f5f7',
            '--theme.light.textColor=#202632', f'--theme.dark.backgroundColor={DARK_BACKGROUND}',
            '--theme.dark.secondaryBackgroundColor=#1a202b', '--theme.dark.textColor=#e3e7ef']


def icon_path(filename: str = 'portfolio-breakdown.png') -> Path | None:
    path = Path(__file__).with_name('assets') / filename
    return path if path.is_file() else None
