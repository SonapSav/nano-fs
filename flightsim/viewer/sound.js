// Cockpit and engine sound, synthesized with the Web Audio API from the state stream (no
// sample files). Sound only follows the frames; it never affects the physics.
//
//   engine   O-320, 4 cylinders, four-stroke: 2 firings per revolution, so the firing
//            frequency is rpm/30 (73 Hz at 2200 rpm); the 2-blade propeller passes at the
//            same rate. A harmonic-rich tone plus combustion noise pulsing at that rate,
//            brighter and louder with power.
//   wind     filtered noise rising with airspeed.
//   stall    warning horn above STALL_HORN_ALPHA_DEG (see below).
//   flaps    motor whine while the flaps move.
//
// soundParams() is the pure mapping from frames to sound settings (tested with Node);
// FlightSound turns it into audio.

const DEG = Math.PI / 180;
// Stall warning: the C172P POH (Section 4, Stalls) says the horn sounds 5-10 kt above the
// stall in all configurations. Like the real vane it is angle-of-attack based. In the
// JSBSim c172p at 2400 lb a single threshold meets that for flaps up and 30 deg at both CG
// limits only between 7.55 and 7.66 deg (measured 2026-10-05; tests/test_viewer_sound.py
// checks it): about 10 kt above the stall flaps up, 5-7 kt with full flaps.
export const STALL_HORN_ALPHA_DEG = 7.6;
const HORN_HYSTERESIS_DEG = 0.3;
const RATED_POWER_W = 160 * 745.7;

export function soundParams(row, prev, { horn: hornWasOn = false, view = "chase", distanceM = 22 } = {}) {
  const rpm = Math.max(0, row.engine_rpm ?? 0);
  const power = Math.max(0, Math.min(1.2, (row.engine_power_w ?? 0) / RATED_POWER_W));
  const kt = Math.max(0, (row.cas_mps ?? 0) / 0.514444);
  const alpha = (row.alpha_rad ?? 0) / DEG;
  const threshold = STALL_HORN_ALPHA_DEG - (hornWasOn ? HORN_HYSTERESIS_DEG : 0);
  const horn = alpha > threshold && kt > 20; // not on the ground at rest
  let flapRate = 0; // deg/s
  if (prev && row.t_s > prev.t_s) flapRate = Math.abs((row.flap_pos_rad - prev.flap_pos_rad) / DEG) / (row.t_s - prev.t_s);
  const inside = view === "cockpit";
  // Outside, sound falls off with distance (about 1/d, relative to the default 22 m).
  const distance = inside ? 1 : Math.min(1.5, 22 / Math.max(8, distanceM));
  const running = rpm > 300;
  return {
    engineHz: running ? rpm / 30 : 0,
    engineLevel: running ? (0.35 + 0.65 * Math.min(1, power)) * (inside ? 1 : 0.8) * distance : 0,
    engineBrightHz: 250 + 1400 * Math.min(1, power) * (inside ? 0.6 : 1), // the cabin muffles the highs
    windLevel: Math.min(1, (kt / 110) ** 2) * (inside ? 0.45 : 0.25) * (inside ? 1 : distance),
    windHz: 300 + 6 * kt,
    horn,
    hornLevel: horn ? (inside ? 0.5 : 0.12 * distance) : 0,
    flapLevel: flapRate > 0.5 ? (inside ? 0.12 : 0.03 * distance) : 0,
  };
}

const SMOOTH_S = 0.06; // parameter smoothing between frames

function noiseBuffer(ctx, seconds = 2) {
  const b = ctx.createBuffer(1, ctx.sampleRate * seconds, ctx.sampleRate);
  const d = b.getChannelData(0);
  let s = 172; // fixed seed: the same noise every time
  for (let i = 0; i < d.length; i++) {
    s = (s * 1664525 + 1013904223) >>> 0;
    d[i] = (s / 4294967296) * 2 - 1;
  }
  return b;
}

export class FlightSound {
  constructor() {
    this.ctx = null;
    this.enabled = true;
    this.volume = 0.6;
    this.state = null; // last parameters (for tests and the UI)
    this._prev = null;
  }

