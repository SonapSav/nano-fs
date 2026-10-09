// Performance test: frame times on a fixed view with the viewer's costly features switched
// off one at a time, to find what limits the frame rate on this computer. Display only.
//
// Each case is measured in the cockpit view and in the chase view: a short warm-up (new
// shaders compile, tiles settle), then the animation frame intervals for MEASURE_MS, and
// the viewer's own work per frame on the main thread ("script": our code plus handing the
// frame to WebGL; the graphics chip works on it afterwards). The test restores everything.

import { groundDetailStrength } from "./groundDetail.js";
import { QUALITY, terrainEffects } from "./terrain.js";

const WARMUP_MS = 1200;
const MEASURE_MS = 3000;

// Cases: a label and what to switch off ({logDepth, antialias, effects, detail, clouds,
// pixelRatio1, hud, panel, camera}) or on ({camera}: the belly camera's picture in the view).
export const BENCH_CASES = [
  { label: "as set", off: {} },
  { label: "no logarithmic depth", off: { logDepth: true } },
  { label: "no antialiasing", off: { antialias: true } },
  { label: "no fields / rivers", off: { effects: true } },
  { label: "no close-up texture", off: { detail: true } },
  { label: "no clouds", off: { clouds: true } },
  { label: "pixel ratio 1", off: { pixelRatio1: true } },
  { label: "no HUD", off: { hud: true } },
  { label: "no instrument panel", off: { panel: true } },
  { label: "belly camera on", off: {}, on: { camera: true } },
  { label: "all of these off", off: { logDepth: true, antialias: true, effects: true, detail: true, clouds: true, pixelRatio1: true, hud: true, panel: true, camera: true } },
];

const frameTimes = (ms) =>
  new Promise((resolve) => {
    const t = [];
    const tick = (now) => {
      t.push(now);
      if (now - t[0] < ms) requestAnimationFrame(tick);
      else resolve(t.slice(1).map((x, i) => x - t[i]));
    };
    requestAnimationFrame(tick);
  });

const stats = (iv) => {
  const s = [...iv].sort((a, b) => a - b);
  const sum = iv.reduce((a, b) => a + b, 0);
  return { fps: (iv.length * 1000) / sum, medianMs: s[Math.floor((s.length - 1) / 2)], p95Ms: s[Math.round(0.95 * (s.length - 1))], worstMs: s[s.length - 1] };
};
const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[Math.floor((s.length - 1) / 2)] : 0;
};

// Run the test. `scene`: the FlightScene; `setView(view)`; `restoreClouds()`: put the sky's
// clouds back; `setHud(on)` / `setPanel(on)` / `setCamera(on)`: draw them or not
// (`cameraOn`: whether the belly camera is in view to begin with); `scriptTimes`: an array the
// viewer appends its per-frame script time (ms) to; `progress(text)`. Returns
// {cases: [{label, view, fps, medianMs, p95Ms, worstMs, scriptMs, scriptP95Ms}], env}.
export async function runBench({ scene, setView, restoreClouds, setHud, setPanel, setCamera = () => {}, cameraOn = false, scriptTimes, progress }) {
  const quality = scene.quality, startView = scene.view;
  const ratio = scene.renderer.getPixelRatio();
  const apply = (off, on = {}) => {
    const ctx = { antialias: !off.antialias, logDepth: !off.logDepth };
    if (ctx.antialias !== apply.ctx.antialias || ctx.logDepth !== apply.ctx.logDepth) {
      scene.rebuildRenderer(ctx);
      apply.ctx = ctx;
    }
    scene.renderer.setPixelRatio(off.pixelRatio1 ? 1 : ratio);
    scene.resize();
    terrainEffects.value = off.effects ? 0 : 1;
    groundDetailStrength.value = off.detail ? 0 : QUALITY[quality].groundDetail;
    if (off.clouds) scene.clouds.set("clear", 0);
    else restoreClouds();
    setHud(!off.hud);
    setPanel(!off.panel);
    setCamera(on.camera ? true : off.camera ? false : cameraOn);
  };
  apply.ctx = { antialias: true, logDepth: true };
  const results = [];
  const views = ["cockpit", "chase"];
  try {
    let k = 0;
    for (const c of BENCH_CASES) {
      if (c.off.pixelRatio1 && ratio <= 1 && Object.keys(c.off).length === 1) continue; // already 1
      if (c.on?.camera && cameraOn) continue; // already in "as set"
      apply(c.off, c.on);
      for (const view of views) {
        progress(`Performance test ${++k}/${BENCH_CASES.length * views.length}: ${c.label}, ${view} view…`);
        setView(view);
        scene.resetView();
        await frameTimes(WARMUP_MS);
        scriptTimes.length = 0;
        const frame = stats(await frameTimes(MEASURE_MS));
        const sorted = [...scriptTimes].sort((a, b) => a - b);
        results.push({ label: c.label, view, ...frame, scriptMs: median(scriptTimes), scriptP95Ms: sorted[Math.round(0.95 * (sorted.length - 1))] ?? 0 });
      }
    }
  } finally {
    apply({});
    setView(startView);
  }
  const gl = scene.renderer.getContext();
  const dbg = gl.getExtension("WEBGL_debug_renderer_info");
  const canvas = scene.renderer.domElement;
  return {
    cases: results,
    env: {
      gpu: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      browser: navigator.userAgent,
      quality,
      view: `${canvas.width} x ${canvas.height} px drawn (window ${canvas.clientWidth} x ${canvas.clientHeight}, device pixel ratio ${window.devicePixelRatio})`,
    },
  };
}

// The results as plain text (to paste into a message).
export function benchReport({ cases, env }) {
  const rows = cases.map((c) => `${c.label.padEnd(22)} ${c.view.padEnd(8)} ${c.fps.toFixed(0).padStart(4)} fps  median ${c.medianMs.toFixed(1).padStart(5)} ms  95% ${c.p95Ms.toFixed(1).padStart(5)} ms  worst ${c.worstMs.toFixed(0).padStart(4)} ms  | script ${c.scriptMs.toFixed(1).padStart(4)} ms (95% ${c.scriptP95Ms.toFixed(1).padStart(4)})`);
  return [`GPU: ${env.gpu}`, `Browser: ${env.browser}`, `Quality: ${env.quality}; ${env.view}`, "", ...rows].join("\n");
}
