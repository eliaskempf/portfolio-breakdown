"""Synthetic recovery fixtures, with exact on-disk newline representations."""
import json

import pytest

from portfolio_app.account_journal import Journal, save_journal
from portfolio_app.history_publication import INTENT, publish_positions_and_history, recover_publication
from portfolio_app.holdings import DataError
from portfolio_app.storage import revision


@pytest.mark.parametrize('before_newline', ['\n', '\r\n'])
@pytest.mark.parametrize('after_newline', ['\n', '\r\n'])
@pytest.mark.parametrize('stage', ['before_journal', 'after_journal'])
def test_interruption_restores_exact_original_bytes(tmp_path, monkeypatch, before_newline, after_newline, stage):
    import portfolio_app.account_journal as journals
    holdings = tmp_path/'holdings.csv'
    holdings.write_bytes(('id,name,shares\nold,Invented,2\n'.replace('\n', before_newline)).encode())
    save_journal(tmp_path, Journal(), None)
    journal = tmp_path/'account-journal.json'
    journal.write_bytes(journal.read_bytes().replace(b'\n', before_newline.encode()))
    originals = {p: p.read_bytes() for p in (holdings, journal)}
    def fail(*args, **kwargs):
        if stage == 'after_journal':
            save_journal(*args, **kwargs)
        raise OSError('Invented interruption')
    monkeypatch.setattr(journals, 'save_journal', fail)
    content = 'id,name,shares\nnew,Invented,3\n'.replace('\n', after_newline)
    with pytest.raises(OSError, match='Invented interruption'):
        publish_positions_and_history(tmp_path, content, Journal(version=5), revision(holdings), revision(journal))
    assert {p: p.read_bytes() for p in originals} == originals
    assert not (tmp_path/INTENT).exists()
    recover_publication(tmp_path)


@pytest.mark.parametrize('unexpected', ['id,name,shares\nother,Invented,9\n', 'id,name,shares\r\nnew,Invented,3\r\n'])
def test_recovery_refuses_external_edits_including_newline_changes(tmp_path, unexpected):
    after = 'id,name,shares\nnew,Invented,3\n'
    (tmp_path/'holdings.csv').write_bytes(unexpected.encode())
    intent = {'version': 2, 'documents': {
        'holdings.csv': {'before': None, 'after': after},
        'account-journal.json': {'before': None, 'after': '{}\n'},
    }}
    (tmp_path/INTENT).write_text(json.dumps(intent))
    with pytest.raises(DataError, match='changed during history recovery'):
        recover_publication(tmp_path)
    assert (tmp_path/'holdings.csv').read_bytes() == unexpected.encode()
    assert (tmp_path/INTENT).exists()


def test_legacy_windows_intent_recovers_translated_crlf(tmp_path, monkeypatch):
    import portfolio_app.history_publication as publication
    monkeypatch.setattr(publication.os, 'linesep', '\r\n')
    after = 'id,name,shares\r\nnew,Invented,3\r\n'
    (tmp_path/'holdings.csv').write_bytes(after.replace('\n', '\r\n').encode())
    (tmp_path/INTENT).write_text(json.dumps({
        'holdings.csv': {'before': None, 'after': after},
        'account-journal.json': {'before': None, 'after': '{}\n'},
    }))
    recover_publication(tmp_path)
    assert not (tmp_path/'holdings.csv').exists()
    assert not (tmp_path/INTENT).exists()


def test_legacy_intent_preserves_unmodified_crlf_original(tmp_path):
    before = 'id,name,shares\nold,Invented,2\n'
    original = before.replace('\n', '\r\n').encode()
    (tmp_path/'holdings.csv').write_bytes(original)
    (tmp_path/INTENT).write_text(json.dumps({
        'holdings.csv': {'before': before, 'after': 'id,name,shares\nnew,Invented,3\n'},
        'account-journal.json': {'before': None, 'after': '{}\n'},
    }))
    recover_publication(tmp_path)
    assert (tmp_path/'holdings.csv').read_bytes() == original
    assert not (tmp_path/INTENT).exists()
