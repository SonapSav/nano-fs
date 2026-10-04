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
- The viewer only consumes state; rendering must never influence physics. The one exception is deliberate: during a manual flight, pilot input (stick, pedals, throttle) enters the physics only as policy actions through the env's action interface, sampled and held at the fixed decision rate. No other client message may reach the physics.
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
- **Viewer:** Three.js in the browser, fed over WebSocket. Chase camera plus a basic instrument panel. Served by `websockets` (HTTP + WebSocket on one port, no other deps). three.js is vendored, not loaded from a CDN, so it works offline; update it deliberately. The viewer may only send playback requests; it must never send anything that reaches the physics.
- **Ports:** expose services on host port **8686** (not 8000, 3000 or other common defaults). If more ports are needed later, ask.
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
- **Logging schema:** defined once in `flightsim/datalog/schema.py` and versioned (`SCHEMA_VERSION`, currently 1). Do not change column names or units without bumping the version; `read_log` refuses other versions. Schema v1 decisions:
  - One row per timestep, float64 throughout (precision for fitting dynamic modes). Row i = state i + the command produced from it; the last row's commands are null.
  - `run_id`, `seed`, `config_hash` repeated on every row so runs concatenate trivially.
  - File metadata: schema version, run id, aircraft, aircraft hash, JSBSim version, canonical config JSON (what the hash covers), trim result.
  - No wall-clock timestamps and a deterministic `run_id` (`<config_hash[:12]>-s<seed>`), so the same seed and config give byte-identical files (verified locally vs Docker).
- **Units:** use SI internally (meters, m/s, radians, kg). JSBSim works in imperial units (ft, slug, lbf), so all conversion happens inside the JSBSim wrapper in `core/`; everything outside `core/` is SI. The logging schema is strictly SI so swapping the physics core never changes the logs. Name variables with units where ambiguous (e.g. `alt_m`, `tas_mps`; imperial names like `alt_ft` only inside `core/`).
- **Time:** fixed timestep only. No variable dt anywhere in the physics path.
- **Config over code:** aircraft, initial conditions, wind, and task parameters live in config files (YAML or JSON), not hard-coded. Variants extend a base file (`base: other.yaml`); changes go through `overrides` (dotted keys allowed) so they are part of the config hash. Never `dataclasses.replace()` a loaded config to change behaviour.
- **Controllers act through the env:** baselines and learned agents use the same action interface and decision rate (`flightsim/envs/policies.py`), so comparisons are fair. Episode logs use the same Parquet schema, one file per episode, seed = episode seed.
- **Tests:** physics validation checks (trim, stall speed, oscillation periods) are automated tests with stated tolerances, not one-off notebooks.
  - Published reference values live in `docs/REFERENCES.md` with their source; tests cite it. Primary source: 1985 Model 172P POH. Its fuel flows assume leaned mixture, so lean before comparing fuel burn.
  - Reference values must name one specific aircraft variant and source (e.g. a specific 172 model-year POH). The JSBSim model will not match published data exactly, so tolerances should reflect that.
  - Phugoid and short-period are measured by perturbing from trim and fitting the decaying response. JSBSim's linearization may be an alternative; check whether the Python bindings expose it.
- **Git:** this repo commits as Panos Vasilopoulos <sonap.sav@gmail.com> (GitHub: SonapSav), set in the repo-local git config. The global git identity on this machine is a different (work) account, so don't rely on it.

## Layout
```
flightsim/          # installable package (uv_build backend)
  core/             # JSBSim wrapper, State/Controls types (only place JSBSim is imported)
  datalog/          # schema + Parquet writer ("logging" would shadow the stdlib module)
  envs/             # Gymnasium env, its config, policy adapters (PID, trim hold), episode metrics
  control/          # heading hold (step 1), PID autopilot (step 3), human pilot policy (step 5)
  stream/           # protocol, frame sources (replay, live PID), HTTP + WebSocket server
  viewer/           # static Three.js app (no build step); three.js 0.186.1 vendored in viewer/vendor
  analysis/         # mode identification, validation maneuvers, validation checks
  atmosphere/       # Dryden turbulence (MIL-F-8785C)
  batch.py          # parallel seeded episode batches
  config.py         # YAML loading (base: inheritance, overrides) + config hash
  runner.py         # headless run loop
configs/            # YAML run configs; configs/validation/ holds reference data + tolerances
docs/               # REFERENCES.md (sources), VALIDATION.md (generated)
tests/
scripts/            # run_headless.py, replay.py, batch_run.py
```

