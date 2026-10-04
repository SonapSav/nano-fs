# Project status

The single place for what is done and what is pending. Read this at the start of a
session, and update it whenever work is completed or a new item comes up: tick items
off, add the date and commit, and add new pending items where they belong.
Rules and conventions live in `CLAUDE.md`; sources live in `docs/REFERENCES.md`.

Legend: `[x]` complete, `[ ]` pending. **Needs decision** = waiting on the project
owner before work can start.

## Build order (from CLAUDE.md)

- [x] **Setup** (2026-10-04, `d3b9bf6`, `341f9e0`, `85fd834`): git repo with the
  SonapSav identity, Python 3.14.3 via `uv` with a committed lock file, Docker from day 1,
  aircraft `c172p`, research priority autopilot / control design.
- [x] **Step 1: headless run and log** (2026-10-04, `3893209`): JSBSim wrapper with SI
  `State`/`Controls`, YAML config with hash, heading hold, Parquet log schema v1.
  Same seed and config give byte-identical logs, also between local and Docker.
  `scripts/run_headless.py`.
- [x] **Step 2: validation** (2026-10-04, `e1d0636`): 24 checks in
  `configs/validation/c172p.yaml` against the C172P POH (1981 and 1985 editions), the
  AAIB 12/2020 C172S flight test and MIL-F-8785C Level 1. 20 pass, 4 accepted known
  deviations (see "Aircraft model fidelity"). `scripts/validate.py` writes `docs/VALIDATION.md`.
- [x] **Step 3: Gymnasium environment and PID baseline** (2026-10-04, `f5a62d0`):
  `flightsim/AltitudeHeadingHold-v0`, cascaded PID autopilot with autothrottle,
  `scripts/compare_controllers.py`. Over 100 seeds the PID settles every episode
  (mean return -1317 vs -10857 holding trim).
- [x] **Step 4: WebSocket stream and 3D viewer** (2026-10-04, `3a04cbe`): one port
  (8686) serves the viewer and the stream; a frame is exactly a log row (live and replay
  share one format). Three.js chase view and C172 six-pack with POH markings.
- [x] **Step 5: manual control** (2026-10-04, `f7e17c1`): keyboard and gamepad flying in
  the viewer, saved as demonstrations in `data/demos/`; `env.refly()` reproduces a
  demonstration's states exactly.
- [x] **Step 6: batch runner** (2026-10-04, `e817f25`): `scripts/batch_run.py`, parallel
  and byte-identical for any worker count; seeded steady wind and Dryden turbulence
  (MIL-F-8785C); config `base:` inheritance and overrides that are part of the hash.
  1000-episode PID batch: no early terminations, 150 never settled within 3 deg heading
  (130 in moderate turbulence).

## Pending

### Next directions (pick one)

- [ ] **RL training against the PID baseline.** **Needs decision:** requires PyTorch and
  Stable-Baselines3 (large dependencies). The environments are ready; compare with
  `scripts/compare_controllers.py` / `scripts/batch_run.py`.
- [ ] **Tuned aircraft model** to close the step 2 deviations, as a separate copy of
  `c172p` (see "Aircraft model fidelity"). Decided 2026-10-04: deviations accepted for
  now, tuning is a possible later step.
- [ ] **Takeoff, approach and landing scenarios.** **Needs decision:** which scenarios
  matter beyond cruise (open since setup). Depends on the low-altitude wind items below,
  and probably on flaps and brakes in the action space.

### Aircraft model fidelity (from step 2)

- [ ] Stall speeds 3.4-4.7 kt fast in 3 of 6 POH cases (aft CG flaps up 54.4 vs 51 KCAS;
  forward CG flaps up 55.5 vs 52; forward CG 30 deg flaps 50.7 vs 46, elevator-limited).
- [ ] Phugoid period 27.8 s vs ~35 s in the AAIB flight test (about 21% short); damping matches.
- [ ] Spiral mode is stable at mid CG in the model; the AAIB pilot found the real aircraft divergent.
- [ ] Short-period mode: no independent C172 measurement found in open sources; only
  MIL-F-8785C limits are checked. Look for a source.
- [ ] Fuel flow not validated: the POH assumes leaned mixture, runs use full rich. Lean
  before comparing with POH Figure 5-8.
- [ ] The model's empty-aircraft CG is aft of a typical 172P: the forward CG limit at
  2400 lb needs about 40 lb fuel and 860 lb in the front seats.

### Wind and atmosphere (from step 6)

- [ ] Low-altitude turbulence model (MIL-F-8785C 3.7.3, below 1000-2000 ft) and wind
  shear (3.7.3.2). Needed for takeoff and landing.
- [ ] Discrete gusts (MIL-F-8785C 3.7.1.3).
- [ ] Gust angular rates (only translational gusts are modelled).
- [ ] Severe turbulence is defined in the windy config (6.4 m/s) but has probability 0.
- [ ] Turbulence intensities were read from MIL-F-8785C Figure 7 (a plot): light ~5,
  moderate ~10, severe ~21 ft/s. Replace with tabulated values if a source is found.

### Controller

- [ ] PID heading does not settle within 3 deg in moderate turbulence (130 of 285 such
  episodes in the 1000-episode batch). A target for a better controller.

### Manual control and environment actions

- [ ] **Needs decision:** pitch trim, flaps, mixture and brakes are held at trim; adding
  any changes the environment's action space. Pitch trim would make long manual flights
  easier; flaps and brakes matter for takeoff and landing.

### Viewer

- [ ] Seeking in replays (only play, pause, stop and speed today).
- [ ] Filter or group batch logs in the flight list (thousands of entries with `--logs`).
- [ ] Font loads from Google Fonts (falls back to a system font offline); vendor it if
  offline use matters.
- [ ] Airspeed indicator shows calibrated airspeed as indicated (no position or
  instrument error modelled).
- [ ] **Needs decision:** the Docker viewer is published on all host interfaces (reachable
  from the local network). Bind to `127.0.0.1` if it should be local only.

### Data and logging

- [ ] Logs are 10.5 MB per 5 minutes (float64, zstd). For large batches, try Parquet
  `BYTE_STREAM_SPLIT` encoding on float columns (no schema change).

### Infrastructure

- [ ] Move to the GPU PC (NVIDIA GTX 1660 6 GB) when GPU training is needed. Needs the
  NVIDIA Container Toolkit and a separate GPU image. Check its core layout first: here,
  workers beyond the 6 physical cores gave no speed-up.

### Housekeeping

- [ ] `data/demos/74899ee1d8e6-s1-m512eaf16.parquet` is a headless test flight from
  step 5 testing; delete it unless wanted. (`74899ee1d8e6-s0-mf3b6bfa2.parquet` was not
  created by Claude: presumably the owner's flight.)
- [ ] Some tests still take a now-unused `cruise` fixture argument; tidy up.
