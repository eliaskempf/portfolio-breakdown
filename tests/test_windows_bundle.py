"""Installer discovery uses synthetic installations, including a PATH shim."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('windows_bundle', Path(__file__).resolve().parents[1] / 'tools/windows_bundle.py')
windows_bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windows_bundle)


@pytest.fixture
def compiler_installations(tmp_path, monkeypatch):
    monkeypatch.delenv('PORTFOLIO_ISCC', raising=False)
    monkeypatch.setenv('ProgramFiles(x86)', str(tmp_path / 'programs32'))
    monkeypatch.setenv('ProgramFiles', str(tmp_path / 'programs64'))
    shim = tmp_path / 'chocolatey' / 'bin' / 'ISCC.exe'
    shim.parent.mkdir(parents=True)
    shim.write_bytes(b'Synthetic launcher shim')
    monkeypatch.setattr(windows_bundle.shutil, 'which', lambda name: str(shim))

    def install(directory):
        compiler = tmp_path / directory / 'ISCC.exe'
        compiler.parent.mkdir(parents=True, exist_ok=True)
        compiler.write_bytes(b'Synthetic compiler')
        compiler.with_name('license.txt').write_text('Invented compiler license', encoding='utf-8')
        return compiler

    return install, shim


@pytest.mark.parametrize('directory', ['programs32/Inno Setup 6', 'programs64/Inno Setup 6'])
def test_compiler_skips_chocolatey_shim(compiler_installations, directory):
    install, shim = compiler_installations
    compiler = install(directory)
    assert windows_bundle.compiler_path() == str(compiler)
    assert not shim.with_name('license.txt').exists()


def test_compiler_accepts_real_path_installation(compiler_installations, monkeypatch):
    install, _ = compiler_installations
    compiler = install('custom-inno')
    install('programs32/Inno Setup 6')
    monkeypatch.setattr(windows_bundle.shutil, 'which', lambda name: str(compiler))
    assert windows_bundle.compiler_path() == str(compiler)


def test_explicit_compiler_requires_its_own_license(compiler_installations, monkeypatch):
    install, shim = compiler_installations
    install('programs32/Inno Setup 6')
    compiler = install('explicit-inno')
    monkeypatch.setenv('PORTFOLIO_ISCC', str(compiler))
    assert windows_bundle.compiler_path() == str(compiler)
    monkeypatch.setenv('PORTFOLIO_ISCC', str(shim))
    with pytest.raises(ValueError, match='adjacent license.txt'):
        windows_bundle.compiler_path()


def test_compiler_missing_installation_is_actionable(compiler_installations):
    with pytest.raises(ValueError, match='Set PORTFOLIO_ISCC'):
        windows_bundle.compiler_path()
