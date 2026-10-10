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
- Real-world scenery from open data, fully offline (decided 2026-10-09; see Decisions)

Out of scope (for now):
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
- **Logging schema:** defined once in `flightsim/datalog/schema.py` and versioned (`SCHEMA_VERSION`, currently 2: v1 plus `cmd_brake_norm`). Do not change column names or units without bumping the version; `read_log` reads the versions in `READABLE_VERSIONS` (v1 logs come back with brake 0) and refuses others. Schema decisions (v1):
  - One row per timestep, float64 throughout (precision for fitting dynamic modes); written with zstd, float columns BYTE_STREAM_SPLIT-encoded (lossless, about half the size). Row i = state i + the command produced from it; the last row's commands are null.
  - `run_id`, `seed`, `config_hash` repeated on every row so runs concatenate trivially.
  - File metadata: schema version, run id, aircraft, aircraft hash, JSBSim version, canonical config JSON (what the hash covers), trim result, optional pilot (`human` for demonstrations) and pilot aids (demonstrations: when the viewer's HUD was in view, `flightsim.pilot_aids`), the belly camera's pointing (demonstrations: `flightsim.camera`, visual only), the real-world scenery region flown over (`flightsim.scenery`: name and manifest hash), code version (`flightsim.code_version`: source hash of the flightsim package plus git commit / dirty flag / diff hash when a repository is available; see `flightsim/provenance.py`).
  - No wall-clock timestamps and a deterministic `run_id` (`<config_hash[:12]>-s<seed>`), so the same seed, config and code give byte-identical files. Local and Docker runs give identical data and the same source hash. Docker has no `.git`: images built through `scripts/docker.py` record the commit they were built from (`git_source: build`); plain `docker compose build` images have null git fields.
- **Units:** use SI internally (meters, m/s, radians, kg). JSBSim works in imperial units (ft, slug, lbf), so all conversion happens inside the JSBSim wrapper in `core/`; everything outside `core/` is SI. The logging schema is strictly SI so swapping the physics core never changes the logs. Name variables with units where ambiguous (e.g. `alt_m`, `tas_mps`; imperial names like `alt_ft` only inside `core/`).
- **Time:** fixed timestep only. No variable dt anywhere in the physics path.
- **Positions:** JSBSim integrates latitude/longitude on WGS84; tasks, terrain and the viewer work on a flat map in metres north/east of the world's origin plus height above mean sea level (JSBSim's altitude; real terrain elevations are MSL on the EGM2008 geoid, and the geoid-ellipsoid difference is ignored: it changes by only metres across a region). Convert only through `flightsim/world/geo.py` (`Geodesy` from the env config's `world` block; `viewer/geo.js` in the browser), never with an Earth radius by hand. Configs without a `world` block (all logs before 2026-10-08) use the original 6371 km sphere, so old logs replay and re-fly exactly. Headings, wind directions and velocities are true; map bearing = true bearing - grid convergence (zero at the default origin 0, 0).
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
  aircraft/         # project aircraft (loaded before JSBSim's): c172p_tuned = c172p with a tuned propeller and brakes
  datalog/          # schema + Parquet writer ("logging" would shadow the stdlib module)
  envs/             # Gymnasium envs (altitude/heading hold; approach and landing; takeoff; circuit; runway.py shared), config, policy adapters, metrics
  control/          # heading hold (step 1), PID autopilot (step 3), human pilot (step 5), gain-scheduled LQR, approach, takeoff and circuit autopilots
  stream/           # protocol, frame sources (replay, live PID), HTTP + WebSocket server
  viewer/           # static Three.js app (no build step); three.js 0.186.1 + Sky addon + font vendored in viewer/vendor
                    #   terrain.js / scenery.js: procedural, seeded, visual-only scenery (airfield at 0 m = physics ground);
                    #     terrainCore.js: height and tile data without three.js (also run in terrainWorker.js)
                    #   aircraft.js: C172P model built in code; control surfaces follow the logged positions
                    #   pattern.js: circuit traffic pattern drawing (from the stream's `pattern`)
                    #   sky.js / clouds.js: time of day, visibility, seeded clouds (from the stream's `visual`)
                    #   groundDetail.js: close-up ground texture (shader noise)
                    #   panel.js / panel.html: instrument panel, also in its own window (BroadcastChannel mirror)
                    #   hud.js: optional head-up display in the cockpit view (conformal, fixed to the aircraft)
                    #   camera.js / camsink.js / camera.html: stabilized belly camera (rendered here, shown in the
                    #     inset, the camera window and the map window; pointing recorded with demonstrations)
                    #   geo.js / gps.js: WGS84 map (port of world/geo.py); GPS unit in the panel
                    #   map.js / map.html: moving map in its own window (same channel as the instruments window)
                    #   sound.js: synthesized engine, wind, stall horn, flap motor (Web Audio, driven by frames)
                    #   world.js: the flight's world (procedural or a real-world region) for every part of the viewer;
                    #     demCore.js (bit-identical to world/dem.py), demTiles.js, featureGeometry.js (region tiles, OSM
                    #     roads, paving and buildings, in the tile worker), realAirfields.js / runwayGeometry.js (its
                    #     runways), mapRegion.js (its map tiles), landmarks.js / landmarkKit.js (landmark models,
                    #     their materials and sun shadows; ?debug in the URL exposes the scene for screenshots),
                    #     staticMerge.js (static objects baked into one mesh per material: fewer draw calls),
                    #     bridges.js (every OSM bridge's deck and piers, the landmark bridges' arches and girders)
                    #     imageryClip.js (a region's 1 m / 4 m imagery streamed around the camera: two fixed clipmap textures)
                    #     groundTextures.js (close-up sand, paving and plant detail blended into a region's imagery)
                    #     sunShadows.js (a region's sun shadows: one map around the camera, drawn only when it moves)
                    #   haze.js: height-aware haze in every material (replaces three's fog chunks)
                    #   nightLights.js: night level (from sky.js times dusk / night), light-point glows, lit surfaces
  analysis/         # mode identification, validation maneuvers, validation checks
  atmosphere/       # Dryden turbulence (MIL-F-8785C)
  world/            # terrain height shared with the viewer (bit-identical port of viewer/terrainCore.js);
                    #   geo.py: WGS84 latitude/longitude <-> map metres around the world's origin (viewer/geo.js the same)
                    #   scenery.py: built real-world regions (tile grid, files, manifest); scenery_build.py: the build
                    #   dem.py: a region's height and water (viewer/demCore.js the same); ground.py: the ground of a config
                    #   scenery_osm.py: the build's OpenStreetMap runways and features; scenery_bridges.py: bridge decks
                    #   scenery_hires.py: the build's 1 m imagery (Satellogic EarthView), colour-matched to Sentinel-2
                    #   scenery_colours.py: building roof colours from the imagery (in the features files)
  batch.py          # parallel seeded episode batches
  config.py         # YAML loading (base: inheritance, overrides) + config hash
  runner.py         # headless run loop
configs/            # YAML run configs; configs/validation/ holds reference data + tolerances; configs/scenery/ real-world regions
docs/               # REFERENCES.md (sources), VALIDATION.md (generated)
tests/
scripts/            # run_headless.py, replay.py, batch_run.py
```

## Sign conventions (verified against JSBSim c172p)
- Elevator command +: nose down. Aileron +: roll right. Rudder +: trailing edge left, nose LEFT (opposite of pedal intuition); the rudder command also steers the nosewheel the same way. Brake: 0-1, both main wheels.
- JSBSim trim adjusts throttle, `pitch_trim`, aileron and rudder; `elevator` stays 0. Controllers must output total commands (trim value + correction).
- Body-axis accelerations in `State` are specific force (what an accelerometer reads): about -1 g on z in level flight.

## JSBSim pitfalls (verified)
- `FGLinearization` suspends integration (dt = 0) and `resume_integration()` does not undo it. Always linearize through `JSBSimCore.linearize()`, which restores dt; `step()` raises if JSBSim's clock did not advance by dt.
- A reused `FGFDMExec` is not bit-reproducible: after `run_ic()` results differ in the last bits (~1e-15) from a fresh instance. For anything that must be reproducible, build a fresh `JSBSimCore` per run/episode (about 4 ms).
- `run_ic()` keeps previous control commands. `JSBSimCore.reset()` therefore always applies a `Controls` (defaults unless given) so runs never depend on history.
- `run_ic()` does not zero JSBSim's sim time; the core counts its own steps for `t_s`.
- Model tank capacity is 185 lb each; larger loads are silently capped.
- The linearization's engine RPM row is wrong: almost no self-damping (time constant ~11 min) while the nonlinear model settles in a few seconds. Do not use `engine_rpm` as a state in linear designs (the LQR leaves it out).
- JSBSim's trim in wind fails for strong headwinds at approach speeds (wind above ~0.75 x airspeed in the descent; its alpha iteration does not settle). The approach task trims in calm air and then calls `JSBSimCore.add_steady_wind` on the same core, which re-runs the IC with the same air-relative state and keeps the engine's trimmed RPM (a fresh core would restart the engine at the wrong RPM; the RPM properties are read-only).
- Steady wind at trim: `ic/vw-north-fps` ignores writes; set `ic/vw-mag-fps` + `ic/vw-dir-deg` (direction the air moves TOWARD) and then the ground velocity `ic/vn-fps`/`ic/ve-fps`/`ic/vd-fps` = air velocity + wind. Setting `ic/vt-fps` with wind gives a slipping, wrong-airspeed start, or a failed trim. `JSBSimCore.reset` handles this; calm resets keep the original path.
- `propulsion/set-running` resets the mixture command to full rich (other commands are kept); `JSBSimCore.reset` re-applies the requested mixture. The c172p tanks use 6.6 lb/gal fuel, so convert fuel mass (`JSBSimCore.engine()`), not JSBSim's gallon figures, when comparing with the POH (6 lb/gal).
- The c172p model does not link nosewheel steering to the rudder (`fcs/steer-cmd-norm` stays 0, so ground handling is aerodynamic rudder only). The core sets steer = -rudder (steer +1 = nose right, 10 deg) in `_apply`.
- `propulsion/set-running` starts the engine at ~2470 RPM whatever the throttle; it takes seconds to spin down. `JSBSimCore.reset_on_ground` sits with brakes set and throttle closed (`settle_s`), then for a rolling start re-runs the IC (the engine state survives `run_ic()`). JSBSim's ground trim (`simulation/do_simple_trim = 2`) fails at speed with the brakes set, with non-neutral stick/pedals, and in a crosswind. A crosswind switched on at once (re-run IC with `ic/vw-*`) jolts the parked aircraft, and with the brakes fully set a crosswind rocks it in pitch until it sits on its tail (tumbles if left long enough); `reset_on_ground` settles in calm air on full brakes, then ramps `atmosphere/wind-north/east-fps` up over 5 s with the brakes at 0.3. At breakaway (first metres of a takeoff roll in a crosswind) the tyre friction can still kick the nose up (about 1 in 1000 crosswind takeoffs strikes the tail; no control input helps at ~10 kt).
- The c172p tail skid touches at 10.3 deg pitch on the main wheels (contact geometry); takeoff techniques must stay below it. The model's full-throttle RPM exceeds the 2700 redline in the climb (see the ground checks in `docs/VALIDATION.md`).
- `atmosphere/gust-*-fps` survive `run_ic()`; `JSBSimCore.reset` zeroes them.
- Non-standard days: `atmosphere/delta-T` (Rankine, at every altitude; the pressure profile follows the shifted temperature) and `atmosphere/P-sl-psf` survive `run_ic()`; set them with `JSBSimCore.set_atmosphere` before `reset` (an env config's `atmosphere: {sea_level_temperature_c, sea_level_pressure_hpa}`). Indicated-to-true airspeed conversions use the day's density (`Atmosphere.density_ratio`, matches JSBSim), not the ISA's.
- Terrain: JSBSim's default ground is a level plane at `position/terrain-elevation-asl-ft` (the gear and `h-agl-ft` follow it, also when changed mid-run). Envs with `terrain: procedural` or `terrain: dem` (a built real-world region, `world.scenery`) set it every step from `flightsim/world/ground.py` at the aircraft's position (slopes under the gear are ignored); over a region's water (WorldCover class 80) touching the surface ends the episode ("water"); `JSBSimCore.reset(..., ground_elevation_m=)` sets it for the start. Turbulence is ours (seeded, `flightsim/atmosphere/turbulence.py`), not JSBSim's `turb-type`.

## How to work with me (Claude Code)
- **Read `PROJECT.md` at the start of every session.** It is the single list of what is done and what is pending. Keep it current: when work completes, tick the item with the date and commit; when a new pending item or decision comes up, add it there (not in this file).
- Before adding a dependency, tell me what it is and why.
- When a number comes from memory (aerodynamic data, published stall speed, etc.), flag it so I can verify it against a source.
- Prefer small, runnable increments. After each step, run the tests and show me the output.
- If JSBSim's behavior or API differs from what's assumed here, say so and update this file.

## Decisions
- **Aircraft:** JSBSim `c172p`. Validation reference values in step 2 must come from a source matching this model.
- **Validation deviations accepted (2026-10-04):** the 4 known deviations from step 2 (stall speeds 3.4-4.7 kt fast in 3 cases, phugoid period ~21% short) are accepted for now. A tuned copy of the aircraft model is a possible later, separate step. Controllers tuned on this model should be expected to meet a slower phugoid on the real aircraft.
- **Real-world scenery (2026-10-09):** option B, built offline from open data for one region at a time, non-commercial for now, nothing fetched while flying. First region: about 100 x 100 km around Al Bateen Executive Airport (OMAD), Abu Dhabi. Ground from FABDEM (bare-earth Copernicus GLO-30, buildings and trees removed; non-commercial licence), land cover from ESA WorldCover (CC BY 4.0), features and buildings from OpenStreetMap (ODbL, Geofabrik extracts). The same height tiles feed the physics and the viewer (bit-identical, as the procedural terrain); logs record the scenery's hash; `procedural` stays. Downloads and built tiles live in `data/scenery/` (never committed: the tiles are an ODbL derived database); the repository holds the build script and the region definitions. The build script's libraries (`rasterio`, `osmium`) are in the `scenery` dependency group only (`uv run --group scenery ...`). Imagery: Sentinel-2 (10 m) over the region, and 1 m Satellogic EarthView (CC BY 4.0, 2022) within 25 km of the origin (2026-10-10). A commercial use would switch the ground to Copernicus GLO-30 (free with attribution) and re-check every licence.
- **Research priority:** autopilot / control design first. The log schema should favor what control work needs: full state, control surface commands and positions, trim condition, and enough precision to fit dynamic modes. RL and pilot training come later.

## Environment (checked 2026-10-04)
- Debian 13, Python 3.13.5, `uv` available; Node 20 available for the viewer.
- 12 CPU cores, 27 GB RAM (good for the parallel batch runner).
- GPU: AMD integrated (Lucienne), no CUDA. ML training runs on CPU; fine for small RL networks.
- Decision: develop on this machine. A second PC (12 cores, NVIDIA GTX 1660 6 GB) is available for later; move only when GPU training is needed (needs NVIDIA Container Toolkit). Docker + `uv.lock` keep the move to a clone and rebuild.

## Common commands
- Local: `uv sync`, `uv run pytest`, `uv run python scripts/<script>.py`
- Validation report: `uv run python scripts/validate.py` (regenerates `docs/VALIDATION.md`); tuned model: `uv run python scripts/validate.py configs/validation/c172p_tuned.yaml --out docs/VALIDATION_c172p_tuned.md`
- Controller comparison: `uv run python scripts/compare_controllers.py --episodes 100 [--log-dir data/episodes]`
- Batch: `uv run python scripts/batch_run.py --seeds 0:1000 [--policy pid|lqr|trim_hold] [--set wind.steady_speed_mps=[5,15]] [--policy-set weights.states.phi_rad=0.1] [--logs]`
- Approach and landing: `uv run python scripts/batch_run.py --env-config configs/envs/approach_landing.yaml --policy approach --seeds 0:1000` (prints landing and rollout statistics; landings roll to a full stop); crosswind and gusts: `configs/envs/approach_landing_crosswind.yaml`
- Takeoff: `uv run python scripts/batch_run.py --env-config configs/envs/takeoff.yaml --policy takeoff --seeds 0:1000` (or `takeoff_crosswind.yaml`; prints lift-off and climb-out statistics)
- Circuit: `uv run python scripts/batch_run.py --env-config configs/envs/circuit.yaml --policy circuit --seeds 0:1000` (or `circuit_crosswind.yaml`; `configs/circuit_autopilot.yaml` inlines the takeoff and approach autopilot files)
- Controller comparison and RL use `configs/envs/altitude_heading_hold_comfort.yaml` (comfort penalties, structural limits, terminations charged for the remaining steps): `uv run python scripts/batch_run.py --env-config configs/envs/altitude_heading_hold_comfort.yaml --seeds 0:1000 --policy lqr`
- Seeds: tune controllers on 1000-1999. Report on 0-999 and 2000-2999 (both were used while debugging the LQR retune, 2026-10-04) and on 3000-3999 (untouched; use only for final numbers). RL training draws its episodes from its own seeded streams, never these ranges. LQR gain schedules are cached in `data/cache/lqr/` (keyed by aircraft, JSBSim version, loading, LQR config, rate and flightsim source hash).
- RL: `uv run python scripts/train_rl.py configs/rl/ppo_comfort.yaml [--set wall_clock_limit_min=5]` writes `data/rl/<run_id>/`; evaluate with `scripts/batch_run.py --policy rl --rl-model data/rl/<run_id>/best`. Long runs: start them in the background and write the console log to a file, so an interrupted session leaves the saved models and log behind.
- Batch ids hash everything that determines results, including the flightsim source hash, but not the git fields (the same code committed or not is the same batch). Manifests record the full code version.
- Real-world scenery: `uv run --group scenery python scripts/build_scenery.py configs/scenery/abu_dhabi.yaml` (downloads into and builds `data/scenery/abu_dhabi/`; `--pin` records the sources' sha256 in the region file; `--hires-only` rebuilds only the 1 m imagery of a built region)
- Logs go to `data/` (gitignored; `data/.gitkeep` is committed so Docker never creates it as root).
- Viewer: `uv run python -m flightsim.stream` then open http://localhost:8686/ ; in Docker `docker compose up -d viewer` (published on all host interfaces)
- Docker: `uv run python scripts/docker.py build` (passes the git commit into the image; any `docker compose` arguments work, e.g. `up -d --build viewer`), `docker compose run --rm sim pytest`, `docker compose run --rm sim python scripts/<script>.py`
