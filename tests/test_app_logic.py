from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

import push_to_talk_realtime as app


class FakeTrayIcon:
    def __init__(self) -> None:
        self.title = ""
        self.icon = None
        self.menu = None
        self.visible = False
        self.updated = 0
        self.stopped = 0

    def update_menu(self) -> None:
        self.updated += 1

    def stop(self) -> None:
        self.stopped += 1


class FakeThread:
    created: ClassVar[list[FakeThread]] = []

    def __init__(self, target, args=(), daemon=False):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False
        FakeThread.created.append(self)

    def start(self) -> None:
        self.started = True


@pytest.fixture(autouse=True)
def reset_app_state(monkeypatch, tmp_path: Path):
    app.state = app.SessionState()
    app.tray_icon = None
    app.keyboard_listener = None
    app.DEVICE_LIST = []
    app.shutdown_event.clear()
    monkeypatch.setattr(app, "WORK_LOG_PATH", tmp_path / "work_log.txt")
    FakeThread.created.clear()


def make_key(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name.lower())


def test_initialize_device_state_uses_env_selected_devices(monkeypatch):
    monkeypatch.setattr(app, "DEVICE_INDEX", None)
    monkeypatch.setenv("DICTATION_DEVICE", "usb mic")
    monkeypatch.setenv("WORKLOG_DEVICE", "stereo mix")
    monkeypatch.setattr(
        app,
        "resolve_device_descriptor",
        lambda descriptor: {
            "usb mic": (2, "USB Mic", True),
            "stereo mix": (7, "Stereo Mix", True),
        }[descriptor],
    )
    monkeypatch.setattr(app, "log_device_selection", lambda *args: None)

    app.initialize_device_state()

    assert app.state.dictation_device_index == 2
    assert app.state.dictation_device_label == "USB Mic"
    assert app.state.worklog_device_index == 7
    assert app.state.worklog_device_label == "Stereo Mix"
    assert app.state.worklog_default_device_index == 2
    assert app.state.worklog_default_device_label == "USB Mic"


def test_apply_punctuation_options_uses_state_flags(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        app,
        "apply_punctuation_options_core",
        lambda text, **kwargs: captured.setdefault("call", (text, kwargs)) or "done",
    )
    with app.state.lock:
        app.state.punctuation_normalize_spaces = True
        app.state.punctuation_capitalize = True
        app.state.punctuation_terminal = True

    app.apply_punctuation_options("hello")

    assert captured["call"] == (
        "hello",
        {
            "normalize_spaces": True,
            "capitalize": True,
            "terminal_punct": True,
        },
    )


def test_prepare_clipboard_text_uses_state_flags(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        app,
        "prepare_clipboard_text_core",
        lambda text, **kwargs: captured.setdefault("call", (text, kwargs)) or "done",
    )
    with app.state.lock:
        app.state.paste_suffix_mode = app.SUFFIX_NEWLINE
        app.state.punctuation_normalize_spaces = True
        app.state.punctuation_capitalize = True
        app.state.punctuation_terminal = True

    app.prepare_clipboard_text("hello")

    assert captured["call"] == (
        "hello",
        {
            "suffix_mode": app.SUFFIX_NEWLINE,
            "normalize_spaces": True,
            "capitalize": True,
            "terminal_punct": True,
        },
    )


def test_initialize_device_state_falls_back_to_dictation_when_worklog_missing(monkeypatch):
    monkeypatch.setattr(app, "DEVICE_INDEX", None)
    monkeypatch.setenv("DICTATION_DEVICE", "usb mic")
    monkeypatch.setenv("WORKLOG_DEVICE", "missing")
    monkeypatch.setattr(
        app,
        "resolve_device_descriptor",
        lambda descriptor: {
            "usb mic": (4, "USB Mic", True),
            "missing": (None, app.DEFAULT_DEVICE_LABEL, False),
        }[descriptor],
    )
    monkeypatch.setattr(app, "log_device_selection", lambda *args: None)

    app.initialize_device_state()

    assert app.state.dictation_device_index == 4
    assert app.state.worklog_device_index == 4
    assert app.state.worklog_device_label == "USB Mic"
    assert app.state.worklog_uses_stereo_mix is False


def test_pick_fallback_input_device_prefers_system_default(monkeypatch):
    monkeypatch.setattr(
        app, "refresh_device_list", lambda: setattr(app, "DEVICE_LIST", [(1, "USB Mic")])
    )
    monkeypatch.setattr(app, "is_default_input_available", lambda: True)

    fallback_index, fallback_label = app.pick_fallback_input_device(5)

    assert fallback_index is None
    assert fallback_label == app.DEFAULT_DEVICE_LABEL


