// Pilot input from keyboard and gamepad, in the stream protocol's terms:
// stick and pedals in [-1, 1] relative to trim, throttle absolute in [0, 1].
// Signs follow the simulator: elevator + = push (nose down); rudder + = nose LEFT,
// so the right pedal sends a negative rudder command.

const KEY_DEFLECTION = 0.2; // gentle; with Shift: full deflection
const KEY_TIME_CONSTANT_S = 0.15; // keys ease in and out instead of jumping
const THROTTLE_RATE_PER_S = 0.4;
const DEADZONE = 0.08;

const AXIS_KEYS = {
  elevator: { ArrowUp: 1, ArrowDown: -1 },
  aileron: { ArrowRight: 1, ArrowLeft: -1 },
  rudder: { KeyZ: 1, KeyX: -1 }, // Z = left pedal (nose left), X = right pedal
};
const THROTTLE_KEYS = { KeyW: 1, PageUp: 1, KeyS: -1, PageDown: -1 };
export const HANDLED_KEYS = new Set([...Object.values(AXIS_KEYS).flatMap(Object.keys), ...Object.keys(THROTTLE_KEYS)]);

const dz = (v) => (Math.abs(v) < DEADZONE ? 0 : (v - Math.sign(v) * DEADZONE) / (1 - DEADZONE));
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export class PilotInput {
  constructor() {
    this.down = new Set();
    this.shift = false;
    this.value = { elevator: 0, aileron: 0, rudder: 0, throttle: 0 };
    this.gamepadName = null;
  }

  reset(throttle) {
    this.value = { elevator: 0, aileron: 0, rudder: 0, throttle };
    this.down.clear();
  }

  keydown(e) {
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

  // Advance by dt seconds and return the current input.
  update(dt) {
    const pad = [...(navigator.getGamepads?.() ?? [])].find((g) => g && g.connected && g.mapping === "standard");
    this.gamepadName = pad ? pad.id : null;
    const k = 1 - Math.exp(-dt / KEY_TIME_CONSTANT_S);
    const full = this.shift ? 1 : KEY_DEFLECTION;
    for (const [axis, keys] of Object.entries(AXIS_KEYS)) {
      let target = 0;
      for (const [code, sign] of Object.entries(keys)) if (this.down.has(code)) target += sign * full;
      this.value[axis] += (clamp(target, -1, 1) - this.value[axis]) * k;
    }
    let throttleDir = 0;
    for (const [code, sign] of Object.entries(THROTTLE_KEYS)) if (this.down.has(code)) throttleDir += sign;
    if (pad) {
      // Standard mapping: left stick = yoke (forward is -1 on axis 1), right stick x = rudder,
      // triggers = throttle up (RT) and down (LT). A deflected stick overrides the keys.
      const [ax, ay, rx] = [dz(pad.axes[0] ?? 0), dz(pad.axes[1] ?? 0), dz(pad.axes[2] ?? 0)];
      if (ax) this.value.aileron = ax;
      if (ay) this.value.elevator = -ay;
      if (rx) this.value.rudder = -rx;
      throttleDir += (pad.buttons[7]?.value ?? 0) - (pad.buttons[6]?.value ?? 0);
    }
    this.value.throttle = clamp(this.value.throttle + throttleDir * THROTTLE_RATE_PER_S * dt, 0, 1);
    return { ...this.value };
  }
}
