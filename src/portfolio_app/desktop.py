"""Per-user shortcuts and opening the active local workspace."""
import os
from pathlib import Path
import subprocess
import sys
import shutil
import webbrowser

from platformdirs import user_data_path

from portfolio_app.launcher import app_command
from portfolio_app.settings import APP_NAME, icon_path, state_path


def system_environment() -> dict[str, str]:
    """Do not pass PyInstaller's bundled library path to system desktop tools."""
    env = dict(os.environ)
    if getattr(sys, 'frozen', False) and sys.platform.startswith('linux'):
        if 'LD_LIBRARY_PATH_ORIG' in env:
            env['LD_LIBRARY_PATH'] = env['LD_LIBRARY_PATH_ORIG']
        else:
            env.pop('LD_LIBRARY_PATH', None)
    return env


def open_browser(url: str) -> None:
    if getattr(sys, 'frozen', False) and sys.platform.startswith('linux') and shutil.which('xdg-open'):
        subprocess.Popen(['xdg-open', url], env=system_environment(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        os.startfile(str(path))
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)],
                         env=system_environment(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def desktop_quote(value: str) -> str:
    # Desktop Entry Exec has its own escaping; shell quoting does not apply.
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def install_shortcut(directory: Path) -> Path:
    command = app_command() + ['--desktop', '--data-dir', str(directory)]
    if os.name == 'nt':
        if getattr(sys, 'frozen', False):
            gui = Path(sys.executable).with_name('Portfolio Breakdown.exe')
            if gui.is_file():
                command[0] = str(gui)
        target = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Portfolio Breakdown.lnk'
        target.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PORTFOLIO_SHORTCUT=str(target), PORTFOLIO_EXECUTABLE=command[0],
                   PORTFOLIO_ARGUMENTS=subprocess.list2cmdline(command[1:]))
        icon = icon_path()
        env['PORTFOLIO_ICON'] = command[0]
        if icon and not getattr(sys, 'frozen', False):
            # Windows shortcuts need ICO; keep generated formats out of source.
            from PIL import Image
            converted = state_path() / 'icons' / 'portfolio-breakdown.ico'
            converted.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(icon) as artwork:
                artwork.save(converted, format='ICO')
            env['PORTFOLIO_ICON'] = str(converted)
        script = ('$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:PORTFOLIO_SHORTCUT); '
                  '$s.TargetPath=$env:PORTFOLIO_EXECUTABLE; $s.Arguments=$env:PORTFOLIO_ARGUMENTS; '
                  '$s.IconLocation=$env:PORTFOLIO_ICON; $s.WindowStyle=1; $s.Save()')
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], env=env, check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        target = user_data_path() / 'applications/portfolio-breakdown.desktop'
        target.parent.mkdir(parents=True, exist_ok=True)
        content = ('[Desktop Entry]\nType=Application\nName=' + APP_NAME + '\n'
                   'Comment=Local portfolio allocation and exposure analysis\n'
                   'Exec=' + ' '.join(desktop_quote(arg) for arg in command) + '\n'
                   'Terminal=false\nCategories=Office;Finance;\n')
        icon = icon_path()
        if icon:
            content += f'Icon={icon}\n'
        target.write_text(content, encoding='utf-8')
        target.chmod(0o755)
    return target


def startup_error(message: str) -> None:
    """Make desktop failures visible even after the launcher console closes."""
    if os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    elif shutil.which('zenity'):
        subprocess.run(['zenity', '--error', '--title=' + APP_NAME, '--text=' + message], env=system_environment(), check=False)
    elif shutil.which('notify-send'):
        subprocess.run(['notify-send', APP_NAME, message], env=system_environment(), check=False)
