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

// Frame-rate limit: draw every `divisor`-th display refresh (1: every refresh). Only whole
// fractions of the refresh rate keep motion even. The refresh interval is measured from
// all animation frame callbacks (the median of the last 120); until it is known, every
// callback draws. Time-based, so a refresh the browser missed does not shift the cadence:
// a callback draws when at least divisor - 1/2 refreshes have passed since the last draw.
export class FramePacer {
  constructor(divisor = 1) {
    this.divisor = divisor;
    this.intervals = [];
    this.lastCallback = null;
    this.lastDraw = null;
  }

  get refreshMs() {
    return this.intervals.length >= 10 ? median(this.intervals) : 0;
  }

  // True if this animation frame callback (at `now`, ms) should draw.
  tick(now) {
    if (this.lastCallback !== null) {
      this.intervals.push(now - this.lastCallback);
      if (this.intervals.length > 120) this.intervals.shift();
    }
    this.lastCallback = now;
    const refresh = this.refreshMs;
    if (this.divisor <= 1 || !refresh || this.lastDraw === null || now - this.lastDraw >= (this.divisor - 0.5) * refresh) {
      this.lastDraw = now;
      return true;
    }
    return false;
  }
}
