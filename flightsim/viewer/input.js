// Pilot input from keyboard and gamepad, in the stream protocol's terms:
// stick and pedals in [-1, 1] relative to trim, throttle and flaps absolute in [0, 1],
// pitch trim absolute in [-1, 1] (+ = nose down, like rolling the trim wheel forward).
// Flaps move one detent (0, 10, 20, 30 deg) per press; pitch trim moves while held;
// brakes (both main wheels, in [0, 1]) act while held.
// Signs follow the simulator: elevator + = push (nose down); rudder + = nose LEFT,
// so the right pedal sends a negative rudder command.

const KEY_DEFLECTION = 0.2; // gentle; with Shift: full deflection
const KEY_TIME_CONSTANT_S = 0.15; // keys ease in and out instead of jumping
const THROTTLE_RATE_PER_S = 0.4;

const AXIS_KEYS = {
  elevator: { ArrowUp: 1, ArrowDown: -1 },
  aileron: { ArrowRight: 1, ArrowLeft: -1 },
  rudder: { KeyZ: 1, KeyX: -1 }, // Z = left pedal (nose left), X = right pedal
};
const THROTTLE_KEYS = { KeyW: 1, PageUp: 1, KeyS: -1, PageDown: -1 };
const FLAP_KEYS = { KeyF: -1, KeyV: 1 }; // F = flaps up one detent, V = down one detent
const TRIM_KEYS = { KeyT: 1, KeyG: -1 }; // T = trim nose down, G = trim nose up
const BRAKE_KEY = "KeyB"; // hold for the toe brakes
const TRIM_RATE_PER_S = 0.15;
const FLAP_DETENTS = 3; // 0, 10, 20, 30 deg = 0, 1/3, 2/3, 1
// Standard gamepad buttons: 4 = LB, 5 = RB, 12 = D-pad up, 13 = D-pad down.
const PAD_FLAPS = { 4: -1, 5: 1 };
const PAD_TRIM = { 12: 1, 13: -1 };
const PAD_BRAKE = 1; // B (Xbox) / circle
export const HANDLED_KEYS = new Set([
  ...Object.values(AXIS_KEYS).flatMap(Object.keys),
  ...Object.keys(THROTTLE_KEYS), ...Object.keys(FLAP_KEYS), ...Object.keys(TRIM_KEYS), BRAKE_KEY,
]);

