from __future__ import annotations

import importlib.util
import os
import platform
import sys
from pathlib import Path


def candidate_linux_site_packages(version_info: tuple[int, int] | None = None) -> list[Path]:
    major, minor = version_info or sys.version_info[:2]
    return [
        Path("/usr/lib/python3/dist-packages"),
        Path(f"/usr/local/lib/python{major}.{minor}/dist-packages"),
    ]


def enable_linux_gtk_backends() -> None:
    if platform.system() != "Linux":
        return

    for site_path in candidate_linux_site_packages():
        if site_path.exists():
            site_path_str = str(site_path)
            if site_path_str not in sys.path:
                sys.path.append(site_path_str)

    if os.environ.get("PYSTRAY_BACKEND"):
        return

    if importlib.util.find_spec("gi") is not None:
        os.environ["PYSTRAY_BACKEND"] = "appindicator"


enable_linux_gtk_backends()
