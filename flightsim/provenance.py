"""Which code produced a result.

`source_sha256` hashes every Python file of the flightsim package (paths and contents),
so it identifies the code exactly, with or without git (e.g. inside Docker, where .git is
not copied). The git fields link it to history: the commit, whether there were
uncommitted changes, and a hash of those changes. They come from the repository when
one is available ("git_source": "repository"), otherwise from FLIGHTSIM_GIT_* variables
recorded when a Docker image was built through scripts/docker.py ("build").
"""

import hashlib
import os
import subprocess
from functools import cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def source_sha256(root: Path = PACKAGE_DIR) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
    return h.hexdigest()


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=PACKAGE_DIR, capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


def git_version() -> dict:
    """Git fields from the repository, or all None without one."""
    commit = _git("rev-parse", "HEAD")
    if commit is None:
        return {"git_commit": None, "git_dirty": None, "git_diff_sha256": None, "git_source": None}
    status = _git("status", "--porcelain")
    diff = _git("diff", "HEAD")
    return {
        "git_commit": commit.strip(),
        "git_dirty": bool(status.strip()) if status is not None else None,
        "git_diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if diff else None,
        "git_source": "repository",
    }


def _build_time_git() -> dict | None:
    commit = os.environ.get("FLIGHTSIM_GIT_COMMIT")
    if not commit:
        return None
    return {
        "git_commit": commit,
        "git_dirty": os.environ.get("FLIGHTSIM_GIT_DIRTY") == "1",
        "git_diff_sha256": os.environ.get("FLIGHTSIM_GIT_DIFF_SHA256") or None,
        "git_source": "build",
    }


@cache
def code_version() -> dict:
    """{"source_sha256", "git_commit", "git_dirty", "git_diff_sha256", "git_source"}; git
    fields are None without a repository or build-time record. Dirty means modified
    tracked files or untracked (not ignored) files; the diff hash covers changes to
    tracked files. Cached for the process."""
    git = git_version()
    if git["git_commit"] is None:
        git = _build_time_git() or git
    return {"source_sha256": source_sha256(), **git}
