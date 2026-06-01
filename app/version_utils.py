"""Versão do app + parsing do CHANGELOG.md.

Padrão dos sites: arquivo `VERSION` (semver) e `CHANGELOG.md` na raiz do repo.
O finder procura em vários caminhos porque o Dockerfile de cada app copia a
árvore de um jeito diferente (raiz vs. subpasta `app/`).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _find(name: str) -> Path | None:
    candidates = [
        os.getenv(f"{name}_PATH"),
        _HERE / name,
        _HERE.parent / name,
        _HERE.parent.parent / name,
        Path.cwd() / name,
        Path.cwd().parent / name,
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def read_version() -> str:
    f = _find("VERSION")
    if f:
        try:
            return f.read_text(encoding="utf-8").strip() or "0.0.0"
        except OSError:
            pass
    return "0.0.0"


def parse_changelog() -> list[dict]:
    """Parse simples do CHANGELOG.md (Keep a Changelog).

    Retorna lista de releases: [{version, date, sections: [{type, items}]}].
    """
    f = _find("CHANGELOG.md")
    if not f:
        return []
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return []

    releases: list[dict] = []
    current: dict | None = None
    section: dict | None = None

    for line in text.splitlines():
        m = re.match(r"^##\s+\[?([^\]\s]+)\]?\s*-?\s*(.*)$", line)
        if m:
            current = {"version": m.group(1).strip(), "date": m.group(2).strip(" -"), "sections": []}
            releases.append(current)
            section = None
            continue
        m = re.match(r"^###\s+(.*)$", line)
        if m and current is not None:
            section = {"type": m.group(1).strip(), "items": []}
            current["sections"].append(section)
            continue
        m = re.match(r"^[-*]\s+(.*)$", line)
        if m and section is not None:
            section["items"].append(m.group(1).strip())

    return releases
