"""Revision-checked private document writes shared by allocation and balance editors."""
from hashlib import sha256
import os
from pathlib import Path
from portfolio_app.workspace_lock import document_write
from tempfile import NamedTemporaryFile
from uuid import uuid4

from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock as _write_lock


def revision(path: Path) -> str | None:
    return sha256(path.read_bytes()).hexdigest() if path.exists() else None


@document_write
def save_document(path: Path, content: str, expected_revision: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock(path.with_name(f'.{path.name}.lock')):
        original = path.read_bytes() if path.exists() else None
        if (sha256(original).hexdigest() if original is not None else None) != expected_revision:
            raise DataError('Data changed since this editor was opened. Reload before saving.')
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if original is not None:
                backups = path.parent / '.backups'
                backups.mkdir(exist_ok=True)
                (backups / f'{path.stem}-{uuid4().hex}{path.suffix}').write_bytes(original)
            if (path.read_bytes() if path.exists() else None) != original:
                raise DataError('Data changed during save. Reload before saving.')
            temporary.replace(path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
