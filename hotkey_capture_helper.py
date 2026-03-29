#!/usr/bin/env python3

import json
import sys

MODIFIER_ORDER = ("CTRL", "ALT", "SHIFT", "SUPER", "ALT_GR")
DISPLAY_NAMES = {
    "ALT": "Alt",
    "ALT_GR": "AltGr",
    "CAPS_LOCK": "Caps Lock",
    "CTRL": "Ctrl",
    "ENTER": "Enter",
    "ESC": "Esc",
    "PAGE_DOWN": "Page Down",
    "PAGE_UP": "Page Up",
    "SHIFT": "Shift",
    "SPACE": "Space",
    "SUPER": "Super",
    "TAB": "Tab",
}
KEY_ALIASES = {
    "alt_l": "ALT",
    "alt_r": "ALT",
    "altgr": "ALT_GR",
    "backspace": "BACKSPACE",
    "caps_lock": "CAPS_LOCK",
    "capslock": "CAPS_LOCK",
    "control_l": "CTRL",
    "control_r": "CTRL",
    "delete": "DELETE",
    "down": "DOWN",
    "end": "END",
    "escape": "ESC",
    "iso_level3_shift": "ALT_GR",
    "left": "LEFT",
    "meta_l": "SUPER",
    "meta_r": "SUPER",
    "next": "PAGE_DOWN",
    "page_down": "PAGE_DOWN",
    "page_up": "PAGE_UP",
    "prior": "PAGE_UP",
    "return": "ENTER",
    "right": "RIGHT",
    "shift_l": "SHIFT",
    "shift_r": "SHIFT",
    "space": "SPACE",
    "super_l": "SUPER",
    "super_r": "SUPER",
    "tab": "TAB",
    "up": "UP",
}

try:
    import tkinter as tk
except Exception:  # pylint: disable=broad-except
    tk = None

if tk is None:
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gtk
    except Exception:  # pylint: disable=broad-except
        Gdk = None
        Gtk = None
else:
    Gdk = None
    Gtk = None


def canonicalize_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return ""
    return KEY_ALIASES.get(token.lower(), token.upper())


def canonicalize_tokens(tokens: set[str]) -> tuple[str, ...]:
    unique = {canonicalize_token(token) for token in tokens}
    unique.discard("")
    return tuple(
        sorted(
            unique,
            key=lambda token: (
                token not in MODIFIER_ORDER,
                MODIFIER_ORDER.index(token) if token in MODIFIER_ORDER else token,
            ),
        )
    )


def format_tokens(tokens: tuple[str, ...]) -> str:
    if not tokens:
        return "Nothing drafted yet"
    return "+".join(DISPLAY_NAMES.get(token, token.title()) for token in tokens)


def emit_result(payload: dict[str, object]) -> None:
    print(json.dumps(payload), flush=True)


class TkHotkeyDialog:
    def __init__(self) -> None:
        assert tk is not None
        self.root = tk.Tk()
        self.root.title("Set Hotkey")
        self.root.geometry("470x230")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.root.bind("<KeyPress>", self.on_key_press)
        self.root.bind("<KeyRelease>", self.on_key_release)

        self.current_keys: set[str] = set()
        self.draft_tokens: tuple[str, ...] = ()

        container = tk.Frame(self.root, padx=16, pady=16)
        container.pack(fill="both", expand=True)

        title = tk.Label(
            container,
            text="Press the key or key combo you want to use",
            font=("TkDefaultFont", 11, "bold"),
            anchor="w",
            justify="left",
        )
        title.pack(fill="x")

        instructions = tk.Label(
            container,
            text=(
                "Keep this window focused, press your desired hotkey, then review the drafted "
                "selection below and click Accept.\n"
                "Note: bare Fn usually cannot be captured because firmware often handles it "
                "before the OS sees a key event."
            ),
            anchor="w",
            justify="left",
            wraplength=430,
        )
        instructions.pack(fill="x", pady=(12, 0))

        self.draft_var = tk.StringVar(value="Drafted hotkey: Nothing drafted yet")
        draft_label = tk.Label(
            container,
            textvariable=self.draft_var,
            anchor="w",
            justify="left",
        )
        draft_label.pack(fill="x", pady=(16, 0))

        button_row = tk.Frame(container)
        button_row.pack(side="bottom", anchor="e", pady=(16, 0))

        tk.Button(button_row, text="Clear", command=self.on_clear, width=10).pack(side="right")
        self.accept_button = tk.Button(
            button_row,
            text="Accept",
            command=self.on_accept,
            width=10,
            state="disabled",
        )
        self.accept_button.pack(side="right", padx=(0, 8))
        tk.Button(button_row, text="Cancel", command=self.on_cancel, width=10).pack(
            side="right",
            padx=(0, 8),
        )

    def update_draft_label(self) -> None:
        self.draft_var.set(f"Drafted hotkey: {format_tokens(self.draft_tokens)}")
        self.accept_button.configure(state="normal" if self.draft_tokens else "disabled")

    def on_key_press(self, event) -> str:
        token = canonicalize_token(event.keysym)
        if token:
            self.current_keys.add(token)
            self.draft_tokens = canonicalize_tokens(self.current_keys)
            self.update_draft_label()
        return "break"

    def on_key_release(self, event) -> str:
        token = canonicalize_token(event.keysym)
        if token:
            self.current_keys.discard(token)
        return "break"

    def on_clear(self) -> None:
        self.current_keys.clear()
        self.draft_tokens = ()
        self.update_draft_label()

    def on_accept(self) -> None:
        emit_result({"accepted": True, "tokens": list(self.draft_tokens)})
        self.root.destroy()

    def on_cancel(self) -> None:
        emit_result({"accepted": False})
        self.root.destroy()

    def run(self) -> int:
        self.root.after(0, self.root.focus_force)
        self.root.mainloop()
        return 0


