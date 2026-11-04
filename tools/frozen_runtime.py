"""Locate the single runtime shipped in a newly built application bundle."""
from pathlib import Path
import re
import subprocess


def windows_runtime_name(root: Path) -> str:
    """Keep upgrades and same-version candidates out of older import directories."""
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('A full source revision is required for the Windows runtime.')
    return '_internal-' + revision[:12]


def bundle_runtime(bundle: Path) -> Path:
    """Inspect build output only; never guess among runtimes in an installation."""
    candidates = [path for path in bundle.iterdir() if path.is_dir()
                  and (path.name == '_internal' or re.fullmatch(r'_internal-[0-9a-f]{12}', path.name))]
    if len(candidates) != 1:
        raise ValueError('Expected exactly one frozen runtime in the fresh bundle.')
    return candidates[0]