def test_pick_fallback_input_device_uses_first_other_device_when_default_unavailable(monkeypatch):
    monkeypatch.setattr(
        app,
        "refresh_device_list",
        lambda: setattr(app, "DEVICE_LIST", [(5, "Broken Mic"), (8, "USB Mic")]),
    )
    monkeypatch.setattr(app, "is_default_input_available", lambda: False)

    fallback_index, fallback_label = app.pick_fallback_input_device(5)

    assert fallback_index == 8
    assert fallback_label == "USB Mic"


def test_maybe_beep_plays_each_tone_when_enabled(monkeypatch):
    beeps = []
    monkeypatch.setattr(app, "IS_WINDOWS", True)
    monkeypatch.setattr(
        app, "winsound", SimpleNamespace(Beep=lambda hz, ms: beeps.append((hz, ms)))
    )
    with app.state.lock:
        app.state.beeps_enabled = True

    app.maybe_beep([(440, 50), (660, 70)])

    assert beeps == [(440, 50), (660, 70)]


def test_maybe_beep_skips_when_disabled(monkeypatch):
    beeps = []
    monkeypatch.setattr(app, "IS_WINDOWS", True)
    monkeypatch.setattr(
        app, "winsound", SimpleNamespace(Beep=lambda hz, ms: beeps.append((hz, ms)))
    )

    app.maybe_beep([(440, 50)])

    assert beeps == []


def test_start_recorder_with_fallback_retries_primary_then_switches(monkeypatch):
    attempts = []

    class FakeRecorder:
        def __init__(self):
            self.device_index = 5

        def start(self):
            attempts.append(self.device_index)
            if self.device_index == 5:
                raise RuntimeError("boom")

    recorder = FakeRecorder()
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(app, "refresh_device_list", lambda: None)
    monkeypatch.setattr(app, "pick_fallback_input_device", lambda _preferred: (8, "USB Mic"))

    started, active_index, active_label = app.start_recorder_with_fallback(
        recorder,
        role="Dictate",
        device_label="Broken Mic",
        retries=1,
    )

    assert started is True
    assert active_index == 8
    assert active_label == "USB Mic"
    assert attempts == [5, 5, 8]


def test_start_recorder_with_fallback_returns_false_when_no_other_device(monkeypatch):
    class FakeRecorder:
        def __init__(self):
            self.device_index = 5

        def start(self):
            raise RuntimeError("boom")

    recorder = FakeRecorder()
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(app, "refresh_device_list", lambda: None)
    monkeypatch.setattr(
        app,
        "pick_fallback_input_device",
        lambda preferred: (preferred, "Broken Mic"),
    )

    started, active_index, active_label = app.start_recorder_with_fallback(
        recorder,
        role="Dictate",
        device_label="Broken Mic",
        retries=0,
    )

    assert started is False
    assert active_index == 5
    assert active_label == "Broken Mic"


def test_paste_text_copies_prepared_text_and_sends_shortcut(monkeypatch):
    copied = []
    paste_calls = []
    monkeypatch.setattr(app, "prepare_clipboard_text", lambda text: f"{text} ")
    monkeypatch.setattr(app.pyperclip, "copy", copied.append)
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(app, "send_paste_shortcut", lambda: paste_calls.append("sent"))

    result = app.paste_text("Hello")

    assert result is True
    assert copied == ["Hello "]
    assert paste_calls == ["sent"]


def test_paste_text_returns_false_for_blank_prepared_text(monkeypatch):
    copied = []
    monkeypatch.setattr(app, "prepare_clipboard_text", lambda _text: "   ")
    monkeypatch.setattr(app.pyperclip, "copy", copied.append)

    result = app.paste_text("Hello")

    assert result is False
    assert copied == []


def test_paste_text_returns_false_when_shortcut_fails(monkeypatch):
    copied = []
    monkeypatch.setattr(app, "prepare_clipboard_text", lambda text: text)
    monkeypatch.setattr(app.pyperclip, "copy", copied.append)
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        app, "send_paste_shortcut", lambda: (_ for _ in ()).throw(RuntimeError("nope"))
    )

    result = app.paste_text("Hello")

    assert result is False
    assert copied == ["Hello"]


def test_append_work_log_entry_writes_timestamped_single_line(monkeypatch, tmp_path: Path):
    class FixedDateTime:
        @staticmethod
        def now() -> datetime:
            return datetime(2026, 1, 2, 3, 4, 5)

    work_log_path = tmp_path / "logs" / "work_log.txt"
    monkeypatch.setattr(app, "datetime", FixedDateTime)
    monkeypatch.setattr(app, "WORK_LOG_PATH", work_log_path)

    app.append_work_log_entry("First line\nSecond line")

    assert work_log_path.read_text(encoding="utf-8") == (
        "- 2026-01-02 03:04:05 First line Second line\n"
    )


