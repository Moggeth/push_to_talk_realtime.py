from __future__ import annotations

from pathlib import Path

import desktop_bootstrap as bootstrap


def test_candidate_linux_site_packages_uses_expected_paths():
    paths = bootstrap.candidate_linux_site_packages((3, 12))

    assert paths == [
        Path("/usr/lib/python3/dist-packages"),
        Path("/usr/local/lib/python3.12/dist-packages"),
    ]


def test_enable_linux_gtk_backends_sets_appindicator_when_gi_available(monkeypatch):
    fake_path = Path("/fake/dist-packages")
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        bootstrap,
        "candidate_linux_site_packages",
        lambda version_info=None: [fake_path],
    )
    monkeypatch.setattr(bootstrap.Path, "exists", lambda self: self == fake_path)
    monkeypatch.setattr(bootstrap.importlib.util, "find_spec", lambda name: object())
    monkeypatch.delenv("PYSTRAY_BACKEND", raising=False)
    monkeypatch.setattr(bootstrap.sys, "path", [])

    bootstrap.enable_linux_gtk_backends()

    assert bootstrap.sys.path == [str(fake_path)]
    assert bootstrap.os.environ["PYSTRAY_BACKEND"] == "appindicator"


def test_enable_linux_gtk_backends_respects_existing_backend(monkeypatch):
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        bootstrap.importlib.util,
        "find_spec",
        lambda name: (_ for _ in ()).throw(AssertionError("should not probe gi")),
    )
    monkeypatch.setenv("PYSTRAY_BACKEND", "xorg")

    bootstrap.enable_linux_gtk_backends()

    assert bootstrap.os.environ["PYSTRAY_BACKEND"] == "xorg"


def test_enable_linux_gtk_backends_keeps_existing_paths_first(monkeypatch):
    fake_path = Path("/fake/dist-packages")
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        bootstrap,
        "candidate_linux_site_packages",
        lambda version_info=None: [fake_path],
    )
    monkeypatch.setattr(bootstrap.Path, "exists", lambda self: self == fake_path)
    monkeypatch.setattr(bootstrap.importlib.util, "find_spec", lambda name: object())
    monkeypatch.delenv("PYSTRAY_BACKEND", raising=False)
    monkeypatch.setattr(bootstrap.sys, "path", ["/venv/site-packages"])

    bootstrap.enable_linux_gtk_backends()

    assert bootstrap.sys.path == ["/venv/site-packages", str(fake_path)]
