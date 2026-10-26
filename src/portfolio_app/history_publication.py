"""Recoverable publication when history application also creates positions.

The private intent file is written before either document. Until it is removed,
an interrupted publication rolls back on the next workspace access. Recovery
never overwrites a document that differs from both reviewed versions.
"""
import json
from pathlib import Path

from portfolio_app.atomic_file import staged_text
from portfolio_app.holdings import DataError

INTENT = '.history-publication.json'


def _write(path, content):
    with staged_text(path, lambda handle: handle.write(content), sync=True) as temporary:
        temporary.replace(path)


def recover_publication(directory):
    directory = Path(directory)
    intent = directory / INTENT
    if not intent.exists():
        return
    document = json.loads(intent.read_text(encoding='utf-8'))
    if set(document) != {'holdings.csv', 'account-journal.json'}:
        raise DataError('Invalid pending history publication; preserve the recovery file.')
    for name, versions in document.items():
        path = directory / name
        current = path.read_text(encoding='utf-8') if path.exists() else None
        if current not in (versions['before'], versions['after']):
            raise DataError('Portfolio changed during history recovery; preserve both versions for review.')
    for name, versions in document.items():
        path = directory / name
        if versions['before'] is None:
            path.unlink(missing_ok=True)
        else:
            _write(path, versions['before'])
    intent.unlink()


def publish_positions_and_history(directory, content, journal, holdings_revision, journal_revision):
    from portfolio_app.account_journal import journal_to_dict, save_journal
    from portfolio_app.storage import save_document, revision
    from portfolio_app.workspace_lock import workspace_lock
    directory = Path(directory)
    with workspace_lock(directory):
        if revision(directory/'holdings.csv') != holdings_revision or revision(directory/'account-journal.json') != journal_revision:
            raise DataError('Portfolio changed. Review the proposed history again.')
        documents = {'holdings.csv': content,
                     'account-journal.json': json.dumps(journal_to_dict(journal), default=str, indent=2, sort_keys=True)+'\n'}
        intent = {name: {'before': (directory/name).read_text(encoding='utf-8') if (directory/name).exists() else None,
                         'after': value} for name, value in documents.items()}
        _write(directory/INTENT, json.dumps(intent))
        try:
            save_document(directory/'holdings.csv', content, holdings_revision)
            save_journal(directory, journal, journal_revision)
            (directory/INTENT).unlink()
        except BaseException:
            recover_publication(directory)
            raise
