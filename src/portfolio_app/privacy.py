"""Fail-closed Git-index privacy check; reports paths/reasons, never secret values."""

import argparse
from pathlib import PurePosixPath
import re
import subprocess
import sys

ROOT_FILES = {"README.md", "AGENTS.md", ".gitignore", ".python-version", "pyproject.toml", "uv.lock"}
RELEASE_FILES = {"packaging/posix-window.spec", "tools/desktop_build.py", "tools/desktop_smoke.py",
                 "tools/desktop_gatekeeper.py", "tools/desktop_session.py",
                 ".github/workflows/desktop-experiment.yml", "docs/desktop-experiment.md","LICENSE", "docs/release-plan.md", "docs/release-checklist.md", "docs/install.md",
                 ".github/workflows/ci.yml", ".github/workflows/candidate.yml",
                 ".github/workflows/publish.yml", ".github/workflows/maintenance.yml",
                 ".github/dependabot.yml", "packaging/portfolio.spec", "packaging/entrypoint.py",
                 "tools/release.py", "tools/promote.py", "tools/package_smoke.py",
                 "packaging/window.spec", "packaging/window_entrypoint.py",
                 "tools/window_build.py", "tools/window_smoke.py", "tools/windows_bundle.py", "packaging/windows.iss", "docs/window-session-result.md",
                 "src/portfolio_app/documentation-build.json",
                 "docs/documentation-session-handoff.md", "docs/window-session-handoff.md",
                 "docs/startup-development.md", "src/portfolio_app/intro_frontend/index.html"}
ICON_FILES = {"src/portfolio_app/assets/portfolio-breakdown.png", "src/portfolio_app/assets/portfolio-breakdown.svg",
              "src/portfolio_app/assets/favicon.svg", "src/portfolio_app/assets/favicon.ico",
              "docs/assets/readme-header.svg"}
# This exact public demo capture is reviewed separately; arbitrary screenshots
# and images remain blocked. Never populate it from a personal workspace.
DEMO_IMAGE_FILES = {'docs/assets/demo-overview.png'}
DOCS_FILES = {"mkdocs.yml", ".github/workflows/docs.yml", ".github/workflows/docs-pages.yml",
              "tools/docs_site.py", "tools/docs_preview.py", "tools/docs_candidate.py", "docs/documentation-maintenance.md",
              "docs/documentation-migration.md", "docs/documentation-session-result.md"}
PRIVATE_PARTS = {"data", "private", "imports", "exports", "reports", "screenshots", ".cache", ".backups", ".codex", ".agents", ".vscode", ".idea", ".streamlit", ".venv"}
SECRET_PATTERNS = (
    ("private key", re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("credential in URL", re.compile(rb"https?://[^\s/:@]+:[^\s/@]+@")),
    ("access token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|xox[baprs]-[A-Za-z0-9-]{20,})\b")),
    ("cloud access key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("bank account", re.compile(rb"\bDE[0-9]{20}\b")),
    ("local user-directory path", re.compile(rb"/(?:home|Users)/[A-Za-z0-9_.-]+|[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9_.-]+")),
    ("secret assignment", re.compile(rb"(?im)^\s*(?:export\s+)?[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|PASSWORD|CLIENT_SECRET)[A-Z0-9_]*\s*[:=]\s*[\"']?[A-Za-z0-9_./+\-=]{12,}")),
)


def path_problem(filename: str) -> str | None:
    path = PurePosixPath(filename)
    if any(part in PRIVATE_PARTS for part in path.parts) or path.name.startswith(".env"):
        return "private data or local configuration"
    if filename in ROOT_FILES | RELEASE_FILES | ICON_FILES | DEMO_IMAGE_FILES | DOCS_FILES or filename == ".githooks/pre-commit":
        return None
    if path.parts[:2] == ("docs", "user") and path.suffix == ".md":
        return None
    if len(path.parts) >= 2 and path.parts[0] in {"src", "tests"} and path.suffix == ".py":
        return None
    return "not an approved source/documentation path; data and exports must stay private"


def content_problem(content: bytes) -> str | None:
    if b"\0" in content:
        return "binary content in a source/documentation file"
    for description, pattern in SECRET_PATTERNS:
        if pattern.search(content):
            return description
    return None


def icon_problem(filename: str, content: bytes) -> str | None:
    if len(content) > 5_000_000:
        return 'oversized approved icon'
    if filename.endswith('.svg'):
        from xml.etree import ElementTree
        reason = content_problem(content)
        if reason:
            return reason
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            return 'invalid SVG icon'
        return None if root.tag == '{http://www.w3.org/2000/svg}svg' else 'invalid SVG icon'
    signature = b'\x89PNG\r\n\x1a\n' if filename.endswith('.png') else b'\x00\x00\x01\x00'
    return None if content.startswith(signature) else 'invalid approved icon'


def _git(*arguments: str, input: bytes | None = None) -> bytes:
    return subprocess.run(["git", *arguments], input=input, capture_output=True, check=True).stdout


def check_index(*, tracked: bool = False) -> list[tuple[str, str]]:
    names = _git("ls-files", "-z") if tracked else _git("diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z")
    filenames = [name.decode("utf-8", errors="surrogateescape") for name in names.split(b"\0") if name]
    issues = []
    for filename in filenames:
        reason = path_problem(filename)
        if reason is None:
            # Read the staged blob, not the possibly cleaner working copy.
            entry = _git("ls-files", "--stage", "-z", "--", filename).split(b" ", 1)[0]
            if entry != b"100644" and entry != b"100755":
                reason = "symlinks, submodules, and unresolved index entries are not allowed"
            else:
                content = _git("show", f":{filename}")
                if filename in ICON_FILES | DEMO_IMAGE_FILES:
                    reason = icon_problem(filename, content)
                else:
                    reason = content_problem(content)
        if reason:
            issues.append((filename, reason))
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Reject private/runtime data and recognizable credentials in Git's index")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="Check staged additions/changes (default)")
    mode.add_argument("--tracked", action="store_true", help="Audit every path in the current Git index")
    args = parser.parse_args()
    try:
        issues = check_index(tracked=args.tracked)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Privacy check could not complete ({type(exc).__name__}); refusing the commit.", file=sys.stderr)
        raise SystemExit(1) from None
    if issues:
        for filename, reason in issues:
            print(f"Blocked: {filename}: {reason}", file=sys.stderr)
        print("Unstage these files and keep private information out of source and documentation. Do not bypass the hook.", file=sys.stderr)
        raise SystemExit(1)
    print("Privacy check passed: no prohibited paths or recognizable credentials in the checked index entries.")


if __name__ == "__main__":
    main()