  // Create the audio graph; browsers only allow this after a user gesture (e.g. Play).
  unlock() {
    if (this.ctx) {
      if (this.ctx.state === "suspended") this.ctx.resume();
      return;
    }
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    const ctx = (this.ctx = new AC());
    const master = (this.master = ctx.createGain());
    master.gain.value = 0;
    const comp = ctx.createDynamicsCompressor();
    master.connect(comp).connect(ctx.destination);
    const noise = noiseBuffer(ctx);
    const noiseSource = () => {
      const n = ctx.createBufferSource();
      n.buffer = noise;
      n.loop = true;
      n.start();
      return n;
    };

    // Engine: harmonic tone (decaying harmonics with alternating phase, pulse-like) through
    // a low-pass whose cutoff follows power, plus a half-order tone for roughness.
    const harmonics = 14, real = new Float32Array(harmonics + 1), imag = new Float32Array(harmonics + 1);
    for (let n = 1; n <= harmonics; n++) imag[n] = (n % 2 ? 1 : -0.6) / n ** 0.75;
    this.engineOsc = ctx.createOscillator();
    this.engineOsc.setPeriodicWave(ctx.createPeriodicWave(real, imag));
    this.engineSub = ctx.createOscillator();
    this.engineSub.type = "triangle";
    this.engineFilter = ctx.createBiquadFilter();
    this.engineFilter.type = "lowpass";
    this.engineFilter.Q.value = 0.8;
    this.engineGain = ctx.createGain();
    this.engineGain.gain.value = 0;
    const subGain = ctx.createGain();
    subGain.gain.value = 0.35;
    this.engineOsc.connect(this.engineFilter);
    this.engineSub.connect(subGain).connect(this.engineFilter);
    this.engineFilter.connect(this.engineGain).connect(master);
    // Combustion noise, pulsing at the firing frequency.
    const pops = ctx.createBiquadFilter();
    pops.type = "bandpass";
    pops.frequency.value = 220;
    pops.Q.value = 0.9;
    this.popGain = ctx.createGain();
    this.popGain.gain.value = 0;
    this.popMod = ctx.createOscillator();
    this.popMod.type = "square";
    const popDepth = (this.popDepth = ctx.createGain());
    popDepth.gain.value = 0;
    this.popMod.connect(popDepth).connect(this.popGain.gain);
    noiseSource().connect(pops).connect(this.popGain).connect(this.engineGain);

    // Wind.
    this.windFilter = ctx.createBiquadFilter();
    this.windFilter.type = "bandpass";
    this.windFilter.Q.value = 0.5;
    this.windGain = ctx.createGain();
    this.windGain.gain.value = 0;
    noiseSource().connect(this.windFilter).connect(this.windGain).connect(master);

    // Stall horn: a reedy tone (frequency approximate).
    this.hornOsc = ctx.createOscillator();
    this.hornOsc.type = "sawtooth";
    this.hornOsc.frequency.value = 1650;
    const hornFilter = ctx.createBiquadFilter();
    hornFilter.type = "lowpass";
    hornFilter.frequency.value = 3200;
    this.hornGain = ctx.createGain();
    this.hornGain.gain.value = 0;
    this.hornOsc.connect(hornFilter).connect(this.hornGain).connect(master);

    // Flap motor whine.
    this.flapOsc = ctx.createOscillator();
    this.flapOsc.type = "sawtooth";
    this.flapOsc.frequency.value = 410;
    const flapFilter = ctx.createBiquadFilter();
    flapFilter.type = "bandpass";
    flapFilter.frequency.value = 900;
    this.flapGain = ctx.createGain();
    this.flapGain.gain.value = 0;
    this.flapOsc.connect(flapFilter).connect(this.flapGain).connect(master);

    for (const o of [this.engineOsc, this.engineSub, this.popMod, this.hornOsc, this.flapOsc]) o.start();
    this._applyMaster();
  }

  setEnabled(on) {
    this.enabled = on;
    this._applyMaster();
  }

  setVolume(v) {
    this.volume = Math.max(0, Math.min(1, v));
    this._applyMaster();
  }

  _applyMaster() {
    if (!this.ctx) return;
    this.master.gain.setTargetAtTime(this.enabled && !this._silent ? this.volume : 0, this.ctx.currentTime, 0.08);
  }

  // A new frame: follow it. opts: {view, distanceM}.
  update(row, opts = {}) {
    const p = soundParams(row, this._prev, { horn: this.state?.horn, ...opts });
    this._prev = row;
    this.state = p;
    this._silent = false;
    if (!this.ctx) return;
    const t = this.ctx.currentTime, set = (param, v) => param.setTargetAtTime(v, t, SMOOTH_S);
    if (p.engineHz > 0) {
      set(this.engineOsc.frequency, p.engineHz);
      set(this.engineSub.frequency, p.engineHz / 2);
      set(this.popMod.frequency, p.engineHz);
    }
    set(this.engineFilter.frequency, p.engineBrightHz);
    set(this.engineGain.gain, p.engineLevel * 0.5);
    set(this.popGain.gain, p.engineLevel > 0 ? 0.25 : 0);
    set(this.popDepth.gain, p.engineLevel > 0 ? 0.25 : 0);
    set(this.windFilter.frequency, p.windHz);
    set(this.windGain.gain, p.windLevel);
    this.hornGain.gain.setTargetAtTime(p.hornLevel, t, 0.02);
    set(this.flapGain.gain, p.flapLevel);
    this._applyMaster();
  }

  // Paused, stopped or finished: fade out (the next frame brings the sound back).
  silence() {
    this._silent = true;
    this._prev = null;
    this._applyMaster();
  }
}
