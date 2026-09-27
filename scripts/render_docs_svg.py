"""Render the observability documents as SVG.

- docs/pipeline-observabilite-eval.svg : the observability and evaluation
  pipeline, with real traces and spans produced by the agent at render time.
- docs/schema-observabilite.svg : docs/schema-observabilite.md laid out as SVG
  (headings, paragraphs, lists, tables; the two Mermaid blocks are redrawn).

Usage:  uv run python scripts/render_docs_svg.py   (or: make docs-svg)
Text width is estimated, not measured: check the output in a browser.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

# --- Visual tokens -----------------------------------------------------------
SANS = "Segoe UI, system-ui, -apple-system, sans-serif"
MONO = "Consolas, ui-monospace, monospace"
SURFACE = "#fcfcfb"
PANEL = "#f3f3f1"
INK = "#1d1d1b"
INK2 = "#55554f"
MUTED = "#8a8a83"
GRID = "#d9d9d4"
BAR = "#6b7280"
CRITICAL = "#d03b3b"  # status "critical": always paired with the ERROR label
ACCENT = "#2a5bd7"  # implemented pipeline edges

CHAR_W = {"normal": 0.51, "bold": 0.55, "code": 0.56}


def text_width(text: str, size: float, style: str = "normal") -> float:
    return len(text) * size * CHAR_W[style]


# --- Inline Markdown ------------------------------------------------------------
Segment = tuple[str, str]  # (text, style) with style in normal | bold | code


def inline_segments(text: str) -> list[Segment]:
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    segments: list[Segment] = []
    for part in re.split(r"(`[^`]+`|\*\*[^*]+\*\*)", text):
        if not part:
            continue
        if part.startswith("`"):
            segments.append((part[1:-1], "code"))
        elif part.startswith("**"):
            segments.append((part[2:-2], "bold"))
        else:
            segments.append((part, "normal"))
    return segments


def wrap(segments: Sequence[Segment], width: float, size: float) -> list[list[Segment]]:
    """Greedy word wrap over styled segments; returns lines of segments."""
    words: list[Segment] = []
    for text, style in segments:
        for token in re.split(r"(\s+)", text):
            if token:
                words.append((token, style))
    lines: list[list[Segment]] = [[]]
    used = 0.0
    for token, style in words:
        w = text_width(token, size, style)
        if token.isspace():
            if lines[-1]:
                lines[-1].append((" ", style))
                used += text_width(" ", size, style)
            continue
        if used + w > width and lines[-1]:
            while lines[-1] and lines[-1][-1][0] == " ":
                lines[-1].pop()
            lines.append([])
            used = 0.0
        while w > width:  # a single token longer than the line: hard split
            fit = max(1, int(width / (size * CHAR_W[style])))
            lines[-1].append((token[:fit], style))
            lines.append([])
            token, w, used = token[fit:], text_width(token[fit:], size, style), 0.0
        lines[-1].append((token, style))
        used += w
    return [line for line in lines if line] or [[("", "normal")]]


def svg_text_line(x: float, y: float, line: Sequence[Segment], size: float, fill: str = INK,
                  weight: str | None = None) -> str:
    spans = []
    for text, style in line:
        attrs = ""
        if style == "code":
            attrs = f' font-family="{MONO}" fill="{INK2}"'
        elif style == "bold" or weight == "bold":
            attrs = ' font-weight="600"'
        spans.append(f"<tspan{attrs}>{escape(text)}</tspan>")
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{SANS}" font-size="{size}" '
            f'fill="{fill}" xml:space="preserve">{"".join(spans)}</text>')


def plain(x: float, y: float, text: str, size: float = 13, fill: str = INK, weight: str = "400",
          anchor: str = "start", family: str = SANS) -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(text)}</text>')


def box(x: float, y: float, w: float, h: float, fill: str = SURFACE, stroke: str = GRID,
        dashed: bool = False, radius: int = 8, width: float = 1.5) -> str:
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{radius}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"{dash}/>')


def arrow(x1: float, y1: float, x2: float, y2: float, color: str = ACCENT,
          dashed: bool = False) -> str:
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    marker = "arrow-dash" if dashed else "arrow"
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" '
            f'stroke-width="2"{dash} marker-end="url(#{marker})"/>')


def svg_document(width: float, height: float, body: list[str], title: str) -> str:
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="{escape(title)}">',
            f"<title>{escape(title)}</title>",
            "<defs>",
            f'<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
            f'markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
            f'fill="{ACCENT}"/></marker>',
            f'<marker id="arrow-dash" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
            f'markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
            f'fill="{MUTED}"/></marker>',
            "</defs>",
            f'<rect width="100%" height="100%" fill="{SURFACE}"/>',
            *body,
            "</svg>",
            "",
        ]
    )


# --- Real traces ------------------------------------------------------------------
def collect_traces() -> dict[str, list]:
    """Run three scenarios through the real agent and return their spans."""
    import os

    os.environ.setdefault("MARDIK_SESSIONS_DIR", str(REPO / "sessions"))
    from conftest import ContextAwareFakeLLM, TimeoutFakeLLM
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from tracing_support import baggage_test_context

    from mardik.agent import Agent
    from mardik.errors import MardikError
    from mardik.runner import load_session, replay, replay_turn_by_turn
    from mardik.session import SessionStore
    from mardik.telemetry import build_telemetry
    from mardik.tools import DEFAULT_TOOLS

    out: dict[str, list] = {}
    scenarios = (
        ("sain", ContextAwareFakeLLM(), lambda a: replay(load_session("replay_delivery"), a,
                                                         SessionStore())),
        ("erreur", TimeoutFakeLLM(), lambda a: replay(load_session("incident_timeout"), a,
                                                      SessionStore())),
        ("session", ContextAwareFakeLLM(),
         lambda a: replay_turn_by_turn(load_session("replay_clarification"), a, SessionStore())),
    )
    for key, llm, action in scenarios:
        exporter = InMemorySpanExporter()
        telemetry = build_telemetry(span_exporter=exporter, metric_reader=InMemoryMetricReader(),
                                    capture_content=True)
        agent = Agent(llm, DEFAULT_TOOLS, telemetry)  # scripted: no model name to claim
        with baggage_test_context(f"schema::{key}", "render"):
            try:
                action(agent)
            except MardikError:
                pass
        out[key] = list(exporter.get_finished_spans())
    return out


def ordered_spans(spans: Sequence) -> list[tuple[object, int]]:
    ids = {s.context.span_id for s in spans}
    kids = lambda sid: sorted((s for s in spans if s.parent and s.parent.span_id == sid),  # noqa: E731
                              key=lambda s: s.start_time)
    rows: list[tuple[object, int]] = []

    def walk(span, depth: int) -> None:
        rows.append((span, depth))
        for child in kids(span.context.span_id):
            walk(child, depth + 1)

    for root in sorted((s for s in spans if not s.parent or s.parent.span_id not in ids),
                       key=lambda s: s.start_time):
        walk(root, 0)
    return rows


def waterfall(x: float, y: float, w: float, spans: Sequence, title: str,
              detail_attrs: Sequence[str]) -> tuple[list[str], float]:
    """Draw one trace as a span waterfall plus the attributes of its key span."""
    from opentelemetry.trace import StatusCode

    parts: list[str] = []
    rows = ordered_spans(spans)
    start = min(s.start_time for s in spans)
    total = max(max(s.end_time for s in spans) - start, 1)
    trace_id = format(spans[0].context.trace_id, "032x")
    name_w, dur_w = 250, 80
    track_x, track_w = x + name_w, w - name_w - dur_w - 20
    row_h = 30
    height = 64 + row_h * len(rows)
    key = next((s for s, _ in reversed(rows) if s.status.status_code is StatusCode.ERROR), rows[0][0])
    attrs = {k: v for k, v in (key.attributes or {}).items() if k in detail_attrs}
    for event in key.events:
        if event.name == "exception" and event.attributes:
            attrs["exception"] = (f"{event.attributes.get('exception.type')}: "
                                  f"{event.attributes.get('exception.message')}")
    height += 34 + 22 * len(attrs) + 16
    parts.append(box(x - 14, y, w + 28, height, fill=SURFACE))
    parts.append(plain(x, y + 26, title, 15, weight="600"))
    parts.append(plain(x, y + 46, f"trace_id {trace_id}", 12, INK2, family=MONO))
    cy = y + 64
    for span, depth in rows:
        err = span.status.status_code is StatusCode.ERROR
        label = ("!! " if err else "") + span.name
        status = "ERROR" if err else span.status.status_code.name
        parts.append(plain(x + depth * 18, cy + 19, label, 13, CRITICAL if err else INK,
                           weight="600" if err else "400"))
        parts.append(plain(x + depth * 18 + text_width(label, 13) + 10, cy + 19, status, 11,
                           INK2))
        parts.append(f'<line x1="{track_x:.1f}" y1="{cy:.1f}" x2="{track_x:.1f}" '
                     f'y2="{cy + row_h:.1f}" stroke="{GRID}"/>')
        bx = track_x + (span.start_time - start) / total * track_w
        bw = max((span.end_time - span.start_time) / total * track_w, 3)
        parts.append(f'<rect x="{bx:.1f}" y="{cy + 10:.1f}" width="{bw:.1f}" height="10" rx="4" '
                     f'fill="{CRITICAL if err else BAR}"/>')
        ms = (span.end_time - span.start_time) / 1e6
        parts.append(plain(x + w, cy + 19, f"{ms:.3f} ms", 12, INK2, anchor="end"))
        cy += row_h
    cy += 12
    parts.append(plain(x, cy + 14, f"Attributs du span {key.name}"
                       + (" (span en erreur le plus profond)" if key.status.status_code is
                          StatusCode.ERROR else ""), 12, INK2, weight="600"))
    cy += 24
    for k, v in attrs.items():
        value = "[" + ", ".join(map(str, v)) + "]" if isinstance(v, tuple) else str(v)
        parts.append(plain(x, cy + 14, k, 12, INK, family=MONO))
        parts.append(plain(x + 260, cy + 14, value[:70], 12, INK2, family=MONO))
        cy += 22
    return parts, height


# --- Pipeline SVG -------------------------------------------------------------------
def column(x: float, y: float, w: float, title: str, items: Sequence[tuple[str, str]],
           dashed: bool = False) -> tuple[list[str], float]:
    parts = []
    h = 46 + sum(18 * len(wrap(inline_segments(desc), w - 32, 12)) + 30 for _, desc in items) + 6
    parts.append(box(x, y, w, h, fill=PANEL, stroke=MUTED if dashed else GRID, dashed=dashed))
    parts.append(plain(x + 16, y + 28, title, 15, weight="600"))
    cy = y + 46
    for name, desc in items:
        lines = wrap(inline_segments(desc), w - 32, 12)
        ih = 18 * len(lines) + 24
        parts.append(box(x + 10, cy, w - 20, ih, fill=SURFACE, stroke=MUTED if dashed else GRID,
                         dashed=dashed, radius=6, width=1))
        parts.append(plain(x + 18, cy + 17, name, 13, weight="600"))
        for i, line in enumerate(lines):
            parts.append(svg_text_line(x + 18, cy + 34 + i * 18, line, 12, INK2))
        cy += ih + 6
    return parts, h


def render_pipeline(traces: dict[str, list]) -> str:
    W = 1760
    body: list[str] = []
    body.append(plain(40, 50, "Mardik : pipeline d'observabilité et d'évaluation", 26, weight="700"))
    body.append(plain(40, 78, "Trait plein : implémenté et testé dans ce dépôt. Pointillés : "
                      "cible, non implémentée. !! et rouge : span en ERROR.", 14, INK2))

    y0 = 110
    col_w, gap = 300, 42
    cols = [
        ("1. Sources", [
            ("Tests d'intégration", "Rejouent `sessions/*.json` via `build_agent()` ; posent "
             "`test.case_id`, `test.run_id` dans le baggage"),
            ("Production", "CLI `mardik.app`, modèle Azure Kimi-K2.6"),
        ]),
        ("2. Agent instrumenté", [
            ("agent.turn", "1 trace par tour ; `mardik.session.id`, `mardik.turn.index`, "
             "`mardik.turn.outcome`"),
            ("llm.invoke", "Thread dédié, contexte copié ; `gen_ai.request.model`, outils demandés"),
            ("tool.call", "`gen_ai.tool.name`, statut, arguments, résultat"),
        ]),
        ("3. SDK OpenTelemetry", [
            ("Traces", "`BaggageTestContextProcessor` puis `SimpleSpanProcessor` (test) ou "
             "`BatchSpanProcessor` (prod)"),
            ("Métriques", "7 instruments, étiquettes bornées : `latency_ms`, `turns_total`, "
             "`errors_total`, `tool_calls_total`..."),
            ("Logs", "structlog JSON + `trace_id`, `span_id`"),
        ]),
        ("4. Export", [
            ("OTLP gRPC :4317", "Jaeger (interface :16686)"),
            ("Mémoire", "Rapport pytest sur test rouge ; JSON par test en CI"),
            ("Visionneuse HTML", "`make demo-traces`, sans Docker"),
            ("Métriques, logs", "Console / OTLP ; stdout"),
        ]),
        ("5. Exploitation", [
            ("Diagnostic", "Span ERROR le plus profond, table des signatures, diff avec la "
             "dernière trace verte"),
            ("CI", "`verify_incident_detection.py` : 5 incidents réinjectés, 5 détectés"),
            ("SLI", "Taux de terminaison, erreurs d'outil, latence p95 ; cibles à calibrer"),
        ]),
    ]
    heights = []
    for i, (title, items) in enumerate(cols):
        parts, h = column(40 + i * (col_w + gap), y0, col_w, title, items)
        body.extend(parts)
        heights.append(h)
    row_h = max(heights)
    for i in range(len(cols) - 1):
        x1 = 40 + i * (col_w + gap) + col_w
        body.append(arrow(x1 + 4, y0 + 60, x1 + gap - 4, y0 + 60))

    # Evaluation row.
    ey = y0 + row_h + 60
    body.append(plain(40, ey - 18, "Pipeline d'évaluation (4e pilier)", 18, weight="700"))
    eval_cols = [
        ("Implémenté : assertions", [
            ("Déterministes, dans les tests", "Réponse attendue par session, trajectoire "
             "d'outils, isolation des sessions, index de tour"),
        ], False),
        ("Cible : jeu de référence", [
            ("Sessions + réponses attendues", "Issues de la production, anonymisées, "
             "versionnées"),
        ], True),
        ("Cible : évaluateur", [
            ("Règles métier + juge", "Score par critère : exactitude, respect de la consigne"),
        ], True),
        ("Cible : score rattaché", [
            ("Au trace_id", "Relie une mauvaise réponse à sa trace et à ses spans"),
        ], True),
        ("Cible : SLO d'exactitude", [
            ("Distribution des scores", "Seuil calibré ; violation = blocage de déploiement"),
        ], True),
    ]
    eh = []
    for i, (title, items, dashed) in enumerate(eval_cols):
        parts, h = column(40 + i * (col_w + gap), ey, col_w, title, items, dashed=dashed)
        body.extend(parts)
        eh.append(h)
    for i in range(len(eval_cols) - 1):
        x1 = 40 + i * (col_w + gap) + col_w
        body.append(arrow(x1 + 4, ey + 60, x1 + gap - 4, ey + 60, MUTED, dashed=True))
    # Traces feed evaluation.
    body.append(arrow(40 + 4 * (col_w + gap) + col_w / 2, y0 + row_h + 4,
                      40 + 3 * (col_w + gap) + col_w / 2, ey - 4, MUTED, dashed=True))

    # Real traces.
    ty = ey + max(eh) + 70
    body.append(plain(40, ty - 30, "Voir les traces et les spans : traces réelles produites par "
                      "l'agent au rendu de ce schéma", 18, weight="700"))
    detail = ("mardik.session.id", "mardik.turn.index", "mardik.turn.outcome",
              "mardik.context.messages", "mardik.turn.user_message", "mardik.turn.reply",
              "gen_ai.operation.name", "gen_ai.request.model", "mardik.llm.input_messages",
              "error.type", "test.case_id")
    half = (W - 80 - 60) / 2
    p1, h1 = waterfall(54, ty, half - 28, traces["sain"], "Tour sain : replay_delivery, tour 2 (modèle scripté : durées quasi nulles)",
                       detail)
    p2, h2 = waterfall(54 + half + 60, ty, half - 28, traces["erreur"],
                       "Incident : timeout du modèle (INC-02), session incident_timeout", detail)
    body.extend(p1 + p2)

    # One session = N traces.
    sy = ty + max(h1, h2) + 40
    session = [s for s in traces["session"] if s.name == "agent.turn"]
    session.sort(key=lambda s: s.attributes["mardik.turn.index"])
    sh = 70 + 34 * len(session)
    body.append(box(40, sy, W - 80, sh, fill=SURFACE))
    body.append(plain(54, sy + 28, "Une session = N traces reliées par mardik.session.id "
                      "(replay_clarification, rejouée tour par tour)", 15, weight="600"))
    for i, span in enumerate(session):
        cy = sy + 52 + i * 34
        a = span.attributes
        body.append(box(54, cy, W - 108, 26, fill=PANEL, radius=5, width=1))
        body.append(plain(66, cy + 18, f"trace {format(span.context.trace_id, '032x')}", 12,
                          INK2, family=MONO))
        body.append(plain(420, cy + 18, f"agent.turn  session={a['mardik.session.id']}  "
                          f"tour={a['mardik.turn.index']}  issue={a['mardik.turn.outcome']}  "
                          f"réponse=« {a.get('mardik.turn.reply', '')[:60]} »", 12, INK,
                          family=MONO))
    height = sy + sh + 40
    return svg_document(W, height, body, "Mardik : pipeline d'observabilité et d'évaluation")


# --- Markdown -> SVG --------------------------------------------------------------------
def md_blocks(md: str) -> list[tuple[str, object]]:
    blocks: list[tuple[str, object]] = []
    lines = md.split("\n")
    i = 0
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(("p", " ".join(s.strip() for s in para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            flush()
            lang = line[3:].strip()
            j = i + 1
            while not lines[j].startswith("```"):
                j += 1
            blocks.append(("code", (lang, lines[i + 1:j])))
            i = j + 1
            continue
        if line.startswith("#"):
            flush()
            level = len(line) - len(line.lstrip("#"))
            blocks.append((f"h{level}", line[level:].strip()))
        elif line.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.match(r"^[\s|:-]+$", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            blocks.append(("table", rows))
            continue
        elif line.startswith("> "):
            flush()
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i][1:].strip())
                i += 1
            blocks.append(("quote", " ".join(quote)))
            continue
        elif re.match(r"^(- |\d+\. )", line):
            flush()
            items = []
            while i < len(lines) and (re.match(r"^(- |\d+\. )", lines[i])
                                      or (lines[i].startswith("  ") and items)):
                if re.match(r"^(- |\d+\. )", lines[i]):
                    items.append(re.sub(r"^(- |\d+\. )", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            blocks.append(("list", items))
            continue
        elif not line.strip():
            flush()
        else:
            para.append(line)
        i += 1
    flush()
    return blocks


def diagram_overview(x: float, y: float, w: float) -> tuple[list[str], float]:
    parts = []
    stages = [("Test d'intégration", "baggage test.*"), ("agent.turn", "llm.invoke, tool.call"),
              ("SDK OpenTelemetry", "processeurs, métriques, logs"),
              ("Exports", "OTLP Jaeger, mémoire, HTML")]
    bw = (w - 3 * 50) / 4
    for i, (name, sub) in enumerate(stages):
        bx = x + i * (bw + 50)
        parts.append(box(bx, y, bw, 64, fill=PANEL))
        parts.append(plain(bx + bw / 2, y + 28, name, 14, weight="600", anchor="middle"))
        parts.append(plain(bx + bw / 2, y + 48, sub, 12, INK2, anchor="middle"))
        if i:
            parts.append(arrow(bx - 46, y + 32, bx - 4, y + 32))
    parts.append(plain(x, y + 90, "Voir aussi docs/pipeline-observabilite-eval.svg pour le "
                       "détail et des traces réelles.", 12, MUTED))
    return parts, 104


def diagram_hierarchy(x: float, y: float, w: float) -> tuple[list[str], float]:
    parts = [box(x, y, 260, 50, fill=PANEL),
             plain(x + 130, y + 22, "agent.turn", 14, weight="600", anchor="middle"),
             plain(x + 130, y + 40, "racine, 1 par tour", 12, INK2, anchor="middle")]
    for i, (name, sub) in enumerate((("llm.invoke", "appel du modèle, thread dédié"),
                                     ("tool.call", "1 par outil demandé"))):
        cy = y + 70 + i * 66
        parts.append(box(x + 200, cy, 260, 50, fill=SURFACE))
        parts.append(plain(x + 330, cy + 22, name, 14, weight="600", anchor="middle"))
        parts.append(plain(x + 330, cy + 40, sub, 12, INK2, anchor="middle"))
        parts.append(f'<path d="M{x + 60},{y + 50} V{cy + 25} H{x + 196}" fill="none" '
                     f'stroke="{ACCENT}" stroke-width="2" marker-end="url(#arrow)"/>')
    return parts, 206


def render_markdown(md: str, title: str) -> str:
    W, M = 1400, 60
    CW = W - 2 * M
    body: list[str] = []
    y = 40.0
    mermaid = 0
    for kind, content in md_blocks(md):
        if kind in ("h1", "h2", "h3"):
            size = {"h1": 28, "h2": 21, "h3": 17}[kind]
            y += {"h1": 10, "h2": 26, "h3": 16}[kind]
            for line in wrap(inline_segments(str(content)), CW, size):
                y += size * 1.3
                body.append(svg_text_line(M, y, line, size, weight="bold"))
            if kind == "h2":
                body.append(f'<line x1="{M}" y1="{y + 10:.1f}" x2="{W - M}" y2="{y + 10:.1f}" '
                            f'stroke="{GRID}"/>')
                y += 10
            y += 12
        elif kind == "p":
            lines = wrap(inline_segments(str(content)), CW, 14)
            for line in lines:
                y += 21
                body.append(svg_text_line(M, y, line, 14))
            y += 12
        elif kind == "quote":
            lines = wrap(inline_segments(str(content)), CW - 30, 13)
            body.append(f'<rect x="{M}" y="{y + 4:.1f}" width="4" height="{21 * len(lines) + 8}" '
                        f'fill="{GRID}"/>')
            for line in lines:
                y += 21
                body.append(svg_text_line(M + 18, y, line, 13, INK2))
            y += 18
        elif kind == "list":
            for item in content:  # type: ignore[union-attr]
                lines = wrap(inline_segments(item), CW - 26, 14)
                for n, line in enumerate(lines):
                    y += 21
                    if n == 0:
                        body.append(f'<circle cx="{M + 7}" cy="{y - 5:.1f}" r="3" fill="{INK2}"/>')
                    body.append(svg_text_line(M + 22, y, line, 14))
                y += 4
            y += 10
        elif kind == "code":
            lang, code_lines = content  # type: ignore[misc]
            if lang == "mermaid":
                mermaid += 1
                draw = diagram_overview if mermaid == 1 else diagram_hierarchy
                parts, h = draw(M, y + 10, CW)
                body.extend(parts)
                y += h + 24
            else:
                for line in code_lines:
                    y += 18
                    body.append(plain(M, y, line, 12, INK2, family=MONO))
                y += 12
        elif kind == "table":
            rows = content  # type: ignore[assignment]
            n = max(len(r) for r in rows)
            rows = [r + ["non disponible"] * (n - len(r)) for r in rows]
            size, pad, lh = 12.5, 8, 18
            want = [max(min(text_width(c, size), 420) for c in col) + 2 * pad
                    for col in zip(*rows)]
            scale = min(1.0, CW / sum(want))
            widths = [w * scale for w in want]
            spare = CW - sum(widths)
            widths = [w + spare * w / sum(widths) for w in widths]
            for r_i, row in enumerate(rows):
                cells = [wrap(inline_segments(c), w - 2 * pad, size) for c, w in zip(row, widths)]
                rh = max(len(c) for c in cells) * lh + 2 * pad - 2
                body.append(f'<rect x="{M}" y="{y:.1f}" width="{CW}" height="{rh:.1f}" '
                            f'fill="{PANEL if r_i == 0 else SURFACE}" stroke="{GRID}"/>')
                cx = M
                for cell, w in zip(cells, widths):
                    for k, line in enumerate(cell):
                        body.append(svg_text_line(cx + pad, y + pad + 12 + k * lh, line, size,
                                                  weight="bold" if r_i == 0 else None))
                    cx += w
                    if cx < M + CW - 1:
                        body.append(f'<line x1="{cx:.1f}" y1="{y:.1f}" x2="{cx:.1f}" '
                                    f'y2="{y + rh:.1f}" stroke="{GRID}"/>')
                y += rh
            y += 18
    return svg_document(W, y + 40, body, title)


def main() -> None:
    docs = REPO / "docs"
    traces = collect_traces()
    (docs / "pipeline-observabilite-eval.svg").write_text(render_pipeline(traces),
                                                          encoding="utf-8", newline="\n")
    md = (docs / "schema-observabilite.md").read_text(encoding="utf-8")
    (docs / "schema-observabilite.svg").write_text(
        render_markdown(md, "Schéma d'observabilité de l'agent Mardik"), encoding="utf-8",
        newline="\n")
    print("écrit : docs/pipeline-observabilite-eval.svg, docs/schema-observabilite.svg")


if __name__ == "__main__":
    main()
