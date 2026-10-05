# Flight Simulator Project

Status and pending work: see `PROJECT.md` (read it first). This file holds goals, rules and conventions.

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
- **ML interface:** Gymnasium environment wrapping the core. RL with Stable-Baselines3 (PPO) on PyTorch from the CPU-only index (`[tool.uv.sources]` in `pyproject.toml`).
- **Logging:** Parquet, one row per timestep.
- **Viewer:** Three.js in the browser, fed over WebSocket. Chase camera plus a basic instrument panel. Served by `websockets` (HTTP + WebSocket on one port, no other deps). three.js and the Barlow Condensed font are vendored, not loaded from a CDN, so the viewer works offline; update them deliberately. The viewer may only send playback requests; it must never send anything that reaches the physics.
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
  - One row per timestep, float64 throughout (precision for fitting dynamic modes); written with zstd, float columns BYTE_STREAM_SPLIT-encoded (lossless, about half the size). Row i = state i + the command produced from it; the last row's commands are null.
  - `run_id`, `seed`, `config_hash` repeated on every row so runs concatenate trivially.
  - File metadata: schema version, run id, aircraft, aircraft hash, JSBSim version, canonical config JSON (what the hash covers), trim result, code version (`flightsim.code_version`: source hash of the flightsim package plus git commit / dirty flag / diff hash when a repository is available; see `flightsim/provenance.py`).
  - No wall-clock timestamps and a deterministic `run_id` (`<config_hash[:12]>-s<seed>`), so the same seed, config and code give byte-identical files. Local and Docker runs give identical data and the same source hash. Docker has no `.git`: images built through `scripts/docker.py` record the commit they were built from (`git_source: build`); plain `docker compose build` images have null git fields.
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
  control/          # heading hold (step 1), PID autopilot (step 3), human pilot (step 5), gain-scheduled LQR
  stream/           # protocol, frame sources (replay, live PID), HTTP + WebSocket server
  viewer/           # static Three.js app (no build step); three.js 0.186.1 + Sky addon + font vendored in viewer/vendor
                    #   terrain.js / scenery.js: procedural, seeded, visual-only scenery (airfield at 0 m = physics ground)
                    #   aircraft.js: C172P model built in code; control surfaces follow the logged positions
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
- The linearization's engine RPM row is wrong: almost no self-damping (time constant ~11 min) while the nonlinear model settles in a few seconds. Do not use `engine_rpm` as a state in linear designs (the LQR leaves it out).
- Steady wind at trim: `ic/vw-north-fps` ignores writes; set `ic/vw-mag-fps` + `ic/vw-dir-deg` (direction the air moves TOWARD) and then the ground velocity `ic/vn-fps`/`ic/ve-fps`/`ic/vd-fps` = air velocity + wind. Setting `ic/vt-fps` with wind gives a slipping, wrong-airspeed start, or a failed trim. `JSBSimCore.reset` handles this; calm resets keep the original path.
- `propulsion/set-running` resets the mixture command to full rich (other commands are kept); `JSBSimCore.reset` re-applies the requested mixture. The c172p tanks use 6.6 lb/gal fuel, so convert fuel mass (`JSBSimCore.engine()`), not JSBSim's gallon figures, when comparing with the POH (6 lb/gal).
- `atmosphere/gust-*-fps` survive `run_ic()`; `JSBSimCore.reset` zeroes them. Turbulence is ours (seeded, `flightsim/atmosphere/turbulence.py`), not JSBSim's `turb-type`.

## How to work with me (Claude Code)
- **Read `PROJECT.md` at the start of every session.** It is the single list of what is done and what is pending. Keep it current: when work completes, tick the item with the date and commit; when a new pending item or decision comes up, add it there (not in this file).
- Before adding a dependency, tell me what it is and why.
- When a number comes from memory (aerodynamic data, published stall speed, etc.), flag it so I can verify it against a source.
- Prefer small, runnable increments. After each step, run the tests and show me the output.
- If JSBSim's behavior or API differs from what's assumed here, say so and update this file.

## Decisions
- **Aircraft:** JSBSim `c172p`. Validation reference values in step 2 must come from a source matching this model.
- **Validation deviations accepted (2026-10-04):** the 4 known deviations from step 2 (stall speeds 3.4-4.7 kt fast in 3 cases, phugoid period ~21% short) are accepted for now. A tuned copy of the aircraft model is a possible later, separate step. Controllers tuned on this model should be expected to meet a slower phugoid on the real aircraft.
- **Research priority:** autopilot / control design first. The log schema should favor what control work needs: full state, control surface commands and positions, trim condition, and enough precision to fit dynamic modes. RL and pilot training come later.

## Environment (checked 2026-10-04)
- Debian 13, Python 3.13.5, `uv` available; Node 20 available for the viewer.
- 12 CPU cores, 27 GB RAM (good for the parallel batch runner).
- GPU: AMD integrated (Lucienne), no CUDA. ML training runs on CPU; fine for small RL networks.
- Decision: develop on this machine. A second PC (12 cores, NVIDIA GTX 1660 6 GB) is available for later; move only when GPU training is needed (needs NVIDIA Container Toolkit). Docker + `uv.lock` keep the move to a clone and rebuild.

## Common commands
- Local: `uv sync`, `uv run pytest`, `uv run python scripts/<script>.py`
- Validation report: `uv run python scripts/validate.py` (regenerates `docs/VALIDATION.md`)
- Controller comparison: `uv run python scripts/compare_controllers.py --episodes 100 [--log-dir data/episodes]`
- Batch: `uv run python scripts/batch_run.py --seeds 0:1000 [--policy pid|lqr|trim_hold] [--set wind.steady_speed_mps=[5,15]] [--policy-set weights.states.phi_rad=0.1] [--logs]`
- Controller comparison and RL use `configs/envs/altitude_heading_hold_comfort.yaml` (comfort penalties, structural limits, terminations charged for the remaining steps): `uv run python scripts/batch_run.py --env-config configs/envs/altitude_heading_hold_comfort.yaml --seeds 0:1000 --policy lqr`
- Seeds: tune controllers on 1000-1999. Report on 0-999 and 2000-2999 (both were used while debugging the LQR retune, 2026-10-04) and on 3000-3999 (untouched; use only for final numbers). RL training draws its episodes from its own seeded streams, never these ranges. LQR gain schedules are cached in `data/cache/lqr/` (keyed by aircraft, JSBSim version, loading, LQR config, rate and flightsim source hash).
- RL: `uv run python scripts/train_rl.py configs/rl/ppo_comfort.yaml [--set wall_clock_limit_min=5]` writes `data/rl/<run_id>/`; evaluate with `scripts/batch_run.py --policy rl --rl-model data/rl/<run_id>/best`. Long runs: start them in the background and write the console log to a file, so an interrupted session leaves the saved models and log behind.
- Batch ids hash everything that determines results, including the flightsim source hash, but not the git fields (the same code committed or not is the same batch). Manifests record the full code version.
- Logs go to `data/` (gitignored; `data/.gitkeep` is committed so Docker never creates it as root).
- Viewer: `uv run python -m flightsim.stream` then open http://localhost:8686/ ; in Docker `docker compose up -d viewer` (published on all host interfaces)
- Docker: `uv run python scripts/docker.py build` (passes the git commit into the image; any `docker compose` arguments work, e.g. `up -d --build viewer`), `docker compose run --rm sim pytest`, `docker compose run --rm sim python scripts/<script>.py`
