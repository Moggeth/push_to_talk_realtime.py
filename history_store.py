from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path


def ensure_history_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()


def append_history_entry(
    path: Path,
    text: str,
    source: str,
    *,
    log: Callable[..., None],
) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sanitized = " ".join(text.strip().splitlines())
    line = f"- {timestamp} [{source}] {sanitized}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as exc:
        log("[Work log error]", exc)
    else:
        log(f"[Logged] {line}")
