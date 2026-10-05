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

- [x] **Second PPO run with the reward fix** (2026-10-04, authorized; `0377c52`).
  `configs/rl/ppo_comfort_v2.yaml` = run 1 with reward normalization/clipping replaced
  by a fixed 0.1 scale (termination charge keeps its size). Run `5a24bb1fb250`, full
  90 min, 14.0 M steps, trained detached from the terminal. Evaluation on seeds
  1000-1019 peaked at 2 M steps (-4635), then fluctuated between -5000 and -10400.
  Seeds 3000-3999, comfort task (same columns as above; "never settled" as printed by
  the batch runner):

  | Policy | Return | Median | Comfort | Never settled | Ended early | TAS RMS | Hdg RMS | Activity | Bank p95 |
  |---|---|---|---|---|---|---|---|---|---|
  | PID `f9f9a8fd6be1` | -1468 | -1309 | -26.6 | 140 | 0 | 1.56 | 14.4 | 0.29 | 28 |
  | LQR `9368815af54f` | -1479 | -1342 | -18.5 | 16 | 0 | 1.24 | 14.9 | 0.78 | 27 |
  | PPO run 1 best (3 M) | -2576 | -1992 | -149 | 784 | 11 (alpha) | 6.32 | 12.3 | 2.19 | 34 |
  | PPO run 2 best (2 M) `e2e261773382` | -6065 | -1850 | -367 | 946 | 83 (79 bank, 4 alpha) | 4.27 | 15.1 | 2.40 | 60 |
  | PPO run 2 final (14 M) `63f3bab4b1f1` | -6422 | -2161 | -465 | 999 | 129 (110 alpha, 19 altitude) | 6.39 | 24.1 | 3.45 | 37 |

  **Result: worse than run 1.** The typical episode is slightly better (run 2 best has
  the best PPO median, -1850), but terminations rose from 11 to 83, so the hypothesis
  "clipping hides the termination charge" is not confirmed as the main problem.
  Likely causes: without normalization the value targets span about -5 to -5000
  (scaled), which PPO's value network fits poorly; the 20-seed evaluation used to pick
  "best" is too noisy (2 M scored -4635 there, -6065 on 1000 seeds); exploration noise
  and learning rate are constant, so the policy keeps drifting late in training. More
  plain-PPO compute is not recommended; residual RL (below) starts from a controller
  that never terminates.

## Pending

### Next directions (pick one)

- [x] **After residual RL: decided (c) on 2026-10-05.** The LQR is the reference
  controller; RL is paused. Kept for later: On this task neither plain
  PPO nor residual RL beats the LQR/PID (about -1470 on seeds 3000-3999). Options:
  (a) residual on the reference governor instead (let the agent adjust the commanded
  climb/turn profile, where the cost is); (b) a harder task where the LQR is weak
  (strong turbulence, large disturbances, engine-out glide, approach); (c) treat the
  LQR as the reference controller and move to other work (second aircraft, HOTAS,
  takeoff/landing). Revisit (a) or (b) if RL becomes a goal again.

