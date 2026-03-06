from pynput import keyboard as pynput_keyboard

from platform_input import (
    get_paste_modifier,
    send_paste_shortcut,
    supports_foreground_console_detection,
)


def test_supports_foreground_console_detection_only_on_windows():
    assert supports_foreground_console_detection("Windows") is True
    assert supports_foreground_console_detection("Linux") is False
    assert supports_foreground_console_detection("Darwin") is False


def test_get_paste_modifier_uses_ctrl_on_linux():
    assert get_paste_modifier("Linux") == pynput_keyboard.Key.ctrl


def test_get_paste_modifier_uses_ctrl_on_windows():
    assert get_paste_modifier("Windows") == pynput_keyboard.Key.ctrl


def test_get_paste_modifier_uses_cmd_on_macos():
    assert get_paste_modifier("Darwin") == pynput_keyboard.Key.cmd


def test_send_paste_shortcut_presses_modifier_then_v(monkeypatch):
    events: list[tuple[str, object]] = []

    class FakePressed:
        def __init__(self, key):
            self.key = key

        def __enter__(self):
            events.append(("enter", self.key))

        def __exit__(self, exc_type, exc, tb):
            events.append(("exit", self.key))
            return False

    class FakeController:
        def pressed(self, key):
            return FakePressed(key)

        def press(self, key):
            events.append(("press", key))

        def release(self, key):
            events.append(("release", key))

    monkeypatch.setattr("platform_input.get_paste_modifier", lambda system_name=None: "CTRL")

    send_paste_shortcut(controller=FakeController())

    assert events == [
        ("enter", "CTRL"),
        ("press", "v"),
        ("release", "v"),
        ("exit", "CTRL"),
    ]
