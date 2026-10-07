// Gamepad and joystick settings, remembered in this browser.
//
// Response per control (shared by all devices):
//   response = sensitivity * ((1 - expo) * x + expo * x^3)
// Sensitivity scales full deflection; expo softens small movements around centre while
// keeping full travel at the ends. Applies to sticks, not to the keyboard.
//
// Per device (keyed by the browser's gamepad id): which axis drives each control and
// whether it is inverted, and the measured rest position of each axis. Mapped values
// use the pilot's sense: roll + = right, pitch + = forward (push), rudder + = right
// pedal / twist right, throttle + = more power. (input.js converts to the simulator's
// signs: elevator + = push, rudder + = nose LEFT.)
//
// Also per device: which button does each button function (flaps, trim, brakes, throttle
// steps). A binding is a button ({button}) or one direction of an axis ({axis, dir}): a
// joystick's hat switch is reported as two axes (-1, 0, +1) on Linux.

export const AXES = ["pitch", "roll", "rudder"]; // shaped controls
export const CONTROLS = ["roll", "pitch", "rudder", "throttle"]; // mappable controls
export const BUTTONS = ["flaps_up", "flaps_down", "trim_nose_down", "trim_nose_up", "brake", "throttle_up", "throttle_down"];
export const DEFAULTS = {
  pitch: { sensitivity: 0.5, expo: 0.5 },
  roll: { sensitivity: 0.7, expo: 0.3 },
  rudder: { sensitivity: 0.7, expo: 0.3 },
  deadzone: 0.08, // stick movement around centre that is ignored (covers stick drift)
  devices: {}, // gamepad id -> { map: {control: {axis, invert}}, centre: {axis: rest value}, buttons: {function: binding} }
};
const STORAGE_KEY = "flightsim.stick.v3";
const OLD_KEY = "flightsim.stick.v2"; // before per-device axis mapping (2026-10-05)
// Largest accepted rest offset. Gamepad stick drift is typically below 0.2 (an Xbox pad
// measured 0.17-0.19 here); a larger "centre" means a stick was touched while calibrating.
export const MAX_CENTRE = 0.3;
// Largest movement of a stick during the calibration second (hands off: noise only).
export const MAX_CALIBRATION_SPREAD = 0.05;
// Axis detection: the moved axis must change by at least this much.
export const DETECT_MIN_TRAVEL = 0.5;
// An axis bound to a button function counts as pressed beyond this (hat: +/-1 when pressed).
export const PRESS_THRESHOLD = 0.5;

// Standard-layout gamepads (e.g. Xbox): left stick = yoke, right stick X = rudder.
// Forward on a standard stick is -1, hence the inverted pitch.
export function defaultProfile(standard) {
  const none = { axis: null, invert: false };
  return standard
    ? { map: { roll: { axis: 0, invert: false }, pitch: { axis: 1, invert: true }, rudder: { axis: 2, invert: false }, throttle: none }, centre: {}, buttons: defaultButtons(true) }
    : { map: { roll: { ...none }, pitch: { ...none }, rudder: { ...none }, throttle: { ...none } }, centre: {}, buttons: defaultButtons(false) };
}

// Standard layout: bumpers = flaps up/down, D-pad up/down = trim nose down/up (like the
// trim wheel), B (Xbox) / circle = brakes, triggers = throttle up (RT) / down (LT).
// Other devices start unbound.
export function defaultButtons(standard) {
  const b = (i) => (standard ? { button: i } : null);
  return { flaps_up: b(4), flaps_down: b(5), trim_nose_down: b(12), trim_nose_up: b(13), brake: b(1), throttle_up: b(7), throttle_down: b(6) };
}

// A button function's value in [0, 1] (analog for analog buttons such as triggers); 0 if
// unbound or the button is missing.
export function buttonValue(pad, binding) {
  if (!binding) return 0;
  if (binding.button !== undefined) {
    const b = pad.buttons?.[binding.button];
    return b ? (typeof b.value === "number" ? b.value : b.pressed ? 1 : 0) : 0;
  }
  return (pad.axes?.[binding.axis] ?? 0) * binding.dir > PRESS_THRESHOLD ? 1 : 0;
}

// Which button (or axis direction) the pilot pressed, from the device's state before
// (`baseline`: {buttons: [values], axes}) and samples after. Buttons win over axes. An
// axis counts only if it rested released and moved beyond the press threshold (a hat),
// never one of `exclude` (axes mapped to stick controls) or one resting deflected (a
// throttle lever). Null if nothing was pressed.
export function detectButton(baseline, samples, exclude = []) {
  for (const s of samples) {
    const i = s.buttons.findIndex((v, k) => v > PRESS_THRESHOLD && !((baseline.buttons[k] ?? 0) > PRESS_THRESHOLD));
    if (i >= 0) return { button: i };
  }
  let best = null;
  for (const s of samples) {
    for (let i = 0; i < Math.min(s.axes.length, baseline.axes.length); i++) {
      if (exclude.includes(i) || Math.abs(baseline.axes[i]) > PRESS_THRESHOLD) continue;
      const v = s.axes[i];
      if (Math.abs(v) > PRESS_THRESHOLD && Math.abs(v - baseline.axes[i]) >= DETECT_MIN_TRAVEL && (!best || Math.abs(v) > Math.abs(best.v))) best = { i, v };
    }
  }
  return best ? { axis: best.i, dir: Math.sign(best.v) } : null;
}

