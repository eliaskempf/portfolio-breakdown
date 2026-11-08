"""Pinned public previous-release inputs for synthetic Windows Setup upgrades."""
from pathlib import Path
import zipfile

from windows_bundle import download

BASELINE_VERSION = '0.2.0'
BASELINE_FILES = {
    'portfolio-breakdown-0.2.0-windows-x64-setup.exe':
        '7ae6c9f169956ce7ea8a852223cf400468b0df231789d0ec0a5c5fcb06db7341',
    'portfolio-breakdown-0.2.0-windows-x64.zip':
        'ed364f376d1605297306be4b739814a11377e601f325c986cb2b86d356724f87',
}


def baseline(cache: Path) -> tuple[Path, Path]:
    """Verify released bytes before unpacking or executing; never use a user's install."""
    from hashlib import file_digest
    cache.mkdir(parents=True, exist_ok=True)
    for name, expected in BASELINE_FILES.items():
        destination = cache / name
        if not destination.exists():
            temporary = destination.with_suffix(destination.suffix + '.download')
            download('https://github.com/eliaskempf/portfolio-breakdown/releases/download/'
                     f'v{BASELINE_VERSION}/{name}', temporary)
            with temporary.open('rb') as stream:
                if file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError('Previous Windows release checksum mismatch.')
            temporary.replace(destination)
        with destination.open('rb') as stream:
            if file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('Previous Windows release checksum mismatch.')
    archive = cache / f'portfolio-breakdown-{BASELINE_VERSION}-windows-x64.zip'
    # Exact pinned public archive; extract only into a new owned directory.
    extracted = cache / 'extracted'
    extracted.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as packaged:
        packaged.extractall(extracted)
    return (cache / f'portfolio-breakdown-{BASELINE_VERSION}-windows-x64-setup.exe',
            extracted / 'portfolio-app')
