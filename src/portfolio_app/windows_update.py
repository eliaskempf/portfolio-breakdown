"""User-requested Windows installer downloads and launcher-owned handoff.

Only the registered per-user installation is eligible. Release metadata and
downloads come from the public repository; portfolio files are never read.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock
from portfolio_app.settings import app_version, state_path
from portfolio_app.updates import MAX_RESPONSE, REPOSITORY, version_number

UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6D349059-F435-44AF-BBD7-FC10D66F665B}_is1'
MAX_INSTALLER = 1024**3


def installed_directory():
    """A portable copy must never replace a separately registered installation."""
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            location, _ = winreg.QueryValueEx(key, 'InstallLocation')
        executable = Path(sys.executable).resolve()
        directory = Path(location).resolve()
        if (directory == executable.parent and executable.name.lower() in
                {'portfolio-app.exe', 'portfolio breakdown.exe'}
                and (directory / 'unins000.exe').is_file()):
            return directory
    except (OSError, TypeError, ValueError):
        pass
    return None


def _trusted_url(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None
            and parsed.hostname in {'api.github.com', 'github.com',
                                    'release-assets.githubusercontent.com', 'objects.githubusercontent.com'})


class _ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_url(newurl):
            raise DataError('The update download redirected to an unsupported address.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url, *, binary=False):
    request = Request(url, headers={
        'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'Portfolio-Breakdown-updater'})
    return build_opener(_ReleaseRedirects()).open(request, timeout=15)


@dataclass(frozen=True)
class Installer:
    tag: str
    asset_id: int
    size: int
    digest: str

    @property
    def filename(self):
        return f'portfolio-breakdown-{self.tag.removeprefix("v")}-windows-x64-setup.exe'

    def path(self, directory):
        return Path(directory) / str(self.asset_id) / self.filename


def installer_metadata(tag, *, installed=None):
    current = version_number(app_version() if installed is None else installed)
    requested = version_number(tag)
    if current is None or requested is None or requested <= current:
        raise DataError('Choose a newer stable release.')
    with _open(f'https://api.github.com/repos/{REPOSITORY}/releases/tags/{tag}') as response:
        content = response.read(MAX_RESPONSE + 1)
    if len(content) > MAX_RESPONSE:
        raise DataError('Release metadata is too large.')
    raw = json.loads(content)
    if (not isinstance(raw, dict) or raw.get('tag_name') != tag
            or raw.get('draft') is not False or raw.get('prerelease') is not False
            or not isinstance(raw.get('assets'), list)):
        raise DataError('This stable release is no longer available.')
    name = f'portfolio-breakdown-{tag.removeprefix("v")}-windows-x64-setup.exe'
    assets = [a for a in raw['assets'] if isinstance(a, dict) and a.get('name') == name]
    if len(assets) != 1:
        raise DataError('No Windows installer is available. Use View release.')
    asset = assets[0]
    digest = asset.get('digest')
    if (asset.get('state') != 'uploaded' or type(asset.get('id')) is not int or asset['id'] <= 0
            or type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_INSTALLER
            or not isinstance(digest, str) or not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', digest)):
        raise DataError('The installer cannot be verified. Use View release for manual installation.')
    return Installer(tag, asset['id'], asset['size'], digest[7:].lower())


def verify_installer(path, installer):
    if path.stat().st_size != installer.size:
        raise DataError('The installer is incomplete. Download it again.')
    digest = sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024**2), b''):
            digest.update(block)
    if digest.hexdigest() != installer.digest:
        raise DataError('The installer checksum does not match. Download it again.')


def download_installer(installer, directory, progress=lambda value: None):
    destination = installer.path(directory)
    with write_lock(Path(directory) / 'download.lock', blocking=False):
        if destination.exists():
            try:
                verify_installer(destination, installer)
                return destination
            except DataError:
                pass
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.part', delete=False) as output:
                temporary = Path(output.name)
                url = f'https://api.github.com/repos/{REPOSITORY}/releases/assets/{installer.asset_id}'
                with _open(url, binary=True) as response:
                    count, deadline = 0, time.monotonic() + 30 * 60
                    while block := response.read(1024**2):
                        count += len(block)
                        if count > installer.size or time.monotonic() > deadline:
                            raise DataError('The update download exceeded its size or time limit.')
                        output.write(block)
                        progress(count / installer.size)
                output.flush()
                os.fsync(output.fileno())
            verify_installer(temporary, installer)
            temporary.replace(destination)
            return destination
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class DownloadStatus:
    tag: str = ''
    phase: str = 'idle'
    progress: float = 0
    error: str = ''


class InstallerDownload:
    """One background download per app, with immutable UI snapshots."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self._lock = threading.Lock()
        self._status = DownloadStatus()

    def status(self):
        with self._lock:
            return self._status

    def start(self, tag):
        with self._lock:
            if self._status.phase == 'downloading':
                return False
            self._status = DownloadStatus(tag, 'downloading')
        threading.Thread(target=self._download, args=(tag,), daemon=True, name='installer-download').start()
        return True

    def _set(self, status):
        with self._lock:
            self._status = status

    def retry_after_error(self, tag, message):
        with self._lock:
            if self._status.tag == tag and self._status.phase == 'ready':
                self._status = DownloadStatus(tag, 'failed', error=message)

    def _download(self, tag):
        try:
            asset = installer_metadata(tag)
            download_installer(asset, self.directory,
                               lambda value: self._set(DownloadStatus(tag, 'downloading', value)))
            self._set(DownloadStatus(tag, 'ready', 1))
        except Exception as exc:
            message = str(exc) if isinstance(exc, DataError) else 'Download unavailable. Retry or use View release.'
            self._set(DownloadStatus(tag, 'failed', error=message))


