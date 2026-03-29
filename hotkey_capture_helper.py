#!/usr/bin/env python3

import json
import sys
import tkinter as tk

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


class HotkeyDialog:
    def __init__(self) -> None:
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

        clear_button = tk.Button(button_row, text="Clear", command=self.on_clear, width=10)
        clear_button.pack(side="right")

        self.accept_button = tk.Button(
            button_row,
            text="Accept",
            command=self.on_accept,
            width=10,
            state="disabled",
        )
        self.accept_button.pack(side="right", padx=(0, 8))

        cancel_button = tk.Button(button_row, text="Cancel", command=self.on_cancel, width=10)
        cancel_button.pack(side="right", padx=(0, 8))

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
        print(json.dumps({"accepted": True, "tokens": list(self.draft_tokens)}), flush=True)
        self.root.destroy()

    def on_cancel(self) -> None:
        print(json.dumps({"accepted": False}), flush=True)
        self.root.destroy()

    def run(self) -> int:
        self.root.after(0, self.root.focus_force)
        self.root.mainloop()
        return 0


def main() -> int:
    dialog = HotkeyDialog()
    return dialog.run()


if __name__ == "__main__":
    sys.exit(main())
