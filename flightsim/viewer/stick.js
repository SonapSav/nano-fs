// Stick response: sensitivity and expo per axis, remembered in this browser.
//   response = sensitivity * ((1 - expo) * x + expo * x^3)
// Sensitivity scales full deflection; expo softens small movements around centre while
// keeping full travel at the ends. Applies to gamepad sticks, not to the keyboard.

export const AXES = ["pitch", "roll", "rudder"];
export const DEFAULTS = {
  pitch: { sensitivity: 0.5, expo: 0.5 },
  roll: { sensitivity: 0.7, expo: 0.3 },
  rudder: { sensitivity: 0.7, expo: 0.3 },
  deadzone: 0.08, // stick movement around centre that is ignored (covers stick drift)
  centre: [0, 0, 0], // measured rest position of axes 0-2 (left X, left Y, right X), see calibration
};
const STORAGE_KEY = "flightsim.stick.v2";
// Largest accepted rest offset. Gamepad stick drift is typically below 0.2 (an Xbox pad
// measured 0.17-0.19 here); a larger "centre" means a stick was touched while calibrating.
export const MAX_CENTRE = 0.3;
// Largest movement of a stick during the calibration second (hands off: noise only).
export const MAX_CALIBRATION_SPREAD = 0.05;

export function shape(x, { sensitivity, expo }) {
  return sensitivity * ((1 - expo) * x + expo * x * x * x);
}

function valid(s) {
  return (
    AXES.every((a) => s?.[a] && [s[a].sensitivity, s[a].expo].every((v) => Number.isFinite(v) && v >= 0 && v <= 1)) &&
    Number.isFinite(s.deadzone) && s.deadzone >= 0 && s.deadzone < 0.5 &&
    Array.isArray(s.centre) && s.centre.length === 3 && s.centre.every((c) => Number.isFinite(c) && Math.abs(c) < MAX_CENTRE)
  );
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

export function loadSettings() {
  try {
    const s = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (s && !("centre" in s)) s.centre = [...DEFAULTS.centre]; // saved before calibration existed
    // A centre beyond MAX_CENTRE came from a stick held while calibrating (accepted up to
    // 0.5 before 2026-10-05): drop it, keep the other settings.
    if (s && Array.isArray(s.centre) && s.centre.some((c) => !(Math.abs(c) < MAX_CENTRE))) s.centre = [...DEFAULTS.centre];
    if (valid(s)) return s;
  } catch {
    // storage unavailable or corrupt: fall back to defaults
  }
  return structuredClone(DEFAULTS);
}

export function saveSettings(s) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    // not persisted (private window, blocked storage); the settings still apply now
  }
}
