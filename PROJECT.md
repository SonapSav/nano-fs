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

## Beyond the build order

- [x] **Gain-scheduled LQR autopilot** (2026-10-04): `flightsim/control/lqr.py`,
  `configs/lqr.yaml`, policy `lqr` in the batch runner and comparison script. Coupled
  10-state LQR with integral action, discretized at 20 Hz, designed at a 4x4
  (altitude, airspeed) grid from JSBSim linearizations; reference governor with
  rate/acceleration-limited profiles and steady-turn/climb feedforward. Tuned on windy
  seeds 1000-1199 for bank parity with the PID; results on held-out seeds 0-999
  (batches `d274d9abd25d` PID, `e8302e39073a` LQR):

  | 1000 windy episodes | PID | LQR |
  |---|---|---|
  | Mean return | -1416 | -1412 |
  | Never settled (all / light / moderate) | 150 / 20 / 130 | 60 / 0 / 60 |
  | Median heading settling | 98 s | 21 s |
  | Airspeed RMS | 1.52 m/s | 1.13 m/s |
  | Calm-air load factor p5-p95 | 0.65-1.34 g | 0.92-1.16 g |
  | Bank p95 / max | 27.5 / 29.8 deg | 27.2 / 31.0 deg |
  | Climb rate p95 | 5.4 m/s | 7.0 m/s |
  | Control activity (action rate) | 0.28 | 0.85 |

  Episode metrics now include peak bank, load factor range, climb rate and airspeed
  deviation (batch summary format 2).

- [x] **RL setup and first PPO run** (2026-10-04, authorized: torch CPU-only + stable-baselines3;
  `803061f`, `406dbe9`). `scripts/train_rl.py` + `configs/rl/ppo_comfort.yaml`: PPO on
  the comfort task, 6 envs (~2,500 decisions/s), obs/reward normalization, evaluation
  every 1 M steps on seeds 1000-1019, best/checkpoint models, wall-clock limit; trained
  agents evaluate through the batch runner (`--policy rl --rl-model <dir>`).
  Run `04fa52c65e4a` was **interrupted at ~11 M steps (71 of 90 min) by a terminal
  shutdown**; best model (3 M steps) and 5 M / 10 M checkpoints survive
  (see `data/rl/04fa52c65e4a/INTERRUPTED.md`). Seeds 3000-3999, comfort task:

  | Policy | Return | Comfort | Never settled | Ended early | TAS RMS | Hdg RMS | Activity |
  |---|---|---|---|---|---|---|---|
  | PID `f9f9a8fd6be1` | -1468 | -26.6 | 140 | 0 | 1.56 | 14.4 | 0.29 |
  | LQR `9368815af54f` | -1479 | -18.5 | 16 | 0 | 1.24 | 14.9 | 0.78 |
  | PPO best, 3 M `5c0d51f44e24` | -2576 | -149 | 784 | 11 (alpha) | 6.32 | 12.3 | 2.19 |
  | PPO 10 M `29605f0cdd63` | -3071 | -208 | 982 | 28 (alpha, bank) | 5.56 | 16.2 | 2.71 |

  PPO learned the task far beyond holding trim (~-12970) and has the best heading
  tracking, but stalls, holds airspeed poorly and works the controls 7.5x the PID.
  Evaluation return peaked at 3 M steps and then degraded with more terminations.
  Likely cause: rewards are normalized and clipped to +/-10, which shrinks the
  termination charge (up to 53,760) to about one bad step, so terminations are not
  feared.

## Pending

### Next directions (pick one)

- [ ] **Second PPO run with the reward fix** (**needs decision:** more compute).
  `normalize.clip_rewards` very large (no clipping) or a fixed reward scale instead of
  normalization, so the termination charge keeps its size. Then evaluate on seeds
  3000-3999 as above.
- [ ] Residual RL: the agent learns corrections on top of the LQR (faster learning,
  starts from a safe controller). Decide after the second run.
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

- [x] PID heading settling in turbulence (was 130 of 285 moderate-turbulence episodes
  unsettled): the LQR halves it (60) and settles every light-turbulence episode.
- [ ] LQR works the controls about 2.7x harder than the PID in turbulence (action rate
  0.77 vs 0.28). Softer airspeed or input weights halve it at a small score cost
  (tuning seeds: tas 4 m/s -> activity 0.55, return -11). Consider an input-rate penalty
  (augment the design with input states) if actuator activity matters.
