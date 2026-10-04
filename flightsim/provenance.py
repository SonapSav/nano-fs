"""Which code produced a result.

`source_sha256` hashes every Python file of the flightsim package (paths and contents),
so it identifies the code exactly, with or without git (e.g. inside Docker, where .git is
not copied). The git fields link it to history when a repository is available: the
commit, whether tracked files had uncommitted changes, and a hash of those changes.
"""

import hashlib
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


@cache
def code_version() -> dict:
    """{"source_sha256", "git_commit", "git_dirty", "git_diff_sha256"}; git fields are None
    without a repository. Dirty means modified tracked files or untracked (not ignored)
    files; the diff hash covers changes to tracked files. Cached for the process."""
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain") if commit is not None else None
    diff = _git("diff", "HEAD") if commit is not None else None
    return {
        "source_sha256": source_sha256(),
        "git_commit": commit.strip() if commit else None,
        "git_dirty": bool(status.strip()) if status is not None else None,
        "git_diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if diff else None,
    }