@lru_cache(maxsize=8)
def _downloads(directory):
    return InstallerDownload(directory)


def downloads_for():
    return _downloads(str(state_path() / 'updates'))


def installer_environment(directory):
    env = dict(os.environ)
    env['PATH'] = os.pathsep.join(entry for entry in env.get('PATH', '').split(os.pathsep)
                                if entry and not Path(entry.strip('"')).resolve().is_relative_to(directory))
    env.pop('PORTFOLIO_DOCS_URL', None)
    # Setup can reopen the replaced executable: it must start a fresh bootloader.
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


@contextmanager
def system_dll_search():
    """Undo PyInstaller's inherited DLL search path while spawning Setup."""
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        yield
        return
    import ctypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    api.GetDllDirectoryW.restype = ctypes.c_uint32
    api.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    api.SetDllDirectoryW.restype = ctypes.c_int
    previous = ctypes.create_unicode_buffer(api.GetDllDirectoryW(0, None) + 1)
    api.GetDllDirectoryW(len(previous), previous)
    if not api.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        api.SetDllDirectoryW(previous.value or None)


class InstallerHandoff:
    """Runs in the launcher, outside the native server's kill-on-close job."""
    def __init__(self):
        self.pending = None
        self._lock = threading.Lock()

    def request(self, tag):
        with self._lock:
            if self.pending is not None:
                raise DataError('An update is already starting.')
            directory = installed_directory()
            if directory is None:
                raise DataError('Exit and update requires an installed Windows app.')
            # Do not accept a path, executable arguments or a digest from the UI.
            # Re-fetch this exact release and verify the staged bytes ourselves.
            asset = installer_metadata(tag)
            path = asset.path(state_path() / 'updates')
            verify_installer(path, asset)
            self.pending = (directory, asset, path)

    def launch(self):
        if self.pending is None:
            return
        directory, asset, path = self.pending
        verify_installer(path, asset)
        # The standard interactive wizard lets the user finish installation and
        # reopen the app. It must neither kill other windows nor reboot Windows.
        with system_dll_search():
            subprocess.Popen([str(path), '/SP-', '/NORESTART', '/NOCLOSEAPPLICATIONS',
                              '/NOFORCECLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS', f'/DIR={directory}'],
                             cwd=path.parent, env=installer_environment(directory),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True,
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