- [x] **Residual RL on the LQR** (2026-10-05, authorized; `dff26ab`): `flightsim/rl/residual.py`,
  `configs/rl/residual_lqr.yaml`. Command = LQR + agent correction (at most +/-0.2 per
  control); the agent also observes the LQR command; zero correction is exactly the LQR
  (tested). Rewards normalized but unclipped, learning rate 1e-4, best model picked on
  50 seeds (1000-1049). Model directories carry `residual.json`, so
  `--policy rl --rl-model <dir>` rebuilds LQR + agent.
  Run `bb4692c0bcb1`: 90 min, 11.3 M steps, no terminations in any evaluation. Training
  was healthy (value function explained >99% of return variance, small stable updates),
  but evaluation on seeds 1000-1049 started at -1507 (the LQR), drifted to -1609 by 5 M
  and recovered only to about -1570 by 11 M; the best model is the step-0 network.
  Seeds 3000-3999, comfort task:

  | Policy | Return | Median | Comfort | Ended early | TAS RMS | Hdg RMS | Alt RMS | Activity |
  |---|---|---|---|---|---|---|---|---|
  | LQR `9368815af54f` | -1479 | -1342 | -18.5 | 0 | 1.24 | 14.9 | 26.1 | 0.78 |
  | PID `f9f9a8fd6be1` | -1468 | -1309 | -26.6 | 0 | 1.56 | 14.4 | 26.5 | 0.29 |
  | Residual best (step 0) `39d4075c6430` | -1479 | -1342 | -18.6 | 0 | 1.24 | 14.9 | 26.0 | 0.78 |
  | Residual final (11.3 M) `730e9e050213` | -1551 | -1390 | -18.6 | 0 | 1.21 | 15.1 | 27.3 | 1.00 |

  Paired with the LQR on the same seeds: final -71.9 per episode (95% CI +/-9.2),
  better on 14% of seeds; step-0 model +0.4 (identical to the LQR in practice).
  **Result: residual RL keeps the LQR's safety (no terminations, unlike plain PPO's
  11-129) but does not improve on it.** The pre-authorized extra 90 min was not used:
  the late trend was only a slow recovery toward the LQR (about 6 points per M steps).
  Likely reason: most of the task cost is the transient to targets up to 150 m and
  75 deg away, whose speed is set by the LQR's reference governor (climb and turn
  rate limits) and the comfort penalties, not by the inner loop the residual corrects.
  The inner loop is already close to optimal for this quadratic-like cost, so the
  agent's corrections mostly add noise (more control activity, slightly worse altitude).
- [ ] **Tuned aircraft model** to close the step 2 deviations, as a separate copy of
  `c172p` (see "Aircraft model fidelity"). Decided 2026-10-04: deviations accepted for
  now, tuning is a possible later step.
- [ ] **Takeoff, approach and landing scenarios.** **Needs decision:** which scenarios
  matter beyond cruise (open since setup). Depends on the low-altitude wind items below,
  and probably on flaps and brakes in the action space.

### Second aircraft

- [ ] **Add a second aircraft** (requested 2026-10-04; **needs decision:** which aircraft
  and why). Bundled JSBSim candidates that suit the project: `pa28` (Piper Cherokee),
  `c182`, `J3Cub`, `c310` (twin), `DHC6`, `pc7`, `t6texan2`. Bundled models vary in
  quality, so the work is mostly: validation against that aircraft's POH (repeat step
  2), loading (seat/tank point-mass indices), PID retune, LQR redesign (automatic from
  linearization, then check), task configs (speeds, comfort/structural limits from its
  POH), viewer (airspeed/tach markings, flap detents and limits, eye point, 3D model);
  twins/turboprops need extra controls. Estimate for a single-engine piston with a POH:
  one to two days. Not in JSBSim: build a model (geometry, mass and inertia, aero
  tables, engine/prop, gear, FCS) from POH, type data, NASA/NACA reports, Roskam,
  Aeromatic, DATCOM/OpenVSP; weeks of work, approximate without flight-test data.

### Aircraft model fidelity (from step 2)

- [ ] Stall speeds 3.4-4.7 kt fast in 3 of 6 POH cases (aft CG flaps up 54.4 vs 51 KCAS;
  forward CG flaps up 55.5 vs 52; forward CG 30 deg flaps 50.7 vs 46, elevator-limited).
- [ ] Phugoid period 27.8 s vs ~35 s in the AAIB flight test (about 21% short); damping matches.
- [ ] Spiral mode is stable at mid CG in the model; the AAIB pilot found the real aircraft divergent.
- [ ] Short-period mode: no independent C172 measurement found in open sources; only
  MIL-F-8785C limits are checked. Look for a source.
- [x] Fuel flow validated leaned (2026-10-05): 8 checks (4 cruise points x RPM and EGT
  leaning methods, POH Section 4) all within 0.2 GPH of Figure 5-8 (tolerance 0.4).
  Found on the way: engine start in `reset` silently reset the mixture to full rich
  (fixed; all earlier runs used full rich, so no results change), and the model's
  tanks use 6.6 lb/gal against the POH's 6 (convert fuel mass). The model's peak RPM
  and peak EGT mixtures are far apart (unlike a real engine); fine for fuel flow.
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
- [x] Watch the LQR autopilot in the viewer (2026-10-05): "Watch the LQR autopilot" in
  the flight menu (`?source=live_lqr`). Live play messages take `autopilot: pid | lqr`;
  hello reports the `pilot`. The server loads (or designs, ~15 s) the gain schedule at
  startup (`--lqr configs/lqr.yaml`).

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

