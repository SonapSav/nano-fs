// Groups the server's log list for the flight menu: your demonstrations (newest first),
// other recorded flights, and one group per batch. Large groups are capped; a filter
// (seed, run id or path text) narrows them. Pure functions, no DOM (tested with Node).

export const GROUP_CAP = 50; // options shown per group before "… N more"
// Task names of past flights (the server reads them from each log's config).
export const TASK_TITLES = { free: "Free flight", approach: "Approach and landing", takeoff: "Takeoff", circuit: "Circuit" };

const pad2 = (n) => String(n).padStart(2, "0");
export const fmtDuration = (s) => `${Math.floor(s / 60)}:${pad2(Math.floor(s % 60))}`;

function fmtDate(epochS) {
  const d = new Date(epochS * 1000);
  return `${d.toLocaleString("en-US", { month: "short" })} ${d.getDate()}, ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

// What a past flight's result says (stream/results.py; the task's failure reasons).
const FAILURES = {
  nose_first: "Nose wheel first", wing_low: "Wing low at touchdown", hard_landing: "Hard landing", off_runway: "Ran off the runway",
  undershoot: "Short of the runway", side_load: "Touched down crabbed", tail_strike: "Tail strike", wingtip_strike: "Wingtip strike",
  nose_strike: "Nose strike", lost_approach: "Lost the approach", overrun: "Ran off the end", no_stop: "Did not stop in time",
  no_liftoff: "No lift-off", sank_back: "Sank back after lift-off", lost: "Off the centreline", alpha: "Stalled",
  bank: "Bank limit", load_factor: "Load limit",
};

// {text, kind: "good" | "bad" | "plain"} for a result, or null (unknown or not computed yet).
export function resultText(r) {
  if (!r || r.outcome === "unknown") return null;
  const td = r.touchdown;
  const where = td ? ` ${Math.round(td.along_m)} m, ${td.sink_fpm} fpm` : "";
  switch (r.outcome) {
    case "landed":
      return { text: `Landed${where}${r.bounces ? `, ${r.bounces} bounce${r.bounces > 1 ? "s" : ""}` : ""}`, kind: "good" };
    case "climbed":
      return { text: "Climbed out", kind: "good" };
    case "completed":
      return { text: "Completed", kind: "good" };
    case "failed":
      return { text: `${FAILURES[r.reason] ?? r.reason.replace(/_/g, " ")}${where}`, kind: "bad" };
    case "stopped":
      return { text: td ? `Stopped after touchdown${where}` : "Stopped early", kind: "plain" };
    default:
      return null;
  }
}

// Title and detail line of a past flight in the Flights drawer: the task (with wind) and
// seed, duration, date and HUD use; logs without a known task have no title.
export function itemText(log) {
  const result = resultText(log.result);
  if (!log.task) return { title: null, detail: optionLabel(log), result };
  const title = `${TASK_TITLES[log.task] ?? log.task}${log.windy ? " in wind" : ""}`;
  const who = log.pilot && log.pilot !== "human" ? log.pilot.toUpperCase() : null;
  const parts = [`Seed ${log.seed ?? "?"}`, fmtDuration(log.duration_s)];
  if (log.group === "demos") parts.push(fmtDate(log.mtime));
  if (who) parts.push(who);
  if (log.hud) parts.push("HUD");
  return { title, detail: parts.join(" · "), result };
}

export function optionLabel(log) {
  const parts = [];
  if (log.group === "demos") {
    parts.push(`Seed ${log.seed ?? "?"}`, fmtDuration(log.duration_s), fmtDate(log.mtime));
  } else if (log.group.startsWith("batch/")) {
    parts.push(`Seed ${log.seed ?? "?"}`);
    if (log.pilot) parts.push(log.pilot.toUpperCase());
    parts.push(fmtDuration(log.duration_s));
  } else {
    parts.push(log.path, fmtDuration(log.duration_s));
    if (log.pilot) parts.push(log.pilot === "human" ? "you" : log.pilot.toUpperCase());
  }
  return parts.join(" · ");
}

// A query matches the seed exactly when it is a number ("12" or "s12"), otherwise any
// text in the path or label.
export function matches(log, query) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const seed = /^s?(\d+)$/.exec(q);
  if (seed) return log.seed === Number(seed[1]);
  const t = itemText(log);
  return [log.path, optionLabel(log), t.title ?? "", t.detail, t.result?.text ?? ""].some((x) => x.toLowerCase().includes(q));
}

// [{label, options: [{value, label}], more}] in display order; `more` counts matching
// logs left out by the cap.
export function groupLogs(logs, query = "", cap = GROUP_CAP) {
  const groups = new Map();
  for (const log of logs) {
    if (!matches(log, query)) continue;
    if (!groups.has(log.group)) groups.set(log.group, []);
    groups.get(log.group).push(log);
  }
  const order = (g) => (g === "demos" ? 0 : g.startsWith("batch/") ? 2 : 1);
  const keys = [...groups.keys()].sort((a, b) => order(a) - order(b) || a.localeCompare(b));
  return keys.map((g) => {
    const items = groups.get(g);
    if (g === "demos") items.sort((a, b) => b.mtime - a.mtime);
    else if (g.startsWith("batch/")) items.sort((a, b) => (a.seed ?? 0) - (b.seed ?? 0));
    const label = g === "demos" ? "Your flights" : g.startsWith("batch/") ? `Batch ${g.slice(6)} (${items.length} flight${items.length === 1 ? "" : "s"})` : g ? `Recorded flights: ${g}` : "Recorded flights";
    const shown = items.slice(0, cap);
    return { label, options: shown.map((log) => ({ value: log.path, label: optionLabel(log), ...itemText(log) })), more: items.length - shown.length };
  });
}
