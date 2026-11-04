"""Fresh bundle discovery must not silently audit an old runtime instead."""
from pathlib import Path

import pytest


@pytest.mark.parametrize('names', [[], ['_internal', '_internal-0123456789ab'],
                                  ['_internal-0123456789ab', '_internal-abcdef012345']])
def test_fresh_bundle_rejects_missing_or_mixed_runtimes(tmp_path, monkeypatch, names):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'tools'))
    from frozen_runtime import bundle_runtime
    for name in names:
        (tmp_path / name).mkdir()
    with pytest.raises(ValueError, match='exactly one'):
        bundle_runtime(tmp_path)


@pytest.mark.parametrize('name', ['_internal', '_internal-0123456789ab'])
def test_fresh_runtime_discovery_ignores_documentation_and_similar_files(tmp_path, monkeypatch, name):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'tools'))
    from frozen_runtime import bundle_runtime
    (tmp_path / name).mkdir()
    (tmp_path / 'documentation').mkdir()
    (tmp_path / '_internal-not-a-revision').mkdir()
    (tmp_path / '_internal-abcdef012345').write_text('Invented file, not a runtime')
    assert bundle_runtime(tmp_path) == tmp_path / name
