"""Installed import boundaries and source packaging after the flat-layout move."""
import ast
import importlib
from pathlib import Path
import re
import subprocess
import sys

import pytest

from portfolio_app.privacy import PRIVATE_PARTS, path_problem
from portfolio_app.settings import package_path

ROOT = Path(__file__).resolve().parents[1]


def test_move_map_accounts_for_original_modules_and_retained_paths():
    guide = (ROOT / 'docs/user/package-layout.md').read_text()
    rows = re.findall(r'^\| `(\w+\.py)` \| `([^`]+\.py)` \| (No|Adapter|Unchanged) \|$', guide, re.M)
    assert len(rows) == 284
    assert len({old for old, _, _ in rows}) == len(rows)
    assert len({new for _, new, _ in rows}) == len(rows)
    for old, new, retained in rows:
        assert package_path(new).is_file(), new
        assert path_problem('src/portfolio_app/' + new) is None
        assert package_path(old).is_file() == (retained != 'No'), old


def test_subpackage_imports_do_not_load_features_or_optional_runtimes(tmp_path):
    packages = sorted('portfolio_app.' + '.'.join(p.parent.relative_to(package_path()).parts)
                      for p in package_path().rglob('__init__.py') if p.parent != package_path())
    script = f'''
import importlib, sys
for name in {packages!r}:
    importlib.import_module(name)
assert not ({{'streamlit', 'pandas', 'yfinance', 'webview'}} & sys.modules.keys())
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('name, canonical', [
    ('app', 'runtime.app'), ('gui', 'runtime.gui'), ('window', 'runtime.window'),
    ('ui', 'interface.ui'), ('intro_preview', 'interface.views.onboarding.intro_preview'),
    ('demo_history', 'demo_data.demo_history'), ('etf_sources', 'funds.etf_sources'),
    ('vaneck', 'funds.providers.vaneck'),
])
def test_entry_adapters_share_the_canonical_function(name, canonical):
    adapter = importlib.import_module('portfolio_app.' + name)
    implementation = importlib.import_module('portfolio_app.' + canonical)
    assert adapter.main is implementation.main
    if name == 'ui':
        assert adapter.render_app is implementation.render_app


@pytest.mark.parametrize('name', ['app', 'demo_history', 'etf_sources', 'vaneck'])
def test_module_commands_work_outside_checkout(name, tmp_path):
    result = subprocess.run([sys.executable, '-m', 'portfolio_app.' + name, '--help'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout


@pytest.mark.parametrize('spec', ['portfolio.spec', 'window.spec', 'posix-window.spec'])
def test_frozen_source_collection_preserves_nested_paths_and_excludes_private_files(spec, tmp_path):
    files = ['ui.py', 'interface/ui.py', 'funds/providers/amundi.py',
             'data/private.py', 'interface/__pycache__/generated.py']
    for name in files:
        file = tmp_path / 'src/portfolio_app' / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('# Deliberately synthetic source\n')
    tree = ast.parse((ROOT / 'packaging' / spec).read_text())
    expressions = [node.value for node in tree.body
                   if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name)
                   and node.target.id == 'datas' and isinstance(node.value, ast.ListComp)
                   and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                           and call.func.attr == 'rglob' for call in ast.walk(node.value))]
    assert len(expressions) == 1
    collected = eval(compile(ast.Expression(expressions[0]), spec, 'eval'),
                     {'root': tmp_path, 'Path': Path, 'PRIVATE_PARTS': PRIVATE_PARTS})
    assert {(Path(source).relative_to(tmp_path / 'src/portfolio_app').as_posix(), Path(target).as_posix())
            for source, target in collected} == {
        ('ui.py', 'portfolio_app'), ('interface/ui.py', 'portfolio_app/interface'),
        ('funds/providers/amundi.py', 'portfolio_app/funds/providers'),
    }