import { choosePad, controlValue, deadzone, loadSettings, profileFor, shape } from "./stick.js";

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export class PilotInput {
  constructor() {
    this.down = new Set();
    this.shift = false;
    this.value = { elevator: 0, aileron: 0, rudder: 0, throttle: 0, flaps: 0, pitch_trim: 0, brake: 0 };
    this.gamepadName = null;
    this._padPrev = {};
    this.stick = loadSettings(); // per-axis sensitivity and expo (stick.js)
  }

  reset(throttle, flaps = 0, pitchTrim = 0) {
    this.value = { elevator: 0, aileron: 0, rudder: 0, throttle, flaps, pitch_trim: pitchTrim, brake: 0 };
    this.down.clear();
    this._padPrev = {};
    this._leverStart = null; // a throttle lever takes over only once it is moved
    this._leverMoved = false;
  }

  _stepFlaps(dir) {
    const detent = Math.round(this.value.flaps * FLAP_DETENTS) + dir;
    this.value.flaps = clamp(detent, 0, FLAP_DETENTS) / FLAP_DETENTS;
  }

  keydown(e) {
    if (e.code in FLAP_KEYS && !e.repeat) this._stepFlaps(FLAP_KEYS[e.code]);
    this.down.add(e.code);
    this.shift = e.shiftKey;
  }

  keyup(e) {
    this.down.delete(e.code);
    this.shift = e.shiftKey;
  }

  releaseAll() {
    this.down.clear();
  }

  // Read the gamepad (its name and raw axes) without changing the control input; the
  // Stick settings readout and calibration use this.
  readPad() {
    const pad = choosePad([...(navigator.getGamepads?.() ?? [])], this.stick);
    this.pad = pad;
    this.gamepadName = pad ? pad.id : null;
    this.rawAxes = pad ? [...pad.axes] : null;
    return pad;
  }

  // The current device's settings (axis mapping, centre); null without a gamepad.
  profile() {
    return this.pad ? profileFor(this.stick, this.pad) : null;
  }

  // Mapped sticks (roll, pitch, rudder) that read outside the dead zone after
  // calibration: [{axis, value}]. With hands off, this is uncorrected drift.
  offCentre() {
    if (!this.readPad()) return [];
    const profile = this.profile();
    return ["roll", "pitch", "rudder"]
      .map((axis) => ({ axis, value: controlValue(profile, this.rawAxes, axis) }))
      .filter((a) => a.value !== null && Math.abs(a.value) >= this.stick.deadzone);
  }

  // Advance by dt seconds and return the current input.
  update(dt) {
    const pad = this.readPad();
    const k = 1 - Math.exp(-dt / KEY_TIME_CONSTANT_S);
    const full = this.shift ? 1 : KEY_DEFLECTION;
    for (const [axis, keys] of Object.entries(AXIS_KEYS)) {
      let target = 0;
      for (const [code, sign] of Object.entries(keys)) if (this.down.has(code)) target += sign * full;
      this.value[axis] += (clamp(target, -1, 1) - this.value[axis]) * k;
    }
    let throttleDir = 0;
    for (const [code, sign] of Object.entries(THROTTLE_KEYS)) if (this.down.has(code)) throttleDir += sign;
    let trimDir = 0;
    for (const [code, sign] of Object.entries(TRIM_KEYS)) if (this.down.has(code)) trimDir += sign;
    let brake = this.down.has(BRAKE_KEY) ? 1 : 0;
    let lever = null;
    if (pad) {
      // Axes as mapped in Stick settings (defaults for standard gamepads: left stick =
      // yoke, right stick X = rudder). Calibrated centre first, then the dead zone, then
      // the response curve. A deflected stick overrides the keys.
      const profile = this.profile();
      const get = (c) => {
        const v = controlValue(profile, pad.axes, c);
        return v === null ? 0 : deadzone(v, this.stick.deadzone);
      };
      const [roll, pitch, yaw] = [get("roll"), get("pitch"), get("rudder")];
      if (roll) this.value.aileron = shape(roll, this.stick.roll);
      if (pitch) this.value.elevator = shape(pitch, this.stick.pitch);
      if (yaw) this.value.rudder = -shape(yaw, this.stick.rudder); // right pedal: nose right = negative rudder
      lever = controlValue(profile, pad.axes, "throttle");
      if (pad.mapping === "standard") {
        // Standard layout: triggers = throttle up (RT) and down (LT), bumpers = flaps, D-pad = trim.
        throttleDir += (pad.buttons[7]?.value ?? 0) - (pad.buttons[6]?.value ?? 0);
        for (const [b, sign] of Object.entries(PAD_TRIM)) if (pad.buttons[b]?.pressed) trimDir += sign;
        brake = Math.max(brake, pad.buttons[PAD_BRAKE]?.value ?? 0);
        for (const [b, dir] of Object.entries(PAD_FLAPS)) {
          const pressed = Boolean(pad.buttons[b]?.pressed);
          if (pressed && !this._padPrev[b]) this._stepFlaps(dir); // one detent per press
          this._padPrev[b] = pressed;
        }
      }
    }
    this.value.throttle = clamp(this.value.throttle + throttleDir * THROTTLE_RATE_PER_S * dt, 0, 1);
    if (lever !== null) {
      // A throttle lever is absolute; it takes over once moved, so a flight starts at the
      // trimmed power whatever the lever position.
      this._leverStart ??= lever;
      if (this._leverMoved || Math.abs(lever - this._leverStart) > 0.03) {
        this._leverMoved = true;
        this.value.throttle = clamp(lever, 0, 1);
      }
    }
    this.value.pitch_trim = clamp(this.value.pitch_trim + clamp(trimDir, -1, 1) * TRIM_RATE_PER_S * dt, -1, 1);
    this.value.brake += (brake - this.value.brake) * k;
    return { ...this.value };
  }
}
