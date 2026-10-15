"""Durable selection per launch path, independent of portable portfolio files."""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from portfolio_app.atomic_file import staged_text

from portfolio_app.holdings import DataError
from portfolio_app.settings import state_path


@dataclass(frozen=True)
class Selection:
    directory: Path
    generation: int = 0


def selection_file(launch: Path) -> Path:
    key = sha256(os.path.normcase(str(launch.resolve())).encode()).hexdigest()[:24]
    return state_path() / 'workspaces' / (key + '.json')


def selection(launch: Path) -> Selection:
    path = selection_file(launch)
    try:
        raw = json.loads(path.read_bytes())
        if (raw['version'] != 1 or type(raw['generation']) is not int or raw['generation'] < 1
                or not isinstance(raw['directory'], str) or not Path(raw['directory']).is_absolute()):
            raise ValueError
        directory = Path(raw['directory'])
        if not directory.is_dir():
            raise ValueError
        return Selection(directory.resolve(), raw['generation'])
    except FileNotFoundError:
        return Selection(launch.resolve())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DataError('The remembered workspace is unavailable or invalid. Use --ignore-workspace-selection to open the original folder.') from exc


def save_selection(launch: Path, chosen: Selection) -> None:
    path = selection_file(launch)
    path.parent.mkdir(parents=True, exist_ok=True)
    with staged_text(path, lambda handle: json.dump(dict(version=1, directory=str(chosen.directory), generation=chosen.generation), handle), sync=True) as temporary:
        temporary.replace(path)