def test_apply_persisted_settings_loads_hotkeys_and_engine(monkeypatch, tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "transcription_engine": app.TRANSCRIPTION_ENGINE_GPT4O_REALTIME,
                "dictation_hotkey": "f15",
                "worklog_hotkey": "f16",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(app, "HOTKEY_DICTATION", app.DEFAULT_HOTKEY_DICTATION)
    monkeypatch.setattr(app, "HOTKEY_WORKLOG", app.DEFAULT_HOTKEY_WORKLOG)

    app.apply_persisted_settings()

    assert app.state.transcription_engine == app.TRANSCRIPTION_ENGINE_GPT4O_REALTIME
    assert app.HOTKEY_DICTATION == "F15"
    assert app.HOTKEY_WORKLOG == "F16"


def test_save_settings_to_disk_includes_hotkeys(monkeypatch, tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(app, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(app, "HOTKEY_DICTATION", "F17")
    monkeypatch.setattr(app, "HOTKEY_WORKLOG", "F18")
    with app.state.lock:
        app.state.transcription_engine = app.TRANSCRIPTION_ENGINE_WHISPER

    app.save_settings_to_disk()

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert saved == {
        "transcription_engine": app.TRANSCRIPTION_ENGINE_WHISPER,
        "dictation_hotkey": "F17",
        "worklog_hotkey": "F18",
    }


def test_start_and_stop_keyboard_listener_manage_single_listener(monkeypatch):
    events = []

    class FakeListener:
        def __init__(self, on_press, on_release):
            self.on_press = on_press
            self.on_release = on_release
            self.started = 0
            self.stopped = 0

        def start(self):
            self.started += 1
            events.append("start")

        def stop(self):
            self.stopped += 1
            events.append("stop")

    monkeypatch.setattr(app.pynput_keyboard, "Listener", FakeListener)

    app.start_keyboard_listener()
    listener = app.keyboard_listener
    app.start_keyboard_listener()
    app.stop_keyboard_listener()

    assert isinstance(listener, FakeListener)
    assert listener.on_press is app.on_press
    assert listener.on_release is app.on_release
    assert events == ["start", "stop"]
    assert app.keyboard_listener is None


def test_describe_device_includes_hostapi_name(monkeypatch):
    monkeypatch.setattr(
        app.sd,
        "query_devices",
        lambda index: {"name": "USB Mic", "hostapi": 1},
    )
    monkeypatch.setattr(app.sd, "query_hostapis", lambda: [{}, {"name": "ALSA"}])

    label, ok = app.describe_device(4)

    assert ok is True
    assert label == "USB Mic (ALSA)"


def test_describe_device_handles_none_and_query_errors(monkeypatch):
    monkeypatch.setattr(
        app.sd,
        "query_devices",
        lambda _index: (_ for _ in ()).throw(RuntimeError("missing")),
    )

    assert app.describe_device(None) == (app.DEFAULT_DEVICE_LABEL, True)
    assert app.describe_device(9) == ("index 9", False)


def test_lookup_input_device_by_name_matches_hostapi_and_ignores_outputs(monkeypatch):
    monkeypatch.setattr(
        app.sd,
        "query_devices",
        lambda: [
            {"name": "Speakers", "hostapi": 0, "max_input_channels": 0},
            {"name": "USB Mic", "hostapi": 1, "max_input_channels": 1},
        ],
    )
    monkeypatch.setattr(app.sd, "query_hostapis", lambda: [{"name": "MME"}, {"name": "ALSA"}])

    idx, label = app.lookup_input_device_by_name("alsa")

    assert idx == 1
    assert label == "USB Mic (ALSA)"


def test_lookup_input_device_by_name_handles_blank_failure_and_miss(monkeypatch):
    assert app.lookup_input_device_by_name("") == (None, "")

    monkeypatch.setattr(
        app.sd,
        "query_devices",
        lambda: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    assert app.lookup_input_device_by_name("usb") == (None, "")

    monkeypatch.setattr(
        app.sd,
        "query_devices",
        lambda: [{"name": "USB Mic", "hostapi": 0, "max_input_channels": 1}],
    )
    monkeypatch.setattr(app.sd, "query_hostapis", lambda: [{"name": "ALSA"}])
    assert app.lookup_input_device_by_name("loopback") == (None, "")


def test_refresh_device_list_keeps_only_input_devices(monkeypatch):
    monkeypatch.setattr(
        app.sd,
        "query_devices",
        lambda: [
            {"name": "Speakers", "hostapi": 0, "max_input_channels": 0},
            {"name": "USB Mic", "hostapi": 1, "max_input_channels": 1},
        ],
    )
    monkeypatch.setattr(app.sd, "query_hostapis", lambda: [{"name": "MME"}, {"name": "ALSA"}])

    app.refresh_device_list()

    assert app.DEVICE_LIST == [(1, "USB Mic (ALSA)")]


def test_refresh_device_list_clears_list_when_query_fails(monkeypatch):
    app.DEVICE_LIST = [(1, "USB Mic")]
    monkeypatch.setattr(
        app.sd,
        "query_devices",
        lambda: (_ for _ in ()).throw(RuntimeError("missing")),
    )

    app.refresh_device_list()

    assert app.DEVICE_LIST == []


def test_is_default_input_available_returns_false_on_query_error(monkeypatch):
    monkeypatch.setattr(
        app.sd,
        "query_devices",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("missing")),
    )

    assert app.is_default_input_available() is False


def test_resolve_device_descriptor_supports_numeric_and_name(monkeypatch):
    monkeypatch.setattr(app, "describe_device", lambda index: (f"Device {index}", True))
    monkeypatch.setattr(app, "lookup_input_device_by_name", lambda name: (7, name.title()))

    assert app.resolve_device_descriptor("5") == (5, "Device 5", True)
    assert app.resolve_device_descriptor("usb mic") == (7, "Usb Mic", True)
    assert app.resolve_device_descriptor("") == (None, app.DEFAULT_DEVICE_LABEL, True)

    monkeypatch.setattr(app, "lookup_input_device_by_name", lambda _name: (None, ""))
    assert app.resolve_device_descriptor("missing") == (None, app.DEFAULT_DEVICE_LABEL, False)


def test_set_device_for_role_updates_state_and_refreshes_menu(monkeypatch):
    refresh_calls = []
    monkeypatch.setattr(app, "refresh_tray_menu", lambda: refresh_calls.append("refresh"))
    with app.state.lock:
        app.state.is_listening = True
        app.state.mode = app.MODE_WORKLOG

    app.set_device_for_role(app.MODE_WORKLOG, 9, "Loopback")

    assert app.state.worklog_default_device_index == 9
    assert app.state.worklog_device_index == 9
    assert app.state.worklog_device_label == "Loopback"
    assert app.state.active_device_label == "Loopback"
    assert refresh_calls == ["refresh"]


def test_toggle_worklog_stereo_mix_switches_between_default_and_stereo(monkeypatch):
    refresh_calls = []
    monkeypatch.setattr(app, "refresh_tray_menu", lambda: refresh_calls.append("refresh"))
    monkeypatch.setattr(app, "lookup_input_device_by_name", lambda _term: (3, "Stereo Mix"))
    app.state.worklog_default_device_index = None
    app.state.worklog_default_device_label = app.DEFAULT_DEVICE_LABEL

    app.toggle_worklog_stereo_mix()

    assert app.state.worklog_uses_stereo_mix is True
    assert app.state.worklog_device_index == 3
    assert app.state.worklog_device_label == "Stereo Mix"

    app.toggle_worklog_stereo_mix()

    assert app.state.worklog_uses_stereo_mix is False
    assert app.state.worklog_device_index is None
    assert app.state.worklog_device_label == app.DEFAULT_DEVICE_LABEL
    assert refresh_calls == ["refresh", "refresh"]


def test_toggle_worklog_stereo_mix_logs_when_stereo_device_missing(monkeypatch):
    logs = []
    monkeypatch.setattr(app, "lookup_input_device_by_name", lambda _term: (None, ""))
    monkeypatch.setattr(app, "log", lambda *args: logs.append(" ".join(map(str, args))))

    app.toggle_worklog_stereo_mix()

    assert logs == [
        f"[Worklog audio] Stereo mix device matching '{app.STEREO_MIX_SEARCH}' not found."
    ]


def test_update_tray_tooltip_includes_status_mode_device_and_muted_warning():
    app.tray_icon = FakeTrayIcon()
    with app.state.lock:
        app.state.tooltip_enabled = True
        app.state.is_listening = True
        app.state.mode = app.MODE_WORKLOG
        app.state.active_device_label = "USB Mic"
        app.state.muted_warning = True

    app.update_tray_tooltip()

    assert app.tray_icon.title == (
        f"{app.TRAY_TITLE} - Listening (Worklog, USB Mic, Whisper, Muted?)"
    )


def test_update_tray_tooltip_resets_title_when_disabled():
    app.tray_icon = FakeTrayIcon()

    app.update_tray_tooltip()

    assert app.tray_icon.title == app.TRAY_TITLE


def test_update_tray_icon_uses_listening_color(monkeypatch):
    app.tray_icon = FakeTrayIcon()
    colors = []
    monkeypatch.setattr(
        app,
        "create_tray_icon_image",
        lambda **kwargs: colors.append(kwargs["color"]) or "icon",
    )
    with app.state.lock:
        app.state.is_listening = True

    app.update_tray_icon()

    assert colors == [app.TRAY_COLOR_LISTENING]
    assert app.tray_icon.icon == "icon"


def test_update_tray_status_refreshes_tooltip_and_icon(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "update_tray_tooltip", lambda: calls.append("tooltip"))
    monkeypatch.setattr(app, "update_tray_icon", lambda: calls.append("icon"))

    app.update_tray_status()

    assert calls == ["tooltip", "icon"]


def test_refresh_tray_menu_updates_menu_when_icon_present(monkeypatch):
    app.tray_icon = FakeTrayIcon()
    calls = []
    monkeypatch.setattr(app, "update_tray_status", lambda: calls.append("status"))

    app.refresh_tray_menu()

    assert calls == ["status"]
    assert app.tray_icon.updated == 1


def test_refresh_and_rebuild_tray_menu_noop_without_icon(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "update_tray_status", lambda: calls.append("status"))
    monkeypatch.setattr(app, "build_menu", lambda: calls.append("menu"))

    app.refresh_tray_menu()
    app.rebuild_tray_menu()

    assert calls == []


def test_rebuild_tray_menu_replaces_menu_and_refreshes(monkeypatch):
    app.tray_icon = FakeTrayIcon()
    refresh_calls = []
    monkeypatch.setattr(app, "build_menu", lambda: "menu")
    monkeypatch.setattr(app, "refresh_tray_menu", lambda: refresh_calls.append("refresh"))

    app.rebuild_tray_menu()

    assert app.tray_icon.menu == "menu"
    assert refresh_calls == ["refresh"]


def test_toggle_attr_and_suffix_helpers_refresh_menu(monkeypatch):
    refresh_calls = []
    monkeypatch.setattr(app, "refresh_tray_menu", lambda: refresh_calls.append("refresh"))

    app.toggle_attr("tooltip_enabled")
    app.set_paste_suffix_mode(app.SUFFIX_NEWLINE)

    assert app.state.tooltip_enabled is True
    assert app.state.paste_suffix_mode == app.SUFFIX_NEWLINE
    assert refresh_calls == ["refresh", "refresh"]


def test_toggle_wrappers_flip_expected_flags(monkeypatch):
    toggled = []
    monkeypatch.setattr(app, "toggle_attr", lambda name: toggled.append(name))

    app.toggle_punctuation_terminal()
    app.toggle_punctuation_capitalize()
    app.toggle_punctuation_normalize()
    app.toggle_tooltip()
    app.toggle_toggle_mode()

    assert toggled == [
        "punctuation_terminal",
        "punctuation_capitalize",
        "punctuation_normalize_spaces",
        "tooltip_enabled",
        "toggle_mode_enabled",
    ]


def test_toggle_beeps_logs_when_platform_beeps_are_unavailable(monkeypatch):
    logs = []
    monkeypatch.setattr(app, "IS_WINDOWS", False)
    monkeypatch.setattr(app, "winsound", None)
    monkeypatch.setattr(app, "log", lambda *args: logs.append(" ".join(map(str, args))))
    monkeypatch.setattr(app, "refresh_tray_menu", lambda: None)

    app.toggle_beeps()

    assert app.state.beeps_enabled is True
    assert logs == ["[Beep] System beeps are unavailable on this platform."]


def test_toggle_monitor_updates_timer_and_clears_warning(monkeypatch):
    refresh_calls = []
    monkeypatch.setattr(app, "refresh_tray_menu", lambda: refresh_calls.append("refresh"))
    monkeypatch.setattr(app.time, "monotonic", lambda: 42.0)
    with app.state.lock:
        app.state.muted_warning = True

    app.toggle_monitor()
    app.toggle_monitor()

    assert app.state.monitor_enabled is False
    assert app.state.last_audio_time == 42.0
    assert app.state.muted_warning is False
    assert refresh_calls == ["refresh", "refresh"]


def test_handle_spacebar_press_toggles_stereo_mix_only_when_idle_and_foreground(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "console_is_foreground", lambda: True)
    monkeypatch.setattr(app, "toggle_worklog_stereo_mix", lambda: calls.append("toggle"))

    app.handle_spacebar_press()

    assert calls == ["toggle"]

    with app.state.lock:
        app.state.is_listening = True

    app.handle_spacebar_press()

    assert calls == ["toggle"]


def test_console_is_foreground_returns_false_when_unsupported_or_windows_api_fails(monkeypatch):
    monkeypatch.setattr(app, "supports_foreground_console_detection", lambda: False)
    assert app.console_is_foreground() is False

    monkeypatch.setattr(app, "supports_foreground_console_detection", lambda: True)
    monkeypatch.setattr(
        app,
        "USER32",
        SimpleNamespace(GetForegroundWindow=lambda: (_ for _ in ()).throw(RuntimeError("boom"))),
    )
    monkeypatch.setattr(app, "KERNEL32", SimpleNamespace(GetConsoleWindow=lambda: 10))
    assert app.console_is_foreground() is False


def test_on_press_dictation_starts_worker_thread(monkeypatch):
    monkeypatch.setattr(app.threading, "Thread", FakeThread)
    app.state.dictation_device_index = 4
    app.state.dictation_device_label = "USB Mic"

    app.on_press(make_key(app.HOTKEY_DICTATION))

    assert len(FakeThread.created) == 1
    created = FakeThread.created[0]
    assert created.target is app.start_listening
    assert created.args == (app.MODE_DICTATION, app.HOTKEY_DICTATION, 4, "USB Mic")
    assert created.daemon is True
    assert created.started is True


def test_on_press_shift_dictation_uses_system_audio_device(monkeypatch):
    monkeypatch.setattr(app.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        app,
        "resolve_system_audio_input_device",
        lambda: (17, "Stereo Mix", True),
    )

    app.on_press(make_key("SHIFT"))
    app.on_press(make_key(app.HOTKEY_DICTATION))

    assert len(FakeThread.created) == 1
    created = FakeThread.created[0]
    assert created.target is app.start_listening
    assert created.args == (app.MODE_DICTATION, app.HOTKEY_DICTATION, 17, "Stereo Mix")


def test_on_press_shift_dictation_skips_when_system_audio_missing(monkeypatch):
    logs = []
    monkeypatch.setattr(app.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        app,
        "resolve_system_audio_input_device",
        lambda: (None, "", False),
    )
    monkeypatch.setattr(app, "log", lambda *args: logs.append(" ".join(map(str, args))))

    app.on_press(make_key("SHIFT"))
    app.on_press(make_key(app.HOTKEY_DICTATION))

    assert FakeThread.created == []
    assert logs == [
        (
            "[Audio] System audio capture unavailable."
            f" No input device matched '{app.system_audio_search_hint()}'."
        )
    ]


def test_on_press_worklog_starts_worker_thread(monkeypatch):
    monkeypatch.setattr(app.threading, "Thread", FakeThread)
    app.state.worklog_device_index = 6
    app.state.worklog_device_label = "Loopback"

    app.on_press(make_key(app.HOTKEY_WORKLOG))

    assert len(FakeThread.created) == 1
    assert FakeThread.created[0].target is app.start_worklog_after_hold
    assert FakeThread.created[0].args == (1,)


def test_on_press_worklog_double_tap_opens_log_without_starting_recording(monkeypatch):
    opened = []
    monkeypatch.setattr(app, "open_work_log", lambda *_args, **_kwargs: opened.append("opened"))
    monkeypatch.setattr(app.threading, "Thread", FakeThread)
    with app.state.lock:
        app.state.last_worklog_tap_time = 10.0
    monkeypatch.setattr(app.time, "monotonic", lambda: 10.2)

    app.on_press(make_key(app.HOTKEY_WORKLOG))

    assert opened == ["opened"]
    assert FakeThread.created == []
    assert app.state.worklog_double_tap_active is True


def test_on_press_spacebar_routes_to_space_handler(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "handle_spacebar_press", lambda: calls.append("space"))

    app.on_press(make_key("SPACE"))

    assert calls == ["space"]


def test_on_press_in_toggle_mode_stops_active_session(monkeypatch):
    with app.state.lock:
        app.state.is_listening = True
        app.state.toggle_mode_enabled = True
        app.state.active_hotkey = app.HOTKEY_DICTATION

    app.on_press(make_key(app.HOTKEY_DICTATION))

    assert app.state.should_stop is True


def test_on_release_records_short_worklog_tap_time(monkeypatch):
    with app.state.lock:
        app.state.worklog_press_time = 10.0
    monkeypatch.setattr(app.time, "monotonic", lambda: 10.2)

    app.on_release(make_key(app.HOTKEY_WORKLOG))

    assert app.state.worklog_press_time == 0.0
    assert app.state.last_worklog_tap_time == 10.2


def test_on_release_clears_last_tap_for_long_press_and_respects_toggle_mode(monkeypatch):
    with app.state.lock:
        app.state.worklog_press_time = 10.0
        app.state.toggle_mode_enabled = True
        app.state.is_listening = True
        app.state.active_hotkey = app.HOTKEY_DICTATION
    monkeypatch.setattr(app.time, "monotonic", lambda: 10.5)

    app.on_release(make_key(app.HOTKEY_WORKLOG))
    app.on_release(make_key(app.HOTKEY_DICTATION))

    assert app.state.last_worklog_tap_time == 0.0
    assert app.state.should_stop is False


def test_on_release_sets_should_stop_for_matching_hotkey():
    with app.state.lock:
        app.state.is_listening = True
        app.state.active_hotkey = app.HOTKEY_DICTATION

    app.on_release(make_key(app.HOTKEY_DICTATION))

    assert app.state.should_stop is True


def test_on_release_shift_clears_modifier_state():
    app.on_press(make_key("SHIFT"))

    app.on_release(make_key("SHIFT"))

    assert app.state.shift_keys_down == set()


def test_on_release_clears_double_tap_flag_without_recording_timestamp(monkeypatch):
    with app.state.lock:
        app.state.worklog_double_tap_active = True
        app.state.worklog_press_time = 10.0
    monkeypatch.setattr(app.time, "monotonic", lambda: 10.2)

    app.on_release(make_key(app.HOTKEY_WORKLOG))

    assert app.state.worklog_double_tap_active is False
    assert app.state.worklog_press_time == 0.0


def test_apply_default_preset_resets_runtime_toggles(monkeypatch):
    refresh_calls = []
    monkeypatch.setattr(app, "refresh_tray_menu", lambda: refresh_calls.append("refresh"))
    with app.state.lock:
        app.state.beeps_enabled = True
        app.state.tooltip_enabled = True
        app.state.toggle_mode_enabled = True
        app.state.monitor_enabled = True
        app.state.paste_suffix_mode = app.SUFFIX_NEWLINE
        app.state.punctuation_terminal = True
        app.state.punctuation_capitalize = True
        app.state.punctuation_normalize_spaces = True

    app.apply_default_preset()

    assert app.state.beeps_enabled is False
    assert app.state.tooltip_enabled is False
    assert app.state.toggle_mode_enabled is False
    assert app.state.monitor_enabled is False
    assert app.state.paste_suffix_mode == app.DEFAULT_SUFFIX_MODE
    assert app.state.punctuation_terminal is True
    assert app.state.punctuation_capitalize is False
    assert app.state.punctuation_normalize_spaces is False
    assert refresh_calls == ["refresh"]


def test_apply_bells_preset_enables_visual_and_audio_toggles(monkeypatch):
    refresh_calls = []
    monkeypatch.setattr(app, "refresh_tray_menu", lambda: refresh_calls.append("refresh"))

    app.apply_bells_preset()

    assert app.state.beeps_enabled is True
    assert app.state.tooltip_enabled is True
    assert app.state.monitor_enabled is True
    assert refresh_calls == ["refresh"]


def test_refresh_audio_devices_refreshes_list_and_menu(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "refresh_device_list", lambda: calls.append("devices"))
    monkeypatch.setattr(app, "rebuild_tray_menu", lambda: calls.append("menu"))

    app.refresh_audio_devices()

    assert calls == ["devices", "menu"]


def test_make_device_action_selects_device(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app, "set_device_for_role", lambda role, idx, label: calls.append((role, idx, label))
    )

    action = app.make_device_action(app.MODE_DICTATION, 2, "USB Mic")
    action(None, None)

    assert calls == [(app.MODE_DICTATION, 2, "USB Mic")]


def test_menu_builders_include_expected_top_level_items(monkeypatch):
    app.DEVICE_LIST = [(2, "USB Mic")]

    punctuation_menu = app.build_punctuation_menu()
    options_menu = app.build_options_menu()
    monkeypatch.setattr(app, "build_device_menu", lambda role: f"device:{role}")
    monkeypatch.setattr(app, "build_options_menu", lambda: "options")
    monkeypatch.setattr(app, "build_punctuation_menu", lambda: "punctuation")
    menu = app.build_menu()

    assert [item.text for item in punctuation_menu] == [
        "Suffix: None",
        "Suffix: Space",
        "Suffix: Newline",
        "Ensure terminal punctuation",
        "Capitalize first letter",
        "Normalize whitespace",
    ]
    assert [item.text for item in options_menu] == [
        "Toggle mode (tap to start/stop)",
        "Beeps",
        "Status tooltip",
        "Mute monitor",
    ]
    assert [item.text for item in menu] == [
        f"Dictation hotkey: {app.HOTKEY_DICTATION}",
        f"Work log hotkey: {app.HOTKEY_WORKLOG}",
        "Default (no frills)",
        "Bells and whistles",
        "Options",
        "Transcription engine",
        "Punctuation",
        "Dictation input device",
        "Work log input device",
        "Refresh audio devices",
        "Open work log",
        "Exit",
    ]


def test_build_device_menu_includes_default_devices_and_worklog_toggle():
    app.DEVICE_LIST = [(2, "USB Mic")]

    dictation_menu = app.build_device_menu(app.MODE_DICTATION)
    worklog_menu = app.build_device_menu(app.MODE_WORKLOG)

    assert [item.text for item in dictation_menu] == [app.DEFAULT_DEVICE_LABEL, "USB Mic"]
    assert [item.text for item in worklog_menu] == [
        "Toggle Stereo Mix (work log)",
        app.DEFAULT_DEVICE_LABEL,
        "USB Mic",
    ]


def test_console_is_foreground_checks_window_handles(monkeypatch):
    monkeypatch.setattr(app, "supports_foreground_console_detection", lambda: True)
    monkeypatch.setattr(app, "USER32", SimpleNamespace(GetForegroundWindow=lambda: 10))
    monkeypatch.setattr(app, "KERNEL32", SimpleNamespace(GetConsoleWindow=lambda: 10))

    assert app.console_is_foreground() is True


def test_get_key_name_supports_keycode_and_named_keys():
    assert app.get_key_name(app.pynput_keyboard.KeyCode.from_char("a")) == "A"
    assert app.get_key_name(make_key(app.HOTKEY_DICTATION)) == app.HOTKEY_DICTATION
    assert app.get_key_name(object()) == ""


def test_transcribe_with_whisper_returns_empty_string_for_empty_audio():
    assert app.transcribe_with_whisper([]) == ""


def test_ensure_and_open_work_log_create_file_and_log_path(monkeypatch, tmp_path: Path):
    logs = []
    work_log_path = tmp_path / "logs" / "work_log.txt"
    monkeypatch.setattr(app, "WORK_LOG_PATH", work_log_path)
    monkeypatch.setattr(app, "IS_WINDOWS", False)
    monkeypatch.setattr(app, "log", lambda *args: logs.append(" ".join(map(str, args))))

    app.open_work_log()

    assert work_log_path.exists() is True
    assert logs == [f"[Tray] Work log located at {work_log_path}"]


def test_initialize_device_state_reverts_invalid_default_device(monkeypatch):
    logs = []
    monkeypatch.setattr(app, "DEVICE_INDEX", 5)
    monkeypatch.delenv("DICTATION_DEVICE", raising=False)
    monkeypatch.delenv("WORKLOG_DEVICE", raising=False)
    monkeypatch.setattr(app, "describe_device", lambda _index: ("index 5", False))
    monkeypatch.setattr(app, "log_device_selection", lambda *args: None)
    monkeypatch.setattr(app, "log", lambda *args: logs.append(" ".join(map(str, args))))

    app.initialize_device_state()

    assert app.state.dictation_device_index is None
    assert app.state.dictation_device_label == app.DEFAULT_DEVICE_LABEL
    assert logs == ["[Audio] DEVICE_INDEX=5 unavailable; using system default."]


def test_create_tray_icon_image_returns_requested_size():
    image = app.create_tray_icon_image(size=32, color=(1, 2, 3, 255))

    assert image.size == (32, 32)


def test_tray_setup_marks_icon_visible_and_starts_listener(monkeypatch):
    logs = []
    refresh_calls = []
    start_calls = []
    icon = FakeTrayIcon()
    monkeypatch.setattr(app, "log", lambda *args: logs.append(" ".join(map(str, args))))
    monkeypatch.setattr(app, "refresh_tray_menu", lambda: refresh_calls.append("refresh"))
    monkeypatch.setattr(app, "start_keyboard_listener", lambda: start_calls.append("start"))

    app.tray_setup(icon)

    assert icon.visible is True
    assert refresh_calls == ["refresh"]
    assert start_calls == ["start"]
    assert logs == [
        (
            f"Push-to-talk ready. {app.HOTKEY_DICTATION} for dictation/paste, "
            f"{app.HOTKEY_WORKLOG} for work log. Engine: Whisper."
        )
    ]


def test_tray_exit_stops_listener_hides_icon_and_sets_shutdown(monkeypatch):
    stop_calls = []
    icon = FakeTrayIcon()
    icon.visible = True
    monkeypatch.setattr(app, "stop_keyboard_listener", lambda: stop_calls.append("stop"))

    app.tray_exit(icon)

    assert app.shutdown_event.is_set() is True
    assert stop_calls == ["stop"]
    assert icon.visible is False
    assert icon.stopped == 1


def test_main_builds_icon_and_runs_tray(monkeypatch):
    calls = []

    class FakeIcon:
        def __init__(self, name, icon, title, menu):
            calls.append(("init", name, icon, title, menu))
            self.visible = False

        def run(self, setup):
            calls.append(("run", setup))

    monkeypatch.setattr(app, "refresh_device_list", lambda: calls.append(("refresh",)))
    monkeypatch.setattr(app, "create_tray_icon_image", lambda: "icon")
    monkeypatch.setattr(app, "build_menu", lambda: "menu")
    monkeypatch.setattr(app.pystray, "Icon", FakeIcon)
    monkeypatch.setattr(app, "stop_keyboard_listener", lambda: calls.append(("stop",)))

    app.main()

    assert calls == [
        ("refresh",),
        ("init", "push_to_talk_realtime", "icon", app.TRAY_TITLE, "menu"),
        ("run", app.tray_setup),
        ("stop",),
    ]
    assert app.shutdown_event.is_set() is True


def test_main_handles_keyboard_interrupt_by_exiting_tray(monkeypatch):
    calls = []

    class FakeIcon:
        def __init__(self, *_args):
            self.visible = True

        def run(self, setup):
            raise KeyboardInterrupt

        def stop(self):
            calls.append("stop")

    monkeypatch.setattr(app, "refresh_device_list", lambda: None)
    monkeypatch.setattr(app, "create_tray_icon_image", lambda: "icon")
    monkeypatch.setattr(app, "build_menu", lambda: "menu")
    monkeypatch.setattr(app.pystray, "Icon", FakeIcon)
    monkeypatch.setattr(app, "stop_keyboard_listener", lambda: calls.append("listener-stop"))
    monkeypatch.setattr(app, "log", lambda *args: calls.append(" ".join(map(str, args))))

    app.main()

    assert calls == ["\nExiting...", "listener-stop", "stop", "listener-stop"]
