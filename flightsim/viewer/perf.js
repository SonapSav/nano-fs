// Frame timing for the performance readout (P key): drawn frames per second, the slowest
// frame, frames dropped (refreshes missed) and the arrival of the server's frame messages,
// over the last few seconds. Pure bookkeeping (tested with Node); app.js shows it.

const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[Math.floor((s.length - 1) / 2)] : 0;
};
const quantile = (xs, q) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[Math.round(q * (s.length - 1))] : 0;
};

export class FrameStats {
  constructor(windowMs = 10000) {
    this.windowMs = windowMs;
    this.frames = []; // animation frame times (ms)
    this.messages = []; // frame message arrival times (ms)
  }

  _prune(list, now) {
    while (list.length && now - list[0] > this.windowMs) list.shift();
  }

  frame(t) {
    this.frames.push(t);
    this._prune(this.frames, t);
    this._prune(this.messages, t);
  }

  message(t) {
    this.messages.push(t);
    this._prune(this.messages, t);
  }

  // {fps, refreshMs (the display's frame interval, the median), worstMs, dropped (refreshes
  // missed: an interval of k refreshes drops k - 1), msgMedianMs, msgP95Ms, msgMaxMs}.
  summary() {
    const iv = this.frames.slice(1).map((t, i) => t - this.frames[i]);
    const refreshMs = median(iv);
    const span = this.frames.length > 1 ? this.frames[this.frames.length - 1] - this.frames[0] : 0;
    const gaps = this.messages.slice(1).map((t, i) => t - this.messages[i]);
    return {
      fps: span > 0 ? (iv.length * 1000) / span : 0,
      refreshMs,
      worstMs: iv.length ? Math.max(...iv) : 0,
      dropped: refreshMs > 0 ? iv.reduce((n, dt) => n + Math.max(0, Math.round(dt / refreshMs) - 1), 0) : 0,
      msgMedianMs: median(gaps),
      msgP95Ms: quantile(gaps, 0.95),
      msgMaxMs: gaps.length ? Math.max(...gaps) : 0,
    };
  }
}