- [x] Flaps and pitch trim joined the manual tasks' action set on 2026-10-04 (see
  "Manual flight hardware"); autopilot and RL tasks keep four controls.
- [ ] Mixture and brakes are still held at trim; brakes (and probably mixture) belong
  to the takeoff/landing item.

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
- [x] Stick drift check (2026-10-04): Stick settings shows live raw stick positions
  (yellow when outside the dead zone) and an adjustable dead zone (default 0.08).
  Investigating a reported left roll found the aircraft itself rolls left when power
  is high for the speed (full throttle from trim: 12 deg left bank in 20 s; slight
  climb: 15 deg), the C172's real left-turning tendency (torque, P-factor, slipstream);
  hands off at trim it holds within 0.04 deg for 60 s. Correct with right rudder.
- [x] Centre calibration (2026-10-04): "Calibrate centre (hands off)" in Stick settings
  averages each stick's rest position for 1 s and subtracts it (each side rescaled, so
  full travel is kept); the dead zone then works around the true centre. Prompted by the
  owner's recorded flight `f8f0461a3c20-s0-m5b803fa0`: the aileron was never centred
  (median -0.048 left) and rudder constantly -0.056, matching Xbox stick rest positions
  of about -0.17 (left stick X) and +0.19 (right stick X), beyond the 0.08 dead zone.
- [ ] **Joystick support for the Thrustmaster T.Flight HOTAS X** (owner plans to buy it,
  2026-10-04). Browsers report it without the standard layout, so it is ignored today.
  Plan: per-device axis mapping (pitch, roll, rudder = twist grip, throttle = lever;
  invert, dead zone) keyed by device name, set through a calibration screen in the
  viewer (move each control when prompted). Do not hard-code axis indices: they vary by
  browser and OS and cannot be verified without the device. The same mechanism covers a
  yoke and rudder pedals later. Test with the device once it arrives.

### Viewer

- [x] **Procedural scenery for visual cues** (2026-10-04): seeded, offline, visual only
  (physics still flies over flat ground at 0 m). `flightsim/viewer/terrain.js`: hills
  up to ~350 m, valleys, lakes and the airfield at 0 m (consistent with the physics),
  4 km tiles streamed around the aircraft in 3 detail levels with time-budgeted
  building (~8 ms/frame), per-pixel field patchwork with hedgerows (shader), forests
  with instanced trees, villages, analytic normals (no tile seams). `scenery.js`:
  three.js Sky shader with sun and haze, airfield 09/27 (1000 m, markings, taxiway,
  apron, hangars) at lat/lon 0,0 below every start. Logarithmic depth buffer.
  Tests: `tests/test_viewer_terrain.py` (needs Node; skipped in Docker).
