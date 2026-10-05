// Groups the server's log list for the flight menu: your demonstrations (newest first),
// other recorded flights, and one group per batch. Large groups are capped; a filter
// (seed, run id or path text) narrows them. Pure functions, no DOM (tested with Node).

export const GROUP_CAP = 50; // options shown per group before "… N more"

const pad2 = (n) => String(n).padStart(2, "0");
export const fmtDuration = (s) => `${Math.floor(s / 60)}:${pad2(Math.floor(s % 60))}`;

function fmtDate(epochS) {
  const d = new Date(epochS * 1000);
  return `${d.toLocaleString("en-US", { month: "short" })} ${d.getDate()}, ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
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
  return log.path.toLowerCase().includes(q) || optionLabel(log).toLowerCase().includes(q);
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
    return { label, options: shown.map((log) => ({ value: log.path, label: optionLabel(log) })), more: items.length - shown.length };
  });
}
