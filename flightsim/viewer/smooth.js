// Smooth motion between the server's frames (30 per second, arriving a few ms early or
// late): the view is drawn a little behind the newest frame (DELAY_S of flight time) and
// interpolated between the two frames around the display time at every screen refresh,
// so the aircraft and the world move evenly whatever the refresh rate and the network
// timing. Display only: the gauges and readouts use the newest frame.
//
// The display clock advances with real time x playback speed and is steered gently toward
// "newest frame - DELAY_S"; it jumps there after a seek, a new flight or a long stall.

export const DELAY_S = 0.05;
const STEER_S = 0.5; // time constant of the steering
const BUFFER_S = 2; // frames kept (flight time)

const wrapPi = (a) => a - 2 * Math.PI * Math.round(a / (2 * Math.PI));
const ANGLES = new Set(["psi_rad", "phi_rad"]); // interpolated the short way round

// Row between a and b at fraction k (numbers only; anything else from b).
export function lerpRow(a, b, k) {
  const out = {};
  for (const key in b) {
    const va = a[key], vb = b[key];
    if (typeof va !== "number" || typeof vb !== "number") out[key] = vb;
    else if (ANGLES.has(key)) out[key] = va + wrapPi(vb - va) * k;
    else out[key] = va + (vb - va) * k;
  }
  return out;
}

export class FrameBuffer {
  constructor() {
    this.reset();
  }

  reset() {
    this.rows = [];
    this.clock = null; // display time (flight seconds)
  }

  push(row) {
    const last = this.rows[this.rows.length - 1];
    if (last && row.t_s < last.t_s) this.reset(); // a seek back: start again
    this.rows.push(row);
    while (this.rows.length > 2 && row.t_s - this.rows[1].t_s > BUFFER_S) this.rows.shift();
  }

  // Advance by dtS seconds of real time at `speed`; the row to draw (null before any frame).
  sample(dtS, speed = 1, paused = false) {
    const n = this.rows.length;
    if (!n) return null;
    const newest = this.rows[n - 1].t_s, target = newest - DELAY_S * speed;
    if (this.clock === null || Math.abs(target - this.clock) > Math.max(0.5, 0.5 * speed)) this.clock = target;
    else if (!paused) {
      this.clock += dtS * speed;
      this.clock += (target - this.clock) * Math.min(1, dtS / STEER_S);
    }
    this.clock = Math.min(this.clock, newest); // never ahead of the data
    if (this.clock <= this.rows[0].t_s) return this.rows[0];
    let i = n - 1;
    while (i > 0 && this.rows[i - 1].t_s > this.clock) i--;
    const a = this.rows[i - 1], b = this.rows[i];
    if (!a) return b;
    const span = b.t_s - a.t_s;
    return span > 0 ? lerpRow(a, b, (this.clock - a.t_s) / span) : b;
  }
}