- [ ] LQR overshoots its reference bank by a few degrees, so its governor bank limit is
  22 deg to stay at the PID's ~25 deg envelope. A bank-angle protection or a
  constrained design (MPC) would enforce it directly.
- [x] LQR climb-rate peaks in turbulence were higher than the PID's (p95 7.0 vs 5.4 m/s);
  after the retune 4.8 vs 5.5.
- [ ] LQR episodes run at ~680x real time vs ~900x for the PID (per-step gain
  interpolation in numpy); optimize only if batches get too slow.
- [ ] Watch the LQR autopilot in the viewer (the live source flies the PID only).

### Task and reward

- [x] **Comfort task** (decided and done 2026-10-04, option C):
  `configs/envs/altitude_heading_hold_comfort.yaml` extends the windy task. Soft comfort
  penalties beyond 25 deg bank, |n - 1| > 0.3 g and 3 m/s climb/descent; episodes end at
  the C172P POH structural limits (60 deg bank, +3.8 / -1.52 g) plus the existing ones.
  Terminations charge every remaining step at the maximum per-step cost (22.4), because
  otherwise ending early was rewarded. Use this task for controller comparisons and RL.
  Held-out seeds 0-999:

  | Policy | Return | Comfort cost | Ended early |
  |---|---|---|---|
  | PID (`1089513fd4cc`) | -1439 | -22.7 | 0 |
  | LQR (`70c41f3b036f`) | -1495 | -82.9 | 0 |
  | Trim hold (`55a8e2845d79`) | -12970 | -1215 | 0 |
  | Aggressive LQR, governor removed (`2c92a2c96eea`) | -41953 | -195 | 777 |

  On this task the PID beat the first LQR: it settled more reliably but its stiffer gust
  response cost about 4x the comfort penalty. See the LQR retune below.
- [x] **LQR retuned on the comfort task** (2026-10-04). Findings, in order:
  - The comfort cost was climb rate in turbulence. Softer altitude hold made it worse,
    and a climb-rate output weight made it worse too.
  - Cause: the LQR fed back air-relative alpha and beta, which jump with every gust, and
    chased them with elevator and rudder. Feeding alpha/beta computed from the ground
    velocity (`alpha_beta_source: inertial`; identical to aero values without wind) cut
    the comfort cost below the PID's. Altitude hold stiffened to 3 m / 30 m s.
  - Held-out seeds then exposed stalls: a slow aircraft climbing into long downdrafts
    pitched up to 24 deg. Added underspeed / stall protection (no climb demand below
    target airspeed - 5 m/s or above 10 deg measured alpha). Its first version chattered
    on and off at the threshold and slammed the elevator; fixed with hysteresis and
    bumpless transfer.
  - Final, comfort task (debugging touched seeds 0-999 and 2000-2999; 3000-3999 untouched):

  | Seeds | Policy | Return | Comfort | Never settled | Climb p95 | Max bank | Activity |
  |---|---|---|---|---|---|---|---|
  | 0-999 | PID `231199a36475` | -1439 | -22.7 | 150 | 5.4 | 29.8 | 0.28 |
  | 0-999 | LQR `c1f32e52cdd7` | -1449 | -17.7 | 26 | 4.8 | 29.6 | 0.77 |
  | 2000-2999 | PID `e04ff9d1179f` | -1514 | -26.5 | 153 | 5.5 | 31.3 | 0.28 |
  | 2000-2999 | LQR `0a2fd8bb5f74` | -1514 | -18.0 | 31 | 4.8 | 29.6 | 0.77 |
  | 3000-3999 | PID `f9f9a8fd6be1` | -1468 | -26.6 | 140 | 5.5 | 31.6 | 0.29 |
  | 3000-3999 | LQR `9368815af54f` | -1479 | -18.5 | 16 | 4.8 | 29.4 | 0.78 |

  Overall score tied (within 1%); LQR ~30% lower comfort cost, ~85% fewer unsettled
  episodes, lower climb and bank peaks, better airspeed; PID slightly better heading and
  a third of the control activity. No terminations for either.
- [ ] LQR heading tracking is slower than the PID's (its governor allows 22 deg bank vs
  the PID's 25); the obvious next LQR lever.
- [ ] The calm and windy tasks still reward early termination (fixed charge of 100 vs
  ~1400 per full episode). Harmless for the autopilots (they never terminate) but do not
  train RL on them; use the comfort task.
- [ ] Termination and comfort are checked on the state at each decision step (20 Hz);
  peaks between decisions (6 simulation steps) are not seen.

