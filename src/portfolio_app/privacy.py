"""Fail-closed Git-index privacy check; reports paths/reasons, never secret values."""

import argparse
from pathlib import PurePosixPath
import re
import subprocess
import sys

ROOT_FILES = {"README.md", "AGENTS.md", ".gitignore", ".python-version", "pyproject.toml", "uv.lock"}
PRIVATE_PARTS = {"data", "private", "imports", "exports", "reports", "screenshots", ".cache", ".backups", ".codex", ".agents", ".vscode", ".idea", ".streamlit", ".venv"}
SECRET_PATTERNS = (
    ("private key", re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("credential in URL", re.compile(rb"https?://[^\s/:@]+:[^\s/@]+@")),
    ("access token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|xox[baprs]-[A-Za-z0-9-]{20,})\b")),
    ("cloud access key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("bank account", re.compile(rb"\bDE[0-9]{20}\b")),
    ("secret assignment", re.compile(rb"(?im)^\s*(?:export\s+)?[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|PASSWORD|CLIENT_SECRET)[A-Z0-9_]*\s*[:=]\s*[\"']?[A-Za-z0-9_./+\-=]{12,}")),
)


def path_problem(filename: str) -> str | None:
    path = PurePosixPath(filename)
    if any(part in PRIVATE_PARTS for part in path.parts) or path.name.startswith(".env"):
        return "private data or local configuration"
    if filename in ROOT_FILES or filename == ".githooks/pre-commit":
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
                reason = content_problem(_git("show", f":{filename}"))
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
