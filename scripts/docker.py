"""Run `docker compose` with the current git version passed to image builds, so runs
inside the container record the commit they were built from (Docker images have no .git).

    uv run python scripts/docker.py build
    uv run python scripts/docker.py up -d --build viewer
    uv run python scripts/docker.py run --rm sim pytest

Any arguments go to `docker compose` unchanged. A plain `docker compose build` also
works; its images then record no git commit (only the source hash).
"""

import os
import subprocess
import sys

from flightsim.provenance import git_version


def main() -> None:
    git = git_version()
    env = dict(os.environ)
    if git["git_commit"] is not None:
        env["FLIGHTSIM_GIT_COMMIT"] = git["git_commit"]
        env["FLIGHTSIM_GIT_DIRTY"] = "1" if git["git_dirty"] else "0"
        env["FLIGHTSIM_GIT_DIFF_SHA256"] = git["git_diff_sha256"] or ""
        print(f"git {git['git_commit'][:12]}{' (dirty)' if git['git_dirty'] else ''}", file=sys.stderr)
    sys.exit(subprocess.run(["docker", "compose", *sys.argv[1:]], env=env).returncode)


if __name__ == "__main__":
    main()