## Sign conventions (verified against JSBSim c172p)
- Elevator command +: nose down. Aileron +: roll right. Rudder +: trailing edge left, nose LEFT (opposite of pedal intuition).
- JSBSim trim adjusts throttle, `pitch_trim`, aileron and rudder; `elevator` stays 0. Controllers must output total commands (trim value + correction).
- Body-axis accelerations in `State` are specific force (what an accelerometer reads): about -1 g on z in level flight.

## JSBSim pitfalls (verified)
- `FGLinearization` suspends integration (dt = 0) and `resume_integration()` does not undo it. Always linearize through `JSBSimCore.linearize()`, which restores dt; `step()` raises if JSBSim's clock did not advance by dt.
- A reused `FGFDMExec` is not bit-reproducible: after `run_ic()` results differ in the last bits (~1e-15) from a fresh instance. For anything that must be reproducible, build a fresh `JSBSimCore` per run/episode (about 4 ms).
- `run_ic()` keeps previous control commands. `JSBSimCore.reset()` therefore always applies a `Controls` (defaults unless given) so runs never depend on history.
- `run_ic()` does not zero JSBSim's sim time; the core counts its own steps for `t_s`.
- Model tank capacity is 185 lb each; larger loads are silently capped.
- Steady wind at trim: `ic/vw-north-fps` ignores writes; set `ic/vw-mag-fps` + `ic/vw-dir-deg` (direction the air moves TOWARD) and then the ground velocity `ic/vn-fps`/`ic/ve-fps`/`ic/vd-fps` = air velocity + wind. Setting `ic/vt-fps` with wind gives a slipping, wrong-airspeed start, or a failed trim. `JSBSimCore.reset` handles this; calm resets keep the original path.
- `atmosphere/gust-*-fps` survive `run_ic()`; `JSBSimCore.reset` zeroes them. Turbulence is ours (seeded, `flightsim/atmosphere/turbulence.py`), not JSBSim's `turb-type`.

## How to work with me (Claude Code)
- Start with step 1 only: scaffold the project, get a headless run working, log it, and confirm the log looks physically sensible before building further.
- Before adding a dependency, tell me what it is and why.
- When a number comes from memory (aerodynamic data, published stall speed, etc.), flag it so I can verify it against a source.
- Prefer small, runnable increments. After each step, run the tests and show me the output.
- If JSBSim's behavior or API differs from what's assumed here, say so and update this file.

## Decisions
- **Aircraft:** JSBSim `c172p`. Validation reference values in step 2 must come from a source matching this model.
- **Validation deviations accepted (2026-10-04):** the 4 known deviations from step 2 (stall speeds 3.4-4.7 kt fast in 3 cases, phugoid period ~21% short) are accepted for now. A tuned copy of the aircraft model is a possible later, separate step. Controllers tuned on this model should be expected to meet a slower phugoid on the real aircraft.
- **Research priority:** autopilot / control design first. The log schema should favor what control work needs: full state, control surface commands and positions, trim condition, and enough precision to fit dynamic modes. RL and pilot training come later.

