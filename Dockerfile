# Python and uv versions are pinned to match .python-version and the local toolchain.
FROM python:3.14.3-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTEST_ADDOPTS="-p no:cacheprovider"

WORKDIR /app

# Dependencies first, from the lockfile only, so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# The git version the image was built from (scripts/docker.py passes it; empty otherwise).
# Last, so a new commit does not invalidate the dependency layers.
ARG GIT_COMMIT=""
ARG GIT_DIRTY=""
ARG GIT_DIFF_SHA256=""
ENV FLIGHTSIM_GIT_COMMIT=$GIT_COMMIT \
    FLIGHTSIM_GIT_DIRTY=$GIT_DIRTY \
    FLIGHTSIM_GIT_DIFF_SHA256=$GIT_DIFF_SHA256

CMD ["python", "-c", "import sys, jsbsim; print(sys.version.split()[0], 'jsbsim', jsbsim.__version__)"]
