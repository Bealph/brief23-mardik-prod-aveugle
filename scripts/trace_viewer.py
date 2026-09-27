"""Write finished spans to a self-contained HTML trace viewer (no server, no CDN).

Fallback for machines where the Jaeger container cannot run. The page lists
traces, draws each one as a span waterfall, and shows every attribute and
event of the selected span, content included.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan


def span_to_dict(span: ReadableSpan) -> dict[str, Any]:
    def plain(value: Any) -> Any:
        return list(value) if isinstance(value, tuple) else value

    return {
        "traceId": format(span.context.trace_id, "032x"),
        "spanId": format(span.context.span_id, "016x"),
        "parentId": format(span.parent.span_id, "016x") if span.parent else None,
        "name": span.name,
        "start": span.start_time or 0,
        "end": span.end_time or 0,
        "status": span.status.status_code.name,
        "statusDescription": span.status.description or "",
        "attributes": {k: plain(v) for k, v in (span.attributes or {}).items()},
        "events": [
            {
                "name": e.name,
                "time": e.timestamp,
                "attributes": {k: plain(v) for k, v in (e.attributes or {}).items()},
            }
            for e in span.events
        ],
        "resource": {k: plain(v) for k, v in span.resource.attributes.items()},
    }


def write_html(spans: Sequence[ReadableSpan], path: Path) -> Path:
    data = json.dumps([span_to_dict(s) for s in spans], ensure_ascii=False)
    # "</" must not appear inside the inline script.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PAGE.replace("__DATA__", data.replace("</", "<\\/")), encoding="utf-8")
    return path


_PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Mardik - traces OpenTelemetry</title>
<style>
:root {
  --surface: #fcfcfb; --panel: #f3f3f1; --ink: #1d1d1b; --ink-2: #55554f; --muted: #8a8a83;
  --grid: #e4e4e0; --bar: #6b7280; --critical: #d03b3b; --select: #e8eefc;
}
@media (prefers-color-scheme: dark) {
  :root { --surface: #1a1a19; --panel: #242422; --ink: #ededea; --ink-2: #b8b8b1;
          --muted: #85857e; --grid: #33332f; --bar: #9ca3af; --select: #2a3350; }
}
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.4 system-ui, "Segoe UI", sans-serif; background: var(--surface); color: var(--ink); }
header { padding: 12px 16px; border-bottom: 1px solid var(--grid); display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
header h1 { font-size: 16px; margin: 0; }
header .meta { color: var(--ink-2); }
main { display: grid; grid-template-columns: 340px 1fr; height: calc(100vh - 53px); }
#traces { overflow: auto; border-right: 1px solid var(--grid); }
.trace { padding: 8px 12px; border-bottom: 1px solid var(--grid); cursor: pointer; }
.trace:hover, .trace.sel { background: var(--select); }
.trace .t1 { font-weight: 600; }
.trace .t2 { color: var(--ink-2); font-size: 12px; word-break: break-all; }
.err { color: var(--critical); font-weight: 600; }
#detail { overflow: auto; padding: 12px 16px; }
.row { display: grid; grid-template-columns: 300px 1fr 80px; align-items: center; min-height: 26px; cursor: pointer; border-radius: 4px; }
.row:hover, .row.sel { background: var(--select); }
.row .name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-right: 8px; }
.track { position: relative; height: 26px; border-left: 1px solid var(--grid); }
.bar { position: absolute; top: 8px; height: 10px; min-width: 3px; border-radius: 4px; background: var(--bar); }
.bar.error { background: var(--critical); }
.dur { text-align: right; color: var(--ink-2); font-variant-numeric: tabular-nums; }
#tip { position: fixed; pointer-events: none; background: var(--panel); border: 1px solid var(--grid); padding: 6px 8px; border-radius: 4px; font-size: 12px; display: none; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; }
td, th { text-align: left; vertical-align: top; padding: 4px 8px; border-bottom: 1px solid var(--grid); }
th { color: var(--ink-2); font-weight: 600; width: 34%; }
td { white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
h2 { font-size: 15px; margin: 16px 0 4px; }
label { color: var(--ink-2); }
</style>
</head>
<body>
<header>
  <h1>Mardik - traces OpenTelemetry</h1>
  <span class="meta" id="summary"></span>
  <label><input type="checkbox" id="onlyErrors"> traces en erreur uniquement</label>
</header>
<main>
  <nav id="traces" aria-label="Traces"></nav>
  <section id="detail"><p class="meta">Choisir une trace à gauche.</p></section>
</main>
<div id="tip" role="tooltip"></div>
<script>
const SPANS = __DATA__;
const byTrace = {};
for (const s of SPANS) (byTrace[s.traceId] ||= []).push(s);
const ms = ns => (ns / 1e6);
const fmt = v => Array.isArray(v) ? "[" + v.join(", ") + "]" : String(v);
const esc = t => String(t).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function summarize(id) {
  const spans = byTrace[id];
  const ids = new Set(spans.map(s => s.spanId));
  const root = spans.find(s => !s.parentId || !ids.has(s.parentId)) || spans[0];
  const start = Math.min(...spans.map(s => s.start)), end = Math.max(...spans.map(s => s.end));
  return { id, spans, root, start, end, error: spans.some(s => s.status === "ERROR") };
}
const traces = Object.keys(byTrace).map(summarize).sort((a, b) => a.start - b.start);
document.getElementById("summary").textContent =
  traces.length + " traces, " + SPANS.length + " spans, " + traces.filter(t => t.error).length + " en erreur";

function renderList() {
  const only = document.getElementById("onlyErrors").checked;
  const nav = document.getElementById("traces");
  nav.innerHTML = "";
  for (const t of traces) {
    if (only && !t.error) continue;
    const a = t.root.attributes;
    const div = document.createElement("div");
    div.className = "trace";
    div.tabIndex = 0;
    div.innerHTML =
      '<div class="t1">' + (t.error ? '<span class="err">!! ERROR</span> ' : "OK ") + esc(t.root.name) +
      " - " + ms(t.end - t.start).toFixed(1) + " ms</div>" +
      '<div class="t2">' + esc(a["test.case_id"] || "") + "</div>" +
      '<div class="t2">session ' + esc(a["mardik.session.id"] || "n/a") + ", tour " + esc(a["mardik.turn.index"] ?? "n/a") +
      ", issue " + esc(a["mardik.turn.outcome"] || "n/a") + "</div>" +
      '<div class="t2">trace ' + t.id + "</div>";
    div.onclick = div.onkeydown = e => {
      if (e.type === "keydown" && e.key !== "Enter") return;
      document.querySelectorAll(".trace").forEach(x => x.classList.remove("sel"));
      div.classList.add("sel");
      renderTrace(t);
    };
    nav.appendChild(div);
  }
}

function ordered(t) {
  const out = [];
  const kids = id => t.spans.filter(s => s.parentId === id).sort((a, b) => a.start - b.start);
  const walk = (s, d) => { out.push([s, d]); kids(s.spanId).forEach(k => walk(k, d + 1)); };
  const ids = new Set(t.spans.map(s => s.spanId));
  t.spans.filter(s => !s.parentId || !ids.has(s.parentId)).sort((a, b) => a.start - b.start).forEach(r => walk(r, 0));
  return out;
}

function renderTrace(t) {
  const total = Math.max(t.end - t.start, 1);
  const tip = document.getElementById("tip");
  const det = document.getElementById("detail");
  det.innerHTML = "<h2>Trace " + t.id + "</h2>" +
    '<p class="meta">Cascade des spans : la barre place chaque span dans la durée de la trace (' +
    ms(total).toFixed(2) + " ms). Cliquer un span pour voir ses attributs et son contenu.</p>";
  const box = document.createElement("div");
  det.appendChild(box);
  const info = document.createElement("div");
  det.appendChild(info);
  const rows = ordered(t);
  for (const [s, depth] of rows) {
    const row = document.createElement("div");
    row.className = "row";
    row.tabIndex = 0;
    const left = (s.start - t.start) / total * 100, width = (s.end - s.start) / total * 100;
    const err = s.status === "ERROR";
    row.innerHTML =
      '<div class="name" style="padding-left:' + depth * 16 + 'px">' + (err ? '<span class="err">!! </span>' : "") +
      esc(s.name) + ' <span class="meta">' + s.status + "</span></div>" +
      '<div class="track"><div class="bar' + (err ? " error" : "") + '" style="left:' + left + "%;width:" + width + '%"></div></div>' +
      '<div class="dur">' + ms(s.end - s.start).toFixed(2) + " ms</div>";
    row.onmousemove = e => {
      tip.style.display = "block";
      tip.style.left = (e.clientX + 12) + "px"; tip.style.top = (e.clientY + 12) + "px";
      tip.textContent = s.name + " - " + s.status + " - " + ms(s.end - s.start).toFixed(2) + " ms";
    };
    row.onmouseleave = () => { tip.style.display = "none"; };
    row.onclick = row.onkeydown = e => {
      if (e.type === "keydown" && e.key !== "Enter") return;
      box.querySelectorAll(".row").forEach(x => x.classList.remove("sel"));
      row.classList.add("sel");
      renderSpan(s, info);
    };
    box.appendChild(row);
  }
  // Open the deepest ERROR span (where the failure starts), else the root.
  let pick = 0, deepest = -1;
  rows.forEach(([s, depth], i) => { if (s.status === "ERROR" && depth > deepest) { deepest = depth; pick = i; } });
  box.children[pick].click();
}

function table(obj) {
  const keys = Object.keys(obj).sort();
  if (!keys.length) return '<p class="meta">Aucun.</p>';
  return "<table>" + keys.map(k => "<tr><th>" + esc(k) + "</th><td>" + esc(fmt(obj[k])) + "</td></tr>").join("") + "</table>";
}

function renderSpan(s, el) {
  const head = {
    "span_id": s.spanId, "parent_span_id": s.parentId || "aucun (racine)", "status": s.status +
    (s.statusDescription ? " - " + s.statusDescription : ""), "durée (ms)": ms(s.end - s.start).toFixed(3),
  };
  el.innerHTML = "<h2>Span " + esc(s.name) + "</h2>" + table(head) +
    "<h2>Attributs</h2>" + table(s.attributes) +
    "<h2>Événements</h2>" + (s.events.length ? s.events.map(e => "<p><b>" + esc(e.name) + "</b></p>" + table(e.attributes)).join("") : '<p class="meta">Aucun.</p>') +
    "<h2>Ressource</h2>" + table(s.resource);
}

document.getElementById("onlyErrors").onchange = renderList;
renderList();
const firstTrace = document.querySelector(".trace");
if (firstTrace) firstTrace.click();
</script>
</body>
</html>
"""
