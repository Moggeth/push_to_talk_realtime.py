from __future__ import annotations

from pathlib import Path

import startup_integration


def test_render_linux_systemd_service_uses_context_paths(tmp_path: Path):
    context = startup_integration.StartupContext(
        script_dir=tmp_path / "repo",
        starter_script_path=tmp_path / "repo" / "start_push_to_talk.py",
        systemd_service_name="push-to-talk-realtime.service",
        macos_launch_agent_name="com.example.push-to-talk-realtime",
        python_executable="/venv/bin/python",
        openai_api_key="",
    )

    rendered = startup_integration.render_linux_systemd_service(context)

    assert f"WorkingDirectory={tmp_path / 'repo'}" in rendered
    assert f"ExecStart=/venv/bin/python {tmp_path / 'repo' / 'start_push_to_talk.py'}" in rendered
    assert "Environment=PUSH_TO_TALK_MANAGED_BY_SYSTEMD=1" in rendered


def test_toggle_run_on_startup_enables_and_refreshes(monkeypatch, tmp_path: Path):
    refresh_calls: list[str] = []
    logs: list[str] = []
    context = startup_integration.StartupContext(
        script_dir=tmp_path / "repo",
        starter_script_path=tmp_path / "repo" / "start_push_to_talk.py",
        systemd_service_name="push-to-talk-realtime.service",
        macos_launch_agent_name="com.example.push-to-talk-realtime",
        python_executable="/venv/bin/python",
        openai_api_key="",
    )
    monkeypatch.setattr(startup_integration, "is_run_on_startup_enabled", lambda ctx: False)
    monkeypatch.setattr(startup_integration, "enable_run_on_startup", lambda ctx: True)

    startup_integration.toggle_run_on_startup(
        context,
        refresh_tray_menu=lambda: refresh_calls.append("refresh"),
        log=lambda *args: logs.append(" ".join(map(str, args))),
    )

    assert refresh_calls == ["refresh"]
    assert logs == ["[Startup] Run on startup enabled."]