def gtk_key_event_to_token(event) -> str:
    assert Gdk is not None
    return canonicalize_token(Gdk.keyval_name(event.keyval) or "")


class GtkHotkeyDialog:
    def __init__(self) -> None:
        assert Gtk is not None
        self.window = Gtk.Window(title="Set Hotkey")
        self.window.set_default_size(420, 220)
        self.window.set_border_width(16)
        self.window.set_keep_above(True)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.connect("destroy", self.on_cancel)
        self.window.connect("key-press-event", self.on_key_press)
        self.window.connect("key-release-event", self.on_key_release)

        self.current_keys: set[str] = set()
        self.draft_tokens: tuple[str, ...] = ()
        self.finished = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.window.add(box)

        title = Gtk.Label()
        title.set_markup("<b>Press the key or key combo you want to use</b>")
        title.set_xalign(0)
        box.pack_start(title, False, False, 0)

        instructions = Gtk.Label(
            label=(
                "Keep this window focused, press your desired hotkey, then review the drafted "
                "selection below and click Accept.\n"
                "Note: bare Fn usually cannot be captured because firmware often handles it "
                "before the OS sees a key event."
            )
        )
        instructions.set_xalign(0)
        instructions.set_line_wrap(True)
        box.pack_start(instructions, False, False, 0)

        self.draft_label = Gtk.Label(label="Drafted hotkey: Nothing drafted yet")
        self.draft_label.set_xalign(0)
        self.draft_label.set_selectable(True)
        box.pack_start(self.draft_label, False, False, 0)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.pack_end(button_box, False, False, 0)

        clear_button = Gtk.Button(label="Clear")
        clear_button.connect("clicked", self.on_clear)
        button_box.pack_end(clear_button, False, False, 0)

        self.accept_button = Gtk.Button(label="Accept")
        self.accept_button.set_sensitive(False)
        self.accept_button.connect("clicked", self.on_accept)
        button_box.pack_end(self.accept_button, False, False, 0)

        cancel_button = Gtk.Button(label="Cancel")
        cancel_button.connect("clicked", self.on_cancel)
        button_box.pack_end(cancel_button, False, False, 0)

    def update_draft_label(self) -> None:
        self.draft_label.set_text(f"Drafted hotkey: {format_tokens(self.draft_tokens)}")
        self.accept_button.set_sensitive(bool(self.draft_tokens))

    def on_key_press(self, _widget, event) -> bool:
        token = gtk_key_event_to_token(event)
        if token:
            self.current_keys.add(token)
            self.draft_tokens = canonicalize_tokens(self.current_keys)
            self.update_draft_label()
        return True

    def on_key_release(self, _widget, event) -> bool:
        token = gtk_key_event_to_token(event)
        if token:
            self.current_keys.discard(token)
        return True

    def on_clear(self, _button) -> None:
        self.current_keys.clear()
        self.draft_tokens = ()
        self.update_draft_label()

    def on_accept(self, _button) -> None:
        if self.finished:
            return
        self.finished = True
        emit_result({"accepted": True, "tokens": list(self.draft_tokens)})
        Gtk.main_quit()

    def on_cancel(self, *_args) -> None:
        if self.finished:
            return
        self.finished = True
        emit_result({"accepted": False})
        Gtk.main_quit()

    def run(self) -> int:
        self.window.show_all()
        self.window.present()
        Gtk.main()
        return 0


def main() -> int:
    if tk is not None:
        return TkHotkeyDialog().run()
    if Gtk is not None:
        return GtkHotkeyDialog().run()
    emit_result({"accepted": False, "error": "No supported GUI toolkit available"})
    return 1


if __name__ == "__main__":
    sys.exit(main())
