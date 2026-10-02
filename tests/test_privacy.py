from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from portfolio_app.demo import create_demo_data
from portfolio_app.holdings import DataError
from portfolio_app.privacy import ICON_FILES, check_index, content_problem, path_problem

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    shutil.copy(REPO / ".gitignore", tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("filename", [
    "data/holdings.csv", "data/classifications.yaml", "data/.backups/snapshot.csv",
    "data/.cache/prices.json", "custom/holdings.csv", "custom/targets.yaml",
    "exports/report.pdf", "reports/allocation.png", "account.xlsx", ".env",
    ".env.local", ".streamlit/secrets.toml", ".codex/config.toml", "handoff.md",
    "notes/credentials.json", "private/notes.py", "src/private/account.py",
])
def test_private_paths_are_blocked(filename):
    assert path_problem(filename) is not None


@pytest.mark.parametrize("filename", [
    "README.md", "AGENTS.md", "pyproject.toml", "uv.lock", ".gitignore", ".python-version",
    "src/portfolio_app/holdings.py", "tests/test_holdings.py", ".githooks/pre-commit",
    "LICENSE", "docs/release-plan.md", ".github/workflows/candidate.yml",
    *sorted(ICON_FILES), "packaging/portfolio.spec",
])
def test_public_sources_are_allowed(filename):
    assert path_problem(filename) is None


@pytest.mark.parametrize('filename', ['.github/workflows/private.yml', 'docs/portfolio.json',
                                    'src/portfolio_app/assets/screenshot.png', 'packaging/holdings.csv'])
def test_release_exceptions_do_not_allow_arbitrary_data(filename):
    assert path_problem(filename)


@pytest.mark.parametrize('filename', sorted(ICON_FILES))
def test_supplied_artwork_can_be_staged_and_checked(git_repo, filename):
    target = git_repo / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / filename, target)
    subprocess.run(['git', 'add', filename], check=True)
    assert check_index() == []
    target.write_bytes(b'not an image')
    subprocess.run(['git', 'add', filename], check=True)
    assert check_index()


@pytest.mark.parametrize("filename", [
    "data/holdings.csv", "data/classifications.yaml", "data/.backups/old.csv",
    "elsewhere/holdings.csv", "elsewhere/targets.yaml", "elsewhere/prices.json",
    "statement.pdf", "allocation.png", ".env.production", ".codex/config.toml",
    ".streamlit/secrets.toml", "handoff.md",
])
def test_gitignore_covers_private_paths(git_repo, filename):
    result = subprocess.run(["git", "check-ignore", "--no-index", filename], capture_output=True)
    assert result.returncode == 0


def test_force_added_private_data_is_rejected(git_repo):
    path = git_repo / "data" / "holdings.csv"
    path.parent.mkdir()
    path.write_text("id,name,shares\nfictional,Invented test asset,1\n")
    subprocess.run(["git", "add", "-f", "data/holdings.csv"], check=True)
    assert check_index() == [("data/holdings.csv", "private data or local configuration")]
    assert check_index(tracked=True) == check_index()


def test_staged_secret_is_detected_even_when_working_copy_is_clean(git_repo):
    path = git_repo / "README.md"
    # Intentionally fake, constructed at runtime so no credential-shaped literal is shipped.
    secret = "ghp_" + "A" * 36
    path.write_text(secret)
    subprocess.run(["git", "add", "README.md"], check=True)
    path.write_text("Clean working copy")
    assert check_index() == [("README.md", "access token")]
    result = subprocess.run([sys.executable, str(REPO / "src/portfolio_app/privacy.py"), "--staged"], capture_output=True, text=True)
    assert result.returncode == 1
    assert secret not in result.stderr


def test_staged_symlink_to_private_file_is_rejected(git_repo):
    (git_repo / "private").mkdir()
    (git_repo / "private" / "account").write_text("fictional private fixture")
    (git_repo / "README.md").symlink_to("private/account")
    subprocess.run(["git", "add", "README.md"], check=True)
    assert "symlinks" in check_index()[0][1]


def test_public_staged_content_passes(git_repo):
    (git_repo / "README.md").write_text("A public portfolio-analysis package.")
    subprocess.run(["git", "add", "README.md"], check=True)
    assert check_index() == []


@pytest.mark.parametrize("content", [
    ("-----BEGIN " + "PRIVATE KEY-----").encode(),
    ("https://example:" + "fake-password@example.invalid").encode(),
    ("AKIA" + "Z" * 16).encode(),
    ("DE" + "1" * 20).encode(),
    ("API_KEY=" + "invented_test_value").encode(),
])
def test_recognizable_private_content_is_rejected(content):
    assert content_problem(content) is not None


def test_real_hook_blocks_force_staged_data(git_repo):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv must be on PATH for the actual Git hook integration test")
    (git_repo / ".githooks").mkdir()
    shutil.copy(REPO / ".githooks/pre-commit", git_repo / ".githooks/pre-commit")
    script_dir = git_repo / "src/portfolio_app"
    script_dir.mkdir(parents=True)
    shutil.copy(REPO / "src/portfolio_app/privacy.py", script_dir)
    shutil.copy(REPO / "pyproject.toml", git_repo)
    shutil.copy(REPO / "uv.lock", git_repo)
    (git_repo / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    subprocess.run(["git", "config", "portfolio.uvPath", uv], check=True)
    (git_repo / "README.md").write_text("Public documentation")
    subprocess.run(["git", "add", "README.md"], check=True)
    hook = ["sh", str(git_repo / ".githooks/pre-commit")]
    assert subprocess.run(hook, capture_output=True).returncode == 0
    (git_repo / "holdings.csv").write_text("id,name,shares\nexample,Synthetic,1\n")
    subprocess.run(["git", "add", "-f", "holdings.csv"], check=True)
    result = subprocess.run(hook, capture_output=True, text=True)
    assert result.returncode == 1
    assert "holdings.csv" in result.stderr


def test_demo_never_overwrites_an_existing_directory(tmp_path):
    sentinel = tmp_path / "holdings.csv"
    sentinel.write_text("Existing file must remain untouched")
    with pytest.raises(DataError, match="not empty"):
        create_demo_data(tmp_path)
    assert sentinel.read_text() == "Existing file must remain untouched"


def test_demo_is_explicit_and_idempotent(tmp_path):
    create_demo_data(tmp_path)
    original = (tmp_path / "holdings.csv").read_bytes()
    create_demo_data(tmp_path)
    assert (tmp_path / "holdings.csv").read_bytes() == original
    assert (tmp_path / ".synthetic-demo").exists()