### Manual control and environment actions

- [ ] **Needs decision:** pitch trim, flaps, mixture and brakes are held at trim; adding
  any changes the environment's action space. Pitch trim would make long manual flights
  easier; flaps and brakes matter for takeoff and landing.

### Manual flight hardware

- [x] Xbox controller works for manual flights (standard gamepad mapping, step 5).
- [x] Stick sensitivity (2026-10-04): per-axis sensitivity and expo for gamepad sticks
  (`flightsim/viewer/stick.js`), set in a "Stick settings" dialog and remembered per
  browser; defaults soften pitch (0.5 / 0.5). A status line shows whether a gamepad is
  detected. Requested after pitch felt too sensitive on the Xbox controller.
- [x] Flaps and pitch trim for manual flight (2026-10-04). New task configs
  `configs/envs/manual.yaml` (calm) and `manual_wind.yaml` add `flaps` and `pitch_trim`
  to the action set (`actions:` in env configs; autopilot/RL tasks keep four controls and
  are unchanged). Flaps step through 0/10/20/30 deg (F/V keys, Xbox LB/RB); trim moves
  while held (T/G, D-pad), +0.15 per second. Flap speed limits from the C172P POH
  (Figure 2-1): 110 KIAS with 10 deg, 85 KIAS beyond; overspeed costs comfort points and
  ends the flight 10 kt beyond the limit (the margin is a project choice, not a POH
  value). The viewer offers calm or wind-and-turbulence manual flights and shows flaps
  (with an overspeed warning) and trim. Also fixed: the flying-controls hint never
  showed during manual flights.
- [ ] **Joystick support for the Thrustmaster T.Flight HOTAS X** (owner plans to buy it,
  2026-10-04). Browsers report it without the standard layout, so it is ignored today.
  Plan: per-device axis mapping (pitch, roll, rudder = twist grip, throttle = lever;
  invert, dead zone) keyed by device name, set through a calibration screen in the
  viewer (move each control when prompted). Do not hard-code axis indices: they vary by
  browser and OS and cannot be verified without the device. The same mechanism covers a
  yoke and rudder pedals later. Test with the device once it arrives.

### Viewer

- [ ] Seeking in replays (only play, pause, stop and speed today).
- [ ] Filter or group batch logs in the flight list (thousands of entries with `--logs`).
- [ ] Font loads from Google Fonts (falls back to a system font offline); vendor it if
  offline use matters.
- [ ] Airspeed indicator shows calibrated airspeed as indicated (no position or
  instrument error modelled).
- [x] Viewer network exposure (decided 2026-10-04): keep the Docker viewer published on
  all host interfaces, reachable from the local network.

### Data and logging

- [x] **Code provenance** (decided and done 2026-10-04): logs (`flightsim.code_version`
  metadata) and batch manifests record the flightsim source hash plus git commit, dirty
  flag and diff hash. Batch ids now change with any code change (source hash) but not
  with git state alone. The LQR gain cache is also keyed on the source hash.
- [ ] Docker runs record the source hash but no git commit (`.git` is not in the image).
  If needed: pass the commit in at build time, or mount `.git` read-only and install git.
- [ ] Batches and logs made before this change (e.g. `data/batch/*` from 2026-10-04) have
  no code version; batch summaries are format 4 from now on.

- [ ] Logs are 10.5 MB per 5 minutes (float64, zstd). For large batches, try Parquet
  `BYTE_STREAM_SPLIT` encoding on float columns (no schema change).

### Infrastructure

- [ ] Docker image is 1.99 GB since torch was added (CPU-only build). A separate
  slim image without torch for sim/viewer-only use is possible if size matters.
- [ ] The viewer container still runs the image from step 6; restart it
  (`docker compose up -d viewer`) to pick up later changes (none affect the viewer).

- [ ] Move to the GPU PC (NVIDIA GTX 1660 6 GB) when GPU training is needed. Needs the
  NVIDIA Container Toolkit and a separate GPU image. Check its core layout first: here,
  workers beyond the 6 physical cores gave no speed-up.

### Housekeeping

- [x] Deleted the step 5 test demonstration `data/demos/74899ee1d8e6-s1-m512eaf16.parquet`
  (2026-10-04). `74899ee1d8e6-s0-mf3b6bfa2.parquet` was not created by Claude:
  presumably the owner's flight, kept.
- [ ] Some tests still take a now-unused `cruise` fixture argument; tidy up.