export function shape(x, { sensitivity, expo }) {
  return sensitivity * ((1 - expo) * x + expo * x * x * x);
}

// Remove a stick's measured rest offset. Each side is rescaled separately, so the
// physical end stops still give exactly -1 and +1.
export function centred(v, c) {
  const d = v - c;
  return Math.max(-1, Math.min(1, d >= 0 ? d / (1 - c) : d / (1 + c)));
}

// Dead zone with rescaling, so the response starts smoothly at the edge of the zone.
export function deadzone(v, dz) {
  return Math.abs(v) < dz ? 0 : (v - Math.sign(v) * dz) / (1 - dz);
}

// A control's value from a device's raw axes, in the pilot's sense, after centre
// calibration and inversion (no dead zone or shaping); null if the control is unmapped.
// The throttle lever is absolute: [-1, 1] -> [0, 1], no centre.
export function controlValue(profile, axes, control) {
  const m = profile.map[control];
  if (!m || m.axis === null || m.axis === undefined || !(m.axis < axes.length)) return null;
  const raw = axes[m.axis] ?? 0;
  if (control === "throttle") return ((m.invert ? -raw : raw) + 1) / 2;
  const v = centred(raw, profile.centre[m.axis] ?? 0);
  return m.invert ? -v : v;
}

// Which axis the pilot moved: the one that changed most from its baseline, if it moved
// at least DETECT_MIN_TRAVEL. `invert` makes the move the positive direction.
export function detectAxis(baseline, samples) {
  let best = null;
  for (const axes of samples) {
    for (let i = 0; i < Math.min(axes.length, baseline.length); i++) {
      const d = axes[i] - baseline[i];
      if (!best || Math.abs(d) > Math.abs(best.delta)) best = { axis: i, delta: d };
    }
  }
  if (!best || Math.abs(best.delta) < DETECT_MIN_TRAVEL) return null;
  return { axis: best.axis, invert: best.delta < 0 };
}

// The device's profile, creating the default one if it has none yet.
// Profiles saved before button mapping (2026-10-07) get the default buttons.
export function profileFor(settings, pad) {
  if (!settings.devices[pad.id]) settings.devices[pad.id] = defaultProfile(pad.mapping === "standard");
  const p = settings.devices[pad.id];
  p.buttons = { ...defaultButtons(pad.mapping === "standard"), ...p.buttons };
  return p;
}

// Pick the gamepad to fly with: one with a saved profile, else a standard-layout one,
// else any connected device (e.g. a joystick the browser reports without a layout).
export function choosePad(pads, settings) {
  const connected = pads.filter((g) => g && g.connected);
  return connected.find((g) => settings.devices[g.id]) ?? connected.find((g) => g.mapping === "standard") ?? connected[0] ?? null;
}

const finite01 = (v) => Number.isFinite(v) && v >= 0 && v <= 1;

function validBinding(b) {
  return b === null || (b && typeof b === "object" && (Number.isInteger(b.button) ? b.button >= 0 : Number.isInteger(b.axis) && b.axis >= 0 && (b.dir === 1 || b.dir === -1)));
}

function validProfile(p) {
  return (
    p && typeof p === "object" && p.map && p.centre && typeof p.centre === "object" &&
    CONTROLS.every((c) => p.map[c] && (p.map[c].axis === null || Number.isInteger(p.map[c].axis)) && typeof p.map[c].invert === "boolean") &&
    Object.values(p.centre).every((c) => Number.isFinite(c) && Math.abs(c) < MAX_CENTRE) &&
    (p.buttons === undefined || (p.buttons && typeof p.buttons === "object" && Object.values(p.buttons).every(validBinding)))
  );
}

function valid(s) {
  return (
    AXES.every((a) => s?.[a] && finite01(s[a].sensitivity) && finite01(s[a].expo)) &&
    Number.isFinite(s.deadzone) && s.deadzone >= 0 && s.deadzone < 0.5 &&
    s.devices && typeof s.devices === "object" && Object.values(s.devices).every(validProfile)
  );
}

function read(key) {
  try {
    return JSON.parse(localStorage.getItem(key));
  } catch {
    return null; // storage unavailable or corrupt
  }
}

export function loadSettings() {
  const s = read(STORAGE_KEY);
  if (valid(s)) return s;
  // Earlier settings: keep the feel (sensitivity, expo, dead zone). Their centre was for
  // axes 0-2 of whatever pad was used, so it is dropped: recalibrate once.
  const old = read(OLD_KEY);
  const migrated = { ...structuredClone(DEFAULTS), ...(old ? { pitch: old.pitch, roll: old.roll, rudder: old.rudder, deadzone: old.deadzone } : {}) };
  return valid(migrated) ? migrated : structuredClone(DEFAULTS);
}

export function saveSettings(s) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    // not persisted (private window, blocked storage); the settings still apply now
  }
}
