# Flight Simulator Project

## Goal
Build a physically accurate, simple fixed-wing flight simulator for training and research, with three uses of one physics core:
1. **Headless fast simulation** (as fast as the CPU allows) for batch experiments and ML
2. **Data logging** with a fixed, reproducible schema
3. **Real-time visuals** (3D viewer + instruments) fed by a state stream

The data logging and real-time visuals will later feed ML work, so reproducibility and a stable schema matter more than polish.

## What "simple" means here
In scope:
- One fixed-wing aircraft (Cessna 172, since its data is well documented)
- Full 6-DOF rigid-body dynamics
- Aerodynamics from coefficient tables / stability derivatives (not "lift = speed x constant")
- Standard atmosphere, basic piston engine + propeller, simple ground contact
- Wind and gust models (later, as randomized conditions)
- Fixed-timestep integration, logging, basic instrument display

Out of scope (for now):
- Real-world scenery or terrain data
- Multiplayer
- Detailed avionics or systems modeling
- Failure and damage modeling

## Architecture
One physics core with three interchangeable front-ends. The core knows nothing about graphics or wall-clock time: it takes control inputs and a timestep, and returns a state.

```
              +- Headless runner (fast) -> Gymnasium env for ML
Physics core -+- Logger (Parquet, fixed schema) -> analysis, training data
(JSBSim)      +- State stream (WebSocket) -> Three.js viewer + instruments
```

Rules:
- The viewer only consumes state. It must never influence physics.
- The same state stream format is used for live flight and for replaying logged flights.
- Keep the physics core replaceable: a custom 6-DOF model could later replace JSBSim without touching the viewer or ML layers.
- The core exposes its own state and control definitions (SI units, our names). JSBSim property names (e.g. `velocities/u-fps`) must never appear outside `core/`.

## Tech stack
- **Physics:** JSBSim via its Python bindings, pinned to `jsbsim==1.3.1` (wheels for CPython 3.10-3.14). Bundled aircraft (verified): `c172p`, `c172r`, `c172x`. Note: JSBSim prints a startup banner to stdout even at debug level 0; suppress it in the core wrapper for headless runs.
- **Language:** Python 3.14 (pinned to 3.14.3 in `.python-version` and the Docker base image). Chosen as the newest version every dependency ships wheels for; 3.15 lacks jsbsim/pyarrow/torch wheels as of 2026-10-04. Python for everything first; optimize only after profiling shows a need.
- **Environment:** `uv` manages Python and a project-local `.venv`. Dependencies live in `pyproject.toml`; `uv.lock` pins every version and is committed. Add dependencies with `uv add` (dev tools with `uv add --dev`), never `pip install`.
- **Docker from day 1:** `Dockerfile` (`python:3.14.3-slim` + uv 0.11.2) installs from `uv.lock` with `--locked`, so local and container environments are identical. `compose.yaml` mounts `./data` for logs and runs as the host user. CPU-only image for now; a separate GPU image only if ML training needs it.
- **ML interface:** Gymnasium environment wrapping the core.
- **Logging:** Parquet, one row per timestep.
- **Viewer:** Three.js in the browser, fed over WebSocket. Chase camera plus a basic instrument panel.
- **Testing:** pytest.

## Build order
Work through these in order. Finish and validate each step before starting the next.

1. **Headless run:** start in trim, hold a heading, log the data.
2. **Validate against known behavior:** trim speed, stall speed, phugoid and short-period oscillations. Compare to published Cessna 172 values and document the results.
3. **Gymnasium wrapper:** simple task (hold altitude and heading) plus a baseline PID autopilot to compare against.
4. **WebSocket stream and 3D viewer:** include replay of logged flights.
5. **Manual control:** keyboard or gamepad, also useful for collecting human demonstrations.
6. **Batch runner:** parallel simulations with randomized conditions (wind, initial states).

## Conventions
- **Reproducibility:** every run takes a seed and a saved config file. Same seed + same config must give identical logs.
  - All randomness (wind, gusts, initial-state perturbations) is generated in Python from a seeded `numpy.random.Generator` and fed into JSBSim as inputs. Do not rely on JSBSim's internal turbulence RNG unless we verify it can be seeded.
  - Every log records the JSBSim version and a hash of the aircraft definition files, alongside seed and config hash.
  - Identical logs are expected on the same machine with pinned versions; bit-identical results across platforms are not guaranteed.
- **Logging schema:** define it once, early, in a single module, and version it. Do not change column names or units casually. Record time, full state, control inputs, seed, and config hash.
- **Units:** use SI internally (meters, m/s, radians, kg). JSBSim works in imperial units (ft, slug, lbf), so all conversion happens inside the JSBSim wrapper in `core/`; everything outside `core/` is SI. The logging schema is strictly SI so swapping the physics core never changes the logs. Name variables with units where ambiguous (e.g. `alt_m`, `tas_mps`; imperial names like `alt_ft` only inside `core/`).
- **Time:** fixed timestep only. No variable dt anywhere in the physics path.
- **Config over code:** aircraft, initial conditions, wind, and task parameters live in config files (YAML or JSON), not hard-coded.
- **Tests:** physics validation checks (trim, stall speed, oscillation periods) are automated tests with stated tolerances, not one-off notebooks.
  - Reference values must name one specific aircraft variant and source (e.g. a specific 172 model-year POH). The JSBSim model will not match published data exactly, so tolerances should reflect that.
  - Phugoid and short-period are measured by perturbing from trim and fitting the decaying response. JSBSim's linearization may be an alternative; check whether the Python bindings expose it.
- **Git:** this repo commits as Panos Vasilopoulos <sonap.sav@gmail.com> (GitHub: SonapSav), set in the repo-local git config. The global git identity on this machine is a different (work) account, so don't rely on it.

## Suggested layout
```
flightsim/
  core/        # JSBSim wrapper, state/control definitions
  logging/     # schema + Parquet writer
  envs/        # Gymnasium environments
  control/     # PID baseline, manual input
  stream/      # WebSocket server
  viewer/      # Three.js app
  configs/
  tests/
  scripts/     # run_headless.py, replay.py, batch_run.py
```

## How to work with me (Claude Code)
- Start with step 1 only: scaffold the project, get a headless run working, log it, and confirm the log looks physically sensible before building further.
- Before adding a dependency, tell me what it is and why.
- When a number comes from memory (aerodynamic data, published stall speed, etc.), flag it so I can verify it against a source.
- Prefer small, runnable increments. After each step, run the tests and show me the output.
- If JSBSim's behavior or API differs from what's assumed here, say so and update this file.

## Open questions to settle early
- What kind of research comes first: autopilot / control design, RL experiments, or pilot training? This sets whether to prioritize speed, visuals, or logging detail.
- Which starting scenarios matter (cruise, takeoff, approach and landing)?

## Environment (checked 2026-10-04)
- Debian 13, Python 3.13.5, `uv` available; Node 20 available for the viewer.
- 12 CPU cores, 27 GB RAM (good for the parallel batch runner).
- GPU: AMD integrated (Lucienne), no CUDA. ML training runs on CPU; fine for small RL networks.
- Decision: develop on this machine. A second PC (12 cores, NVIDIA GTX 1660 6 GB) is available for later; move only when GPU training is needed (needs NVIDIA Container Toolkit). Docker + `uv.lock` keep the move to a clone and rebuild.

## Common commands
- Local: `uv sync`, `uv run pytest`, `uv run python scripts/<script>.py`
- Docker: `docker compose build`, `docker compose run --rm sim pytest`, `docker compose run --rm sim python scripts/<script>.py`
