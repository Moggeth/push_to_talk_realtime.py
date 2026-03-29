from __future__ import annotations

from datetime import datetime
from pathlib import Path

import history_store


def test_ensure_history_file_creates_parent_directory(tmp_path: Path):
    history_path = tmp_path / "logs" / "work_log.txt"

    history_store.ensure_history_file(history_path)

    assert history_path.exists()
    assert history_path.is_file()


def test_append_history_entry_writes_timestamped_single_line(monkeypatch, tmp_path: Path):
    class FixedDateTime:
        @staticmethod
        def now() -> datetime:
            return datetime(2026, 3, 30, 12, 34, 56)

    history_path = tmp_path / "logs" / "work_log.txt"
    logs: list[str] = []
    monkeypatch.setattr(history_store, "datetime", FixedDateTime)

    history_store.append_history_entry(
        history_path,
        "First line\nSecond line",
        "Dictation",
        log=lambda *args: logs.append(" ".join(map(str, args))),
    )

    assert history_path.read_text(encoding="utf-8") == (
        "- 2026-03-30 12:34:56 [Dictation] First line Second line\n"
    )
    assert logs == ["[Logged] - 2026-03-30 12:34:56 [Dictation] First line Second line"]
