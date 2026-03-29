from __future__ import annotations

import os
import platform
import plistlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StartupContext:
    script_dir: Path
    starter_script_path: Path
    systemd_service_name: str
    macos_launch_agent_name: str
    python_executable: str
    openai_api_key: str


def windows_startup_script_path() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        return (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
            / "push-to-talk-realtime.cmd"
        )
    return (
        Path.home()
        / "AppData"
        / "Roaming"
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "push-to-talk-realtime.cmd"
    )


def macos_launch_agent_path(name: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{name}.plist"


def linux_user_service_path(service_name: str) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / service_name


def startup_env_file_path() -> Path:
    return Path.home() / ".config" / "push-to-talk-realtime.env"


def startup_python_executable(sys_executable: str) -> str:
    if platform.system() == "Windows":
        pythonw = Path(sys_executable).with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return sys_executable


def render_linux_systemd_service(context: StartupContext) -> str:
    env_path = startup_env_file_path()
    return "\n".join(
        [
            "[Unit]",
            "Description=Push-to-talk Whisper tray app",
            "After=graphical-session.target",
            "PartOf=graphical-session.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={context.script_dir}",
            f"EnvironmentFile=-{env_path}",
            "Environment=PUSH_TO_TALK_MANAGED_BY_SYSTEMD=1",
            f"ExecStart={startup_python_executable(context.python_executable)} {context.starter_script_path}",
            "Restart=always",
            "RestartSec=2",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def render_windows_startup_script(context: StartupContext) -> str:
    return "\r\n".join(
        [
            "@echo off",
            f'start "" "{startup_python_executable(context.python_executable)}" "{context.starter_script_path}"',
            "",
        ]
    )


def render_macos_launch_agent(context: StartupContext) -> bytes:
    payload = {
        "Label": context.macos_launch_agent_name,
        "ProgramArguments": [
            startup_python_executable(context.python_executable),
            str(context.starter_script_path),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(context.script_dir),
        "EnvironmentVariables": {"PUSH_TO_TALK_MANAGED_BY_SYSTEMD": "1"},
    }
    return plistlib.dumps(payload)


def ensure_startup_env_file(context: StartupContext) -> None:
    env_path = startup_env_file_path()
    if env_path.exists():
        return
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Add OPENAI_API_KEY here if it is not already in your login environment."]
    if context.openai_api_key:
        lines.append(f"OPENAI_API_KEY={context.openai_api_key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def is_run_on_startup_enabled(context: StartupContext) -> bool:
    system = platform.system()
    if system == "Linux":
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-enabled", context.systemd_service_name],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            return False
        return result.returncode == 0
    if system == "Windows":
        return windows_startup_script_path().exists()
    if system == "Darwin":
        return macos_launch_agent_path(context.macos_launch_agent_name).exists()
    return False


def enable_run_on_startup(context: StartupContext) -> bool:
    system = platform.system()
    if system == "Linux":
        ensure_startup_env_file(context)
        service_path = linux_user_service_path(context.systemd_service_name)
        service_path.parent.mkdir(parents=True, exist_ok=True)
        service_path.write_text(render_linux_systemd_service(context), encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", context.systemd_service_name],
            check=True,
        )
        return True
    if system == "Windows":
        startup_path = windows_startup_script_path()
        startup_path.parent.mkdir(parents=True, exist_ok=True)
        startup_path.write_text(render_windows_startup_script(context), encoding="utf-8")
        return True
    if system == "Darwin":
        plist_path = macos_launch_agent_path(context.macos_launch_agent_name)
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_bytes(render_macos_launch_agent(context))
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
        subprocess.run(["launchctl", "load", "-w", str(plist_path)], check=True)
        return True
    return False


def disable_run_on_startup(context: StartupContext) -> bool:
    system = platform.system()
    if system == "Linux":
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", context.systemd_service_name],
            check=False,
        )
        return True
    if system == "Windows":
        windows_startup_script_path().unlink(missing_ok=True)
        return True
    if system == "Darwin":
        plist_path = macos_launch_agent_path(context.macos_launch_agent_name)
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
        plist_path.unlink(missing_ok=True)
        return True
    return False


def toggle_run_on_startup(
    context: StartupContext,
    *,
    refresh_tray_menu: Callable[[], None],
    log: Callable[..., None],
) -> None:
    enabled = is_run_on_startup_enabled(context)
    changed = disable_run_on_startup(context) if enabled else enable_run_on_startup(context)
    if not changed:
        log("[Startup] Run on startup is not supported on this platform.")
        return
    log(
        "[Startup]",
        "Run on startup enabled." if not enabled else "Run on startup disabled.",
    )
    refresh_tray_menu()
