import platform

from pynput import keyboard as pynput_keyboard


def supports_foreground_console_detection(system_name: str | None = None) -> bool:
    return (system_name or platform.system()) == "Windows"


def get_paste_modifier(system_name: str | None = None) -> pynput_keyboard.Key:
    if (system_name or platform.system()) == "Darwin":
        return pynput_keyboard.Key.cmd
    return pynput_keyboard.Key.ctrl


def send_paste_shortcut(controller: pynput_keyboard.Controller | None = None) -> None:
    keyboard_controller = controller or pynput_keyboard.Controller()
    modifier = get_paste_modifier()
    with keyboard_controller.pressed(modifier):
        keyboard_controller.press("v")
        keyboard_controller.release("v")
