"""Prove that the integration suite catches every documented incident.

For each incident, the original defect is re-injected into a throwaway copy of
the repository and ``pytest tests/integration`` is run there. The incident is
"detected" only if at least one integration test fails. The working tree is
never modified.

Usage:  uv run python scripts/verify_incident_detection.py
Exit code 0 when the clean copy passes and every incident is detected.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COPIED = ("src", "tests", "sessions", "pyproject.toml")


@dataclass(frozen=True)
class Incident:
    key: str
    title: str
    path: str
    fixed: str  # exact snippet present in the corrected code
    defect: str  # the original, defective version of that snippet


INCIDENTS = (
    Incident(
        "INC-01",
        "rejeu limité au dernier message (perte du contexte de session)",
        "src/mardik/runner.py",
        "    for message in previous:\n"
        "        store.append(session_id, message)\n"
        "        if message.get(\"role\") == \"user\":\n"
        "            store.record_turn(session_id)\n",
        "",
    ),
    Incident(
        "INC-02",
        "timeout du modèle avalé puis transformé en None",
        "src/mardik/agent.py",
        '                raise LLMTimeoutError("LLM invocation exceeded its deadline") from exc\n',
        "                return None  # type: ignore[return-value]\n",
    ),
    Incident(
        "INC-02b",
        "timeout du SDK Azure non converti (ServiceResponseTimeoutError)",
        "src/mardik/llm.py",
        '            raise TimeoutError(f"Azure SDK timeout: {type(exc).__name__}") from exc\n',
        "            raise\n",
    ),
    Incident(
        "INC-03",
        "compteur de tours non atomique sous concurrence",
        "src/mardik/session.py",
        "    def record_turn(self, session_id: str) -> int:\n"
        '        """Count one more turn for the session and return its 1-based index."""\n'
        "        with self._lock:\n",
        "    def record_turn(self, session_id: str) -> int:\n"
        '        """Count one more turn for the session and return its 1-based index."""\n'
        "        if True:\n",
    ),
    Incident(
        "INC-04",
        "contexte de trace non propagé au thread du modèle",
        "src/mardik/agent.py",
        '                box["reply"] = ctx.run(self._invoke_llm_sync, messages)\n',
        '                box["reply"] = self._invoke_llm_sync(messages)\n',
    ),
    Incident(
        "INC-05",
        "télémétrie ignorée par l'assemblage de production",
        "src/mardik/app.py",
        "        telemetry=telemetry,\n",
        "",
    ),
    Incident(
        "INC-06",
        "outils jamais transmis au modèle réel",
        "src/mardik/llm.py",
        "    runnable = model.bind_tools(list(tools.values())) if tools else model\n",
        "    runnable = model\n",
    ),
    Incident(
        "INC-07",
        "nom du déploiement ignoré par le client Azure (model_name=)",
        "src/mardik/llm.py",
        "        model=settings.azure_model,\n",
        "        model_name=settings.azure_model,\n",
    ),
)


def copy_repo(target: Path) -> None:
    for name in COPIED:
        source = REPO / name
        if source.is_dir():
            shutil.copytree(source, target / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target / name)


def run_integration(root: Path) -> tuple[int, str]:
    env = dict(os.environ, MARDIK_SESSIONS_DIR=str(root / "sessions"))
    env.pop("MARDIK_TRACE_ARTIFACTS", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration", "-q", "-p", "no:cacheprovider"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout


def failed_tests(output: str) -> list[str]:
    return sorted(set(re.findall(r"^FAILED (\S+)", output, flags=re.MULTILINE)))


def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory(prefix="mardik-verify-") as tmp:
        clean = Path(tmp) / "clean"
        clean.mkdir()
        copy_repo(clean)
        code, out = run_integration(clean)
        summary = out.strip().splitlines()[-1] if out.strip() else "no output"
        print(f"code corrigé : {summary}")
        if code != 0:
            print("La suite d'intégration doit passer sur le code corrigé.")
            print(out)
            return 1

        for incident in INCIDENTS:
            root = Path(tmp) / incident.key
            root.mkdir()
            copy_repo(root)
            target = root / incident.path
            source = target.read_text(encoding="utf-8")
            if source.count(incident.fixed) != 1:
                print(f"{incident.key} : extrait introuvable dans {incident.path}, script à mettre à jour")
                ok = False
                continue
            target.write_text(source.replace(incident.fixed, incident.defect), encoding="utf-8")
            code, out = run_integration(root)
            failures = failed_tests(out)
            detected = code != 0 and bool(failures)
            ok &= detected
            verdict = "DÉTECTÉ" if detected else "NON DÉTECTÉ"
            print(f"{incident.key} {incident.title} : {verdict}, {len(failures)} test(s) rouge(s)")
            for name in failures:
                print(f"    {name}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