- [x] **Cockpit view** (2026-10-04): "Cockpit view" button or C key (remembered per
  browser). Camera at the pilot's eye in the left seat, from the c172p model's EYEPOINT
  and pilot position (0.10 m forward, 0.36 m left, 0.29 m above the CG), rotating with
  the aircraft; drag to turn the head, double-click to look ahead. Plain view, no
  cockpit parts drawn (a cowling/glare-shield/frame version was tried and removed at
  the owner's request, 2026-10-04). A white triangle at the bottom edge of the view points
  up at the aircraft's straight-ahead direction; it follows the nose when the head is
  turned and hides when the nose direction is out of view.
- [ ] **Scenery plan** (discussed 2026-10-05; do together with takeoff/landing, in this
  order, about 2 days for 1-3):
  1. **Terrain height in the physics.** Visual hills reach ~350 m but the physics ground
     is flat at 0 m, so low flight passes through hills. Port the seeded height function
     to Python (bit-identical to terrain.js, tested against it) and feed JSBSim's
     terrain elevation each step; deterministic, so logs stay reproducible.
     (Alternative considered: flatten everywhere you can fly low.) ~half a day + tests.
  2. **Landing cues:** aircraft shadow on the ground (~1 h); PAPI lights for a 3 deg
     glide path (~2 h; also a reference for an approach autopilot); windsock driven by
     the wind model (~1 h); runway edge markings, touchdown zone, approach light bar
     (~1-2 h).
  3. **Close-up ground detail** (grass/soil texture below ~50 m, ~2-3 h) and a
     **quality setting** low/medium/high (view distance, tree density, shadows; ~2 h;
     the dev machine has integrated graphics).
  Later, as a cruise/navigation package: clouds (~half a day, could follow the
  conditions); roads, rivers, towns (~1 day); smooth forest edges and trees/houses
  beyond the nearest 3 x 3 tiles (~half a day); time of day and visibility as config
  (~2-3 h). Skip: real-world scenery (out of scope), water reflections, detailed buildings.

- [x] Seeking in replays (2026-10-05): click or drag the progress bar (also while paused
  or after the replay ended), or arrow keys on it (5 s; up/down 30 s; Home/End).
  Protocol: `seek` message and `start_s` on replay play. Fixed on the way: a second
  play on the same connection streamed unpaced (saved demos checked, not affected).
- [ ] Replays show no altitude/heading targets (the replay source sends none); the
  targets could be recomputed from the logged config and seed.
- [x] Flight list grouped and filterable (2026-10-05): "Your flights" (newest first, seed,
  length, date), other recorded flights, one group per batch (by seed, pilot); 50 per
  group, a filter box (seed number or text) appears from 20 recorded flights. Log
  summaries come from Parquet footers (statistics for the duration), cached, and are
  listed off the event loop: 2,000 batch logs take 0.66 s the first time.
- [x] Font vendored (2026-10-05): Barlow Condensed 400/500/600, latin subset, SIL OFL 1.1
  (`flightsim/viewer/vendor/fonts/`). The viewer loads nothing from the internet (tested).
- [ ] Airspeed indicator shows calibrated airspeed as indicated (no position or
  instrument error modelled).
- [x] Viewer network exposure (decided 2026-10-04): keep the Docker viewer published on
  all host interfaces, reachable from the local network.

### Data and logging

- [x] **Code provenance** (decided and done 2026-10-04): logs (`flightsim.code_version`
  metadata) and batch manifests record the flightsim source hash plus git commit, dirty
  flag and diff hash. Batch ids now change with any code change (source hash) but not
  with git state alone. The LQR gain cache is also keyed on the source hash.
- [x] Docker runs record the git commit (2026-10-05): `scripts/docker.py` passes the
  commit, dirty flag and diff hash as build arguments; `code_version()` uses them when
  there is no repository (`git_source: build`, vs `repository` locally).
- [ ] Batches and logs made before this change (e.g. `data/batch/*` from 2026-10-04) have
  no code version; batch summaries are format 4 from now on.

- [x] Logs halved (2026-10-05): float columns use Parquet `BYTE_STREAM_SPLIT` before zstd
  (no schema change, lossless): a 5-minute run is 5.1 MB instead of 10.5 MB and writes
  4-7x faster. Older logs read as before.

### Infrastructure

- [ ] Docker image is 1.99 GB since torch was added (CPU-only build). A separate
  slim image without torch for sim/viewer-only use is possible if size matters.
- [x] The viewer container is rebuilt and restarted with each viewer change
  (`docker compose up -d --build viewer`); it was stale only after step 6.

- [ ] Move to the GPU PC (NVIDIA GTX 1660 6 GB) when GPU training is needed. Needs the
  NVIDIA Container Toolkit and a separate GPU image. Check its core layout first: here,
  workers beyond the 6 physical cores gave no speed-up.

### Housekeeping

- [x] Deleted the step 5 test demonstration `data/demos/74899ee1d8e6-s1-m512eaf16.parquet`
  (2026-10-04). `74899ee1d8e6-s0-mf3b6bfa2.parquet` was not created by Claude:
  presumably the owner's flight, kept.
- [x] Tests no longer take the unused `cruise` fixture argument (2026-10-05).
