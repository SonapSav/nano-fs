// Stick response: sensitivity and expo per axis, remembered in this browser.
//   response = sensitivity * ((1 - expo) * x + expo * x^3)
// Sensitivity scales full deflection; expo softens small movements around centre while
// keeping full travel at the ends. Applies to gamepad sticks, not to the keyboard.

export const AXES = ["pitch", "roll", "rudder"];
export const DEFAULTS = {
  pitch: { sensitivity: 0.5, expo: 0.5 },
  roll: { sensitivity: 0.7, expo: 0.3 },
  rudder: { sensitivity: 0.7, expo: 0.3 },
};
const STORAGE_KEY = "flightsim.stick.v1";

export function shape(x, { sensitivity, expo }) {
  return sensitivity * ((1 - expo) * x + expo * x * x * x);
}

function valid(s) {
  return AXES.every((a) => s?.[a] && [s[a].sensitivity, s[a].expo].every((v) => Number.isFinite(v) && v >= 0 && v <= 1));
}

export function loadSettings() {
  try {
    const s = JSON.parse(localStorage.getItem(STORAGE_KEY));
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