## Progress
- Step 6 (batch runner + wind): done 2026-10-04. `scripts/batch_run.py` runs seeded episodes in parallel and writes `data/batch/<batch_id>/` (manifest.json, episodes.parquet summary sorted by seed, optional logs/). Results are byte-identical for any worker count (tested). Wind task `configs/envs/altitude_heading_hold_wind.yaml` (extends the calm task): steady wind 0-10 m/s from a random direction, Dryden turbulence none/light/moderate (MIL-F-8785C 3.7.2, see `docs/REFERENCES.md`). 1000-episode PID batch: no early terminations, 150 never settled within 3 deg heading (130 of them in moderate turbulence). Default workers = physical cores: on this 6-core/12-thread CPU, 12 workers are no faster than 6.
- Step 5 (manual control): done 2026-10-04. "Fly it yourself" in the viewer flies the step 3 task episode in real time (speed capped at 1x) with keyboard (arrows, Z/X, W/S, Shift = full deflection) or a standard-mapping gamepad. Input is relative to trim; stale input (>0.5 s) centres the stick and holds throttle. Flights >= 5 s are saved to `data/demos/<config>-s<seed>-m<input hash>.parquet` with `flightsim.pilot = human` metadata (schema unchanged). `AltitudeHeadingHoldEnv.refly(seed, controls)` reproduces a demonstration's logged states exactly (tested).
- Step 4 (stream + viewer): done 2026-10-04. `python -m flightsim.stream` (or `docker compose up viewer`) serves the viewer and the `/ws` stream on port 8686. Sources: replay of any log under `data/`, or a live PID episode of the step 3 task. Protocol in `flightsim/stream/protocol.py`: a frame is exactly a log schema v1 row (tested: live frames == logged rows). Viewer: Three.js chase view plus a C172 six-pack with POH airspeed/tach markings and magenta target bugs; URL params `?source=live|<log path>&seed=&speed=&autoplay=1`.
- Step 3 (Gymnasium + PID baseline): done 2026-10-04. `flightsim/AltitudeHeadingHold-v0` (`configs/envs/altitude_heading_hold.yaml`): randomized cruise start, random altitude (±150 m) and heading (±120°) targets, 120 s episodes, 20 Hz decisions, absolute commands as actions. Baseline PID autopilot (`configs/autopilot.yaml`) over 100 seeds: all episodes settle (alt within 10 m by ≤52 s, heading within 3° by ≤31 s), mean return -1317 vs -10857 for holding trim. Run `scripts/compare_controllers.py`.
- Step 2 (validation): done 2026-10-04. `scripts/validate.py` runs the checks in `configs/validation/c172p.yaml` and writes `docs/VALIDATION.md` (20 pass, 4 known deviations). Known deviations: stall speeds 3.4-4.7 kt fast in 3 of 6 cases; phugoid period 27.8 s vs ~35 s flight test (damping matches). Also qualitative: model spiral mode is stable at mid CG, the real aircraft's diverges. No independent C172 short-period measurement exists in open sources; only MIL-F-8785C limits are checked.
- Step 1 (headless run + log): done 2026-10-04. `scripts/run_headless.py` trims at 5000 ft / 100 KTAS, holds heading for 300 s, writes `data/<run_id>.parquet`.

## Open questions
- Which starting scenarios matter beyond cruise (takeoff, approach and landing)? Step 1 uses straight-and-level cruise.

## Environment (checked 2026-10-04)
- Debian 13, Python 3.13.5, `uv` available; Node 20 available for the viewer.
- 12 CPU cores, 27 GB RAM (good for the parallel batch runner).
- GPU: AMD integrated (Lucienne), no CUDA. ML training runs on CPU; fine for small RL networks.
- Decision: develop on this machine. A second PC (12 cores, NVIDIA GTX 1660 6 GB) is available for later; move only when GPU training is needed (needs NVIDIA Container Toolkit). Docker + `uv.lock` keep the move to a clone and rebuild.

## Common commands
- Local: `uv sync`, `uv run pytest`, `uv run python scripts/<script>.py`
- Validation report: `uv run python scripts/validate.py` (regenerates `docs/VALIDATION.md`)
- Controller comparison: `uv run python scripts/compare_controllers.py --episodes 100 [--log-dir data/episodes]`
- Batch: `uv run python scripts/batch_run.py --seeds 0:1000 [--policy trim_hold] [--set wind.steady_speed_mps=[5,15]] [--logs]`
- Logs go to `data/` (gitignored; `data/.gitkeep` is committed so Docker never creates it as root).
- Viewer: `uv run python -m flightsim.stream` then open http://localhost:8686/ ; in Docker `docker compose up -d viewer` (published on all host interfaces)
- Docker: `docker compose build`, `docker compose run --rm sim pytest`, `docker compose run --rm sim python scripts/<script>.py`
