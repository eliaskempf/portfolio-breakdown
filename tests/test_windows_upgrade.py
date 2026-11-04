"""Previous release inputs must be verified before extraction or execution."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest


@pytest.mark.parametrize('corrupt', [None, 'setup', 'zip'])
def test_upgrade_baseline_verifies_both_downloads_before_extraction(tmp_path, monkeypatch, corrupt):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'tools'))
    import windows_upgrade
    contents = BytesIO()
    with ZipFile(contents, 'w') as archive:
        archive.writestr('portfolio-app/_internal/httptools/parser/old.pyd', b'Invented old parser')
    names = list(windows_upgrade.BASELINE_FILES)
    inputs = {names[0]: b'Invented previous Setup', names[1]: contents.getvalue()}
    monkeypatch.setattr(windows_upgrade, 'BASELINE_FILES',
                        {name: sha256(data).hexdigest() for name, data in inputs.items()})
    def download(url, target):
        name = url.rsplit('/', 1)[-1]
        data = inputs[name]
        if (corrupt == 'setup' and name.endswith('.exe')) or (corrupt == 'zip' and name.endswith('.zip')):
            data += b'Changed bytes'
        target.write_bytes(data)
    monkeypatch.setattr(windows_upgrade, 'download', download)
    if corrupt:
        with pytest.raises(ValueError, match='checksum mismatch'):
            windows_upgrade.baseline(tmp_path)
        assert not (tmp_path / 'extracted').exists()
    else:
        setup, bundle = windows_upgrade.baseline(tmp_path)
        assert setup.read_bytes() == inputs[names[0]]
        assert (bundle / '_internal/httptools/parser/old.pyd').read_bytes() == b'Invented old parser'
