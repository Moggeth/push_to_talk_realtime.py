#!/usr/bin/env python3
"""
Push-to-talk transcription:
- Hold the configured dictation trigger to dictate and paste upon release.
- Hold the configured work-log hotkey to capture audio and append a timestamped work entry.

Notes
-----
- Captures 16 kHz mono PCM from the default input device (set DEVICE_INDEX if needed).
- Uses Ctrl+V on Windows/Linux and Cmd+V on macOS to paste the final text.
- The dictation trigger is configurable and can be set to the Linux touchpad middle click.
- Requires OPENAI_API_KEY with speech-to-text access in the environment or a .env file.
"""

import base64
import ctypes
import json
import os
import platform
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pyperclip
import pystray
import sounddevice as sd
from dotenv import load_dotenv
from PIL import Image, ImageDraw
from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse

from platform_input import send_paste_shortcut, supports_foreground_console_detection
from text_processing import (
    SUFFIX_NEWLINE,
    SUFFIX_NONE,
    SUFFIX_SPACE,
)
from text_processing import (
    apply_punctuation_options as apply_punctuation_options_core,
)
from text_processing import (
    prepare_clipboard_text as prepare_clipboard_text_core,
)

try:
    import winsound
except Exception:  # pylint: disable=broad-except
    winsound = None

# -------------------- Configuration --------------------

load_dotenv()  # loads OPENAI_API_KEY from .env if present

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
WHISPER_MODEL = os.getenv("OPENAI_WHISPER_MODEL", "whisper-1")
DEFAULT_TRANSCRIPTION_PROMPT = (
    "Transcribe exactly what is spoken. Use full sentence punctuation, including periods."
)
WHISPER_PROMPT = os.getenv("OPENAI_WHISPER_PROMPT", DEFAULT_TRANSCRIPTION_PROMPT).strip()
REALTIME_TRANSCRIBE_MODEL = os.getenv(
    "OPENAI_REALTIME_TRANSCRIBE_MODEL", "gpt-4o-transcribe"
).strip()
REALTIME_SESSION_MODEL = os.getenv("OPENAI_REALTIME_SESSION_MODEL", "").strip()
REALTIME_TRANSCRIBE_LANGUAGE = os.getenv("OPENAI_REALTIME_TRANSCRIBE_LANGUAGE", "").strip()
REALTIME_TRANSCRIBE_PROMPT = os.getenv(
    "OPENAI_REALTIME_TRANSCRIBE_PROMPT", DEFAULT_TRANSCRIPTION_PROMPT
).strip()
REALTIME_WS_URL = os.getenv(
    "OPENAI_REALTIME_WS_URL",
    "wss://api.openai.com/v1/realtime?intent=transcription",
).strip()
REALTIME_WS_USE_BETA_HEADER = os.getenv(
    "OPENAI_REALTIME_WS_USE_BETA_HEADER", "0"
).strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
TRANSCRIPTION_ENGINE_WHISPER = "whisper"
TRANSCRIPTION_ENGINE_GPT4O_REALTIME = "gpt4o_realtime"
DEFAULT_TRANSCRIPTION_ENGINE = (
    os.getenv("TRANSCRIPTION_ENGINE", TRANSCRIPTION_ENGINE_WHISPER).strip().lower()
)
REALTIME_INPUT_SAMPLE_RATE = 24000
REALTIME_LIVE_TYPING_ENABLED = os.getenv("REALTIME_LIVE_TYPING", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
LIVE_CORRECTION_MAX_BACKSPACES = 24
REALTIME_FINAL_WAIT_S = 1.5
REALTIME_COMMIT_INTERVAL_S = float(os.getenv("REALTIME_COMMIT_INTERVAL_S", "0.8"))
REALTIME_LIVE_MIN_CHUNK_S = float(os.getenv("REALTIME_LIVE_MIN_CHUNK_S", "1.6"))
REALTIME_LIVE_MAX_CHUNK_S = float(os.getenv("REALTIME_LIVE_MAX_CHUNK_S", "4.0"))
REALTIME_LIVE_SILENCE_S = float(os.getenv("REALTIME_LIVE_SILENCE_S", "0.65"))
REALTIME_LIVE_SILENCE_RMS = float(os.getenv("REALTIME_LIVE_SILENCE_RMS", "0.007"))
REALTIME_SERVER_VAD_THRESHOLD = float(os.getenv("REALTIME_SERVER_VAD_THRESHOLD", "0.5"))
REALTIME_SERVER_VAD_PREFIX_MS = int(os.getenv("REALTIME_SERVER_VAD_PREFIX_MS", "300"))
REALTIME_SERVER_VAD_SILENCE_MS = int(os.getenv("REALTIME_SERVER_VAD_SILENCE_MS", "700"))
REALTIME_TRANSCRIBE_MODEL_FALLBACKS = (
    "gpt-4o-transcribe",
    "gpt-4o-transcribe-latest",
    "gpt-4o-mini-transcribe",
    "whisper-1",
)

# Audio capture
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_DUR_S = 0.04  # 40 ms per audio chunk
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_DUR_S)
DEVICE_INDEX = None  # set to an index from sd.query_devices() if needed

# Behavior
MODE_DICTATION = "dictation"
MODE_WORKLOG = "worklog"

HOTKEY_KIND_MOUSE = "mouse"
HOTKEY_KIND_KEYBOARD = "keyboard"
DEFAULT_HOTKEY_DICTATION = "F13"
DEFAULT_HOTKEY_WORKLOG = "F14"
DEFAULT_DICTATION_HOTKEY_LABEL = "Three-finger touchpad press"
DICTATION_MOUSE_BUTTON = pynput_mouse.Button.middle
PASTE_ON_RELEASE = True
DEFAULT_SUFFIX_MODE = SUFFIX_SPACE
WORKLOG_DOUBLE_TAP_WINDOW_S = 0.4
WORKLOG_TAP_MAX_S = 0.25
SCRIPT_DIR = Path(__file__).resolve().parent
WORK_LOG_PATH = Path(os.getenv("WORK_LOG_PATH") or (SCRIPT_DIR / "work_log.txt"))
SETTINGS_PATH = Path(os.getenv("PUSH_TO_TALK_SETTINGS_PATH") or (SCRIPT_DIR / "settings.json"))
HOTKEY_CAPTURE_HELPER_PATH = SCRIPT_DIR / "hotkey_capture_helper.py"
SYSTEMD_SERVICE_NAME = os.getenv("PUSH_TO_TALK_SERVICE_NAME", "push-to-talk-realtime.service")
SYSTEMD_MANAGED_ENV = "PUSH_TO_TALK_MANAGED_BY_SYSTEMD"
DEFAULT_DEVICE_LABEL = "System default input"
STEREO_MIX_SEARCH = os.getenv("STEREO_MIX_SEARCH", "Stereo Mix")
SYSTEM_AUDIO_DEVICE = os.getenv("SYSTEM_AUDIO_DEVICE", "").strip()
IS_WINDOWS = platform.system().lower().startswith("win")
try:
    USER32 = ctypes.windll.user32 if IS_WINDOWS else None
    KERNEL32 = ctypes.windll.kernel32 if IS_WINDOWS else None
except Exception:  # pylint: disable=broad-except
    USER32 = None
    KERNEL32 = None

MUTE_RMS_THRESHOLD = float(os.getenv("MUTE_RMS_THRESHOLD", "0.01"))
MUTE_WARNING_AFTER_S = float(os.getenv("MUTE_WARNING_AFTER_S", "1.5"))
BEEP_START_PATTERN = [(784, 60), (1175, 60)]
BEEP_STOP_PATTERN = [(659, 70), (494, 90)]


def normalize_transcription_engine(engine: str) -> str:
    normalized = (engine or "").strip().lower()
    if normalized == TRANSCRIPTION_ENGINE_GPT4O_REALTIME:
        return TRANSCRIPTION_ENGINE_GPT4O_REALTIME
    return TRANSCRIPTION_ENGINE_WHISPER


def realtime_transcribe_model_candidates() -> list[str]:
    candidates: list[str] = []
    override = REALTIME_SESSION_MODEL.strip()
    if override:
        candidates.append(override)
    primary = REALTIME_TRANSCRIBE_MODEL.strip()
    if primary:
        candidates.append(primary)
    for fallback in REALTIME_TRANSCRIBE_MODEL_FALLBACKS:
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def transcription_engine_label(engine: str) -> str:
    normalized = normalize_transcription_engine(engine)
    if normalized == TRANSCRIPTION_ENGINE_GPT4O_REALTIME:
        return "GPT-4o Realtime"
    return "Whisper"


def normalize_hotkey_name(value: Any, default: str) -> str:
    hotkey = str(value or "").strip().upper()
    return hotkey or default


HOTKEY_DICTATION = normalize_hotkey_name(
    os.getenv("DICTATION_HOTKEY"),
    DEFAULT_HOTKEY_DICTATION,
)
HOTKEY_WORKLOG = normalize_hotkey_name(
    os.getenv("WORKLOG_HOTKEY"),
    DEFAULT_HOTKEY_WORKLOG,
)


# -------------------- State --------------------

TRAY_ICON_SIZE = 64
TRAY_TITLE = "Push-to-talk Transcription"
TRAY_COLOR_READY = (46, 160, 67, 255)
TRAY_COLOR_LISTENING = (220, 53, 69, 255)
TRAY_COLOR_TRANSCRIBING = (255, 166, 0, 255)
TRAY_SPINNER_STEPS = 12
TRAY_SPINNER_SWEEP_DEG = 90
TRAY_SPINNER_INTERVAL_S = 0.1


class TrayIconLike(Protocol):
    title: str
    icon: Any
    menu: Any
    visible: bool

    def update_menu(self) -> None: ...
    def stop(self) -> None: ...


@dataclass
class SessionState:
    is_listening: bool = False
    is_transcribing: bool = False
    transcribing_session_count: int = 0
    session_start_pending: bool = False
    should_stop: bool = False
    transcript_final: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)
    output_condition: threading.Condition = field(default_factory=threading.Condition)
    mode: str = MODE_DICTATION
    active_hotkey: str = ""
    active_hotkey_kind: str = ""
    active_hotkey_tokens: tuple[str, ...] = ()
    active_device_label: str = ""
    dictation_hotkey_kind: str = HOTKEY_KIND_KEYBOARD
    dictation_hotkey_label: str = HOTKEY_DICTATION
    dictation_hotkey_tokens: tuple[str, ...] = (HOTKEY_DICTATION,)
    dictation_device_index: int | None = DEVICE_INDEX
    dictation_device_label: str = DEFAULT_DEVICE_LABEL
    worklog_device_index: int | None = DEVICE_INDEX
    worklog_device_label: str = DEFAULT_DEVICE_LABEL
    worklog_default_device_index: int | None = DEVICE_INDEX
    worklog_default_device_label: str = DEFAULT_DEVICE_LABEL
    worklog_uses_stereo_mix: bool = False
    stereo_mix_device_index: int | None = None
    stereo_mix_device_label: str = ""
    session_counter: int = 0
    active_session_id: int = 0
    beeps_enabled: bool = False
    tooltip_enabled: bool = False
    toggle_mode_enabled: bool = False
    monitor_enabled: bool = False
    muted_warning: bool = False
    last_audio_time: float = 0.0
    last_audio_rms: float = 0.0
    paste_suffix_mode: str = DEFAULT_SUFFIX_MODE
    punctuation_terminal: bool = True
    punctuation_capitalize: bool = False
    punctuation_normalize_spaces: bool = False
    transcription_engine: str = normalize_transcription_engine(DEFAULT_TRANSCRIPTION_ENGINE)
    worklog_press_time: float = 0.0
    last_worklog_tap_time: float = 0.0
    worklog_double_tap_active: bool = False
    worklog_is_pressed: bool = False
    worklog_press_token: int = 0
    pressed_keys: set[str] = field(default_factory=set)
    tray_spinner_step: int = 0
    next_output_session_id: int = 1
    shift_keys_down: set[str] = field(default_factory=set)


state = SessionState()
shutdown_event = threading.Event()
keyboard_listener: pynput_keyboard.Listener | None = None
mouse_listener: pynput_mouse.Listener | None = None
tray_icon: TrayIconLike | None = None
tray_animation_thread: threading.Thread | None = None
DEVICE_LIST: list[tuple[int, str]] = []
HOTKEY_MODIFIER_ORDER = ("CTRL", "ALT", "SHIFT", "SUPER", "ALT_GR")
HOTKEY_DISPLAY_NAMES = {
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
PYNPUT_KEY_ALIASES = {
    "alt": "ALT",
    "alt_gr": "ALT_GR",
    "alt_l": "ALT",
    "alt_r": "ALT",
    "backspace": "BACKSPACE",
    "caps_lock": "CAPS_LOCK",
    "cmd": "SUPER",
    "cmd_l": "SUPER",
    "cmd_r": "SUPER",
    "control_l": "CTRL",
    "control_r": "CTRL",
    "ctrl": "CTRL",
    "ctrl_l": "CTRL",
    "ctrl_r": "CTRL",
    "delete": "DELETE",
    "down": "DOWN",
    "end": "END",
    "enter": "ENTER",
    "esc": "ESC",
    "escape": "ESC",
    "home": "HOME",
    "insert": "INSERT",
    "left": "LEFT",
    "menu": "MENU",
    "meta_l": "SUPER",
    "meta_r": "SUPER",
    "page_down": "PAGE_DOWN",
    "page_up": "PAGE_UP",
    "return": "ENTER",
    "right": "RIGHT",
    "shift": "SHIFT",
    "shift_l": "SHIFT",
    "shift_r": "SHIFT",
    "space": "SPACE",
    "super": "SUPER",
    "super_l": "SUPER",
    "super_r": "SUPER",
    "tab": "TAB",
    "up": "UP",
}
openai_client_lock = threading.Lock()
openai_client: Any = None
transcription_warmup_started = threading.Event()
transcription_warmup_finished = threading.Event()
output_keyboard_lock = threading.Lock()

# -------------------- Utilities --------------------


def log(*a):
    message = " ".join(str(part) for part in a)
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_message = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_message, flush=True)


def canonicalize_hotkey_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return ""
    normalized = PYNPUT_KEY_ALIASES.get(token.lower(), token.upper())
    if normalized.startswith("<") and normalized.endswith(">"):
        return ""
    return normalized


def canonicalize_hotkey_tokens(tokens: list[str] | set[str] | tuple[str, ...]) -> tuple[str, ...]:
    unique = {canonicalize_hotkey_token(token) for token in tokens}
    unique.discard("")
    return tuple(
        sorted(
            unique,
            key=lambda token: (
                token not in HOTKEY_MODIFIER_ORDER,
                HOTKEY_MODIFIER_ORDER.index(token) if token in HOTKEY_MODIFIER_ORDER else token,
            ),
        )
    )


def format_hotkey_tokens(tokens: tuple[str, ...]) -> str:
    if not tokens:
        return DEFAULT_DICTATION_HOTKEY_LABEL
    return "+".join(HOTKEY_DISPLAY_NAMES.get(token, token.title()) for token in tokens)


def dictation_hotkey_summary() -> str:
    with state.lock:
        return state.dictation_hotkey_label


def load_settings_from_disk() -> dict[str, Any]:
    try:
        if not SETTINGS_PATH.exists():
            return {}
        raw = SETTINGS_PATH.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:  # pylint: disable=broad-except
        log("[Settings] Unable to load settings:", exc)
    return {}


def save_settings_to_disk() -> None:
    with state.lock:
        payload = {
            "transcription_engine": state.transcription_engine,
            "dictation_hotkey_kind": state.dictation_hotkey_kind,
            "dictation_hotkey_tokens": list(state.dictation_hotkey_tokens),
            "dictation_hotkey": HOTKEY_DICTATION,
            "worklog_hotkey": HOTKEY_WORKLOG,
        }
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = SETTINGS_PATH.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(SETTINGS_PATH)
    except Exception as exc:  # pylint: disable=broad-except
        log("[Settings] Unable to save settings:", exc)


def apply_persisted_settings() -> None:
    global HOTKEY_DICTATION, HOTKEY_WORKLOG

    settings = load_settings_from_disk()
    engine = normalize_transcription_engine(str(settings.get("transcription_engine", "")))
    dictation_kind = HOTKEY_KIND_KEYBOARD
    dictation_tokens = (HOTKEY_DICTATION,)
    worklog_hotkey = HOTKEY_WORKLOG

    if "DICTATION_HOTKEY" not in os.environ and settings:
        saved_kind = str(settings.get("dictation_hotkey_kind", "")).strip().lower()
        saved_tokens = canonicalize_hotkey_tokens(settings.get("dictation_hotkey_tokens", []))
        if saved_kind == HOTKEY_KIND_MOUSE:
            dictation_kind = HOTKEY_KIND_MOUSE
            dictation_tokens = ()
            HOTKEY_DICTATION = DEFAULT_DICTATION_HOTKEY_LABEL
        elif saved_tokens:
            dictation_tokens = saved_tokens
            HOTKEY_DICTATION = format_hotkey_tokens(dictation_tokens)
        else:
            dictation_hotkey = normalize_hotkey_name(
                settings.get("dictation_hotkey"),
                HOTKEY_DICTATION,
            )
            dictation_tokens = (dictation_hotkey,)
            HOTKEY_DICTATION = dictation_hotkey
    if "WORKLOG_HOTKEY" not in os.environ and settings:
        worklog_hotkey = normalize_hotkey_name(
            settings.get("worklog_hotkey"),
            HOTKEY_WORKLOG,
        )
    with state.lock:
        state.transcription_engine = engine
        state.dictation_hotkey_kind = dictation_kind
        state.dictation_hotkey_tokens = dictation_tokens
        state.dictation_hotkey_label = format_hotkey_tokens(dictation_tokens)
    HOTKEY_WORKLOG = worklog_hotkey
    log(f"[Settings] Loaded transcription engine: {transcription_engine_label(engine)}")
    log(
        "[Settings] Loaded hotkeys:"
        f" dictation={dictation_hotkey_summary()}, worklog={HOTKEY_WORKLOG}"
    )


apply_persisted_settings()


def set_dictation_hotkey(kind: str, tokens: tuple[str, ...] = ()) -> None:
    global HOTKEY_DICTATION
    normalized_tokens = canonicalize_hotkey_tokens(tokens)
    with state.lock:
        if kind == HOTKEY_KIND_KEYBOARD and normalized_tokens:
            state.dictation_hotkey_kind = HOTKEY_KIND_KEYBOARD
            state.dictation_hotkey_tokens = normalized_tokens
            state.dictation_hotkey_label = format_hotkey_tokens(normalized_tokens)
            HOTKEY_DICTATION = state.dictation_hotkey_label
        else:
            state.dictation_hotkey_kind = HOTKEY_KIND_MOUSE
            state.dictation_hotkey_tokens = ()
            state.dictation_hotkey_label = DEFAULT_DICTATION_HOTKEY_LABEL
            HOTKEY_DICTATION = DEFAULT_DICTATION_HOTKEY_LABEL
    save_settings_to_disk()
    refresh_tray_menu()


def is_dictation_keyboard_hotkey_pressed() -> bool:
    with state.lock:
        tokens = set(state.dictation_hotkey_tokens)
        if state.dictation_hotkey_kind != HOTKEY_KIND_KEYBOARD or not tokens:
            return False
        if state.pressed_keys == tokens:
            return True
        return "SHIFT" not in tokens and state.pressed_keys == (tokens | {"SHIFT"})


def apply_punctuation_options(text: str) -> str:
    with state.lock:
        normalize_spaces = state.punctuation_normalize_spaces
        capitalize = state.punctuation_capitalize
        terminal_punct = state.punctuation_terminal
    return apply_punctuation_options_core(
        text,
        normalize_spaces=normalize_spaces,
        capitalize=capitalize,
        terminal_punct=terminal_punct,
    )


def prepare_clipboard_text(text: str) -> str:
    with state.lock:
        suffix_mode = state.paste_suffix_mode
        normalize_spaces = state.punctuation_normalize_spaces
        capitalize = state.punctuation_capitalize
        terminal_punct = state.punctuation_terminal
    return prepare_clipboard_text_core(
        text,
        suffix_mode=suffix_mode,
        normalize_spaces=normalize_spaces,
        capitalize=capitalize,
        terminal_punct=terminal_punct,
    )


def get_openai_client() -> Any:
    global openai_client
    with openai_client_lock:
        if openai_client is None:
            from openai import OpenAI

            openai_client = OpenAI(api_key=OPENAI_API_KEY)
        return openai_client


def _prewarm_transcription_stack() -> None:
    try:
        if not OPENAI_API_KEY:
            return
        start = time.perf_counter()
        client = get_openai_client()
        try:
            warm_client = client.with_options(timeout=5.0)
        except Exception:
            warm_client = client
        try:
            warm_client.models.list()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            log(f"[Warmup] OpenAI transcription stack warmed in {elapsed_ms:.0f} ms.")
        except Exception as exc:  # pylint: disable=broad-except
            log("[Warmup] OpenAI API warmup skipped:", exc)

        with state.lock:
            selected_engine = state.transcription_engine
        if selected_engine == TRANSCRIPTION_ENGINE_GPT4O_REALTIME:
            try:
                from websockets.sync.client import connect as _connect  # noqa: F401

                log("[Warmup] Realtime websocket client imported.")
            except Exception as exc:  # pylint: disable=broad-except
                log("[Warmup] Realtime websocket import skipped:", exc)
    finally:
        transcription_warmup_finished.set()


def start_transcription_warmup() -> None:
    if transcription_warmup_started.is_set() or not OPENAI_API_KEY:
        return
    transcription_warmup_started.set()
    threading.Thread(target=_prewarm_transcription_stack, daemon=True).start()


def realtime_dependency_error() -> str | None:
    if not OPENAI_API_KEY:
        return "Realtime engine unavailable: OPENAI_API_KEY not set."
    try:
        from websockets.sync.client import connect  # noqa: F401
    except Exception as exc:  # pylint: disable=broad-except
        return f"Realtime engine unavailable: {exc}"
    return None


def can_use_realtime_engine() -> bool:
    return realtime_dependency_error() is None


def enforce_transcription_engine_dependencies() -> None:
    with state.lock:
        engine = state.transcription_engine
    if engine != TRANSCRIPTION_ENGINE_GPT4O_REALTIME:
        return
    dep_error = realtime_dependency_error()
    if dep_error:
        log("[Transcription engine]", dep_error)
        with state.lock:
            state.transcription_engine = TRANSCRIPTION_ENGINE_WHISPER


def paste_text(text: str):
    """Paste text into the active control using clipboard + platform paste chord."""
    prepared = prepare_clipboard_text(text)
    if not prepared or not prepared.strip():
        return False
    pyperclip.copy(prepared)
    time.sleep(0.02)
    try:
        send_paste_shortcut()
    except Exception as exc:  # pylint: disable=broad-except
        log("[Paste error]", exc)
        log("[Paste error] Clipboard still contains the transcript.")
        return False
    return True


def send_backspaces(count: int) -> None:
    if count <= 0:
        return
    controller = pynput_keyboard.Controller()
    with output_keyboard_lock:
        for _ in range(count):
            controller.press(pynput_keyboard.Key.backspace)
            controller.release(pynput_keyboard.Key.backspace)


def type_text_direct(text: str) -> None:
    if not text:
        return
    controller = pynput_keyboard.Controller()
    with output_keyboard_lock:
        controller.type(text)


def try_apply_live_dictation_correction(live_text: str, target_text: str) -> bool:
    """Adjust already-typed realtime text toward final post-processed output."""
    if not live_text:
        return False
    if live_text == target_text:
        return True

    prefix_len = 0
    for live_char, target_char in zip(live_text, target_text, strict=False):
        if live_char != target_char:
            break
        prefix_len += 1

    backspaces = len(live_text) - prefix_len
    if backspaces > LIVE_CORRECTION_MAX_BACKSPACES:
        return False

    to_insert = target_text[prefix_len:]
    try:
        send_backspaces(backspaces)
        if to_insert:
            type_text_direct(to_insert)
    except Exception as exc:  # pylint: disable=broad-except
        log("[Realtime] Live correction failed:", exc)
        return False
    return True


def maybe_beep(pattern: list[tuple[int, int]]) -> None:
    with state.lock:
        enabled = state.beeps_enabled
    if not enabled or not IS_WINDOWS or winsound is None:
        return
    try:
        for hz, duration_ms in pattern:
            winsound.Beep(hz, duration_ms)
    except Exception as exc:  # pylint: disable=broad-except
        log("[Beep error]", exc)


def mark_transcription_started() -> None:
    with state.lock:
        state.transcribing_session_count += 1
        state.is_transcribing = state.transcribing_session_count > 0
    update_tray_status()


def mark_transcription_finished(final_text: str = "") -> None:
    with state.lock:
        if final_text:
            state.transcript_final = final_text
        state.transcribing_session_count = max(0, state.transcribing_session_count - 1)
        state.is_transcribing = state.transcribing_session_count > 0
    update_tray_status()


def wait_for_output_turn(session_id: int) -> None:
    with state.output_condition:
        while session_id != state.next_output_session_id:
            state.output_condition.wait()


def advance_output_turn() -> None:
    with state.output_condition:
        state.next_output_session_id += 1
        state.output_condition.notify_all()


def append_work_log_entry(text: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sanitized = " ".join(text.strip().splitlines())
    line = f"- {timestamp} {sanitized}"
    try:
        WORK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with WORK_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as exc:
        log("[Work log error]", exc)
    else:
        log(f"[Logged] {line}")


# -------------------- Audio device helpers --------------------


def start_keyboard_listener() -> None:
    global keyboard_listener
    if keyboard_listener is None:
        keyboard_listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
        keyboard_listener.start()


def stop_keyboard_listener() -> None:
    global keyboard_listener
    if keyboard_listener is not None:
        keyboard_listener.stop()
        keyboard_listener = None


def start_mouse_listener() -> None:
    global mouse_listener
    if mouse_listener is None:
        mouse_listener = pynput_mouse.Listener(on_click=on_mouse_click)
        mouse_listener.start()


def stop_mouse_listener() -> None:
    global mouse_listener
    if mouse_listener is not None:
        mouse_listener.stop()
        mouse_listener = None


def start_input_listeners() -> None:
    start_keyboard_listener()
    start_mouse_listener()


def stop_input_listeners() -> None:
    stop_mouse_listener()
    stop_keyboard_listener()


def describe_device(index: int | None) -> tuple[str, bool]:
    if index is None:
        return DEFAULT_DEVICE_LABEL, True
    try:
        device = sd.query_devices(index)
    except Exception as exc:  # pylint: disable=broad-except
        log(f"[Audio] Unable to describe device index {index}: {exc}")
        return f"index {index}", False

    hostapi_name = ""
    hostapi_index = device.get("hostapi")
    if isinstance(hostapi_index, int):
        try:
            hostapis = sd.query_hostapis()
            hostapi_name = hostapis[hostapi_index].get("name", "")
        except Exception:  # pylint: disable=broad-except
            hostapi_name = ""

    label = device.get("name", f"index {index}")
    if hostapi_name:
        label = f"{label} ({hostapi_name})"
    return label, True


def lookup_input_device_by_name(search_term: str) -> tuple[int | None, str]:
    term = search_term.strip().lower()
    if not term:
        return None, ""

    try:
        devices = sd.query_devices()
    except Exception as exc:  # pylint: disable=broad-except
        log(f"[Audio] Unable to query devices: {exc}")
        return None, ""

    try:
        hostapis = sd.query_hostapis()
    except Exception:  # pylint: disable=broad-except
        hostapis = []

    hostapi_names = {idx: api.get("name", "") for idx, api in enumerate(hostapis)}

    for idx, device in enumerate(devices):
        if device.get("max_input_channels", 0) <= 0:
            continue
        hostapi_index = device.get("hostapi")
        hostapi_name = (
            hostapi_names.get(hostapi_index, "") if isinstance(hostapi_index, int) else ""
        )
        label = device.get("name", str(idx))
        if hostapi_name:
            label = f"{label} ({hostapi_name})"
        combined = " ".join(part for part in [device.get("name", ""), hostapi_name] if part)
        if term in combined.lower():
            return idx, label

    return None, ""


def refresh_device_list() -> None:
    global DEVICE_LIST
    try:
        devices = sd.query_devices()
    except Exception as exc:  # pylint: disable=broad-except
        log(f"[Audio] Unable to query devices: {exc}")
        DEVICE_LIST = []
        return

    try:
        hostapis = sd.query_hostapis()
    except Exception:  # pylint: disable=broad-except
        hostapis = []

    hostapi_names = {idx: api.get("name", "") for idx, api in enumerate(hostapis)}
    listed: list[tuple[int, str]] = []
    for idx, device in enumerate(devices):
        if device.get("max_input_channels", 0) <= 0:
            continue
        hostapi_index = device.get("hostapi")
        hostapi_name = (
            hostapi_names.get(hostapi_index, "") if isinstance(hostapi_index, int) else ""
        )
        label = device.get("name", str(idx))
        if hostapi_name:
            label = f"{label} ({hostapi_name})"
        listed.append((idx, label))
    DEVICE_LIST = listed


def is_default_input_available() -> bool:
    try:
        sd.query_devices(None, "input")
    except Exception:  # pylint: disable=broad-except
        return False
    return True


def pick_fallback_input_device(preferred_index: int | None) -> tuple[int | None, str]:
    refresh_device_list()
    if preferred_index is None:
        if DEVICE_LIST:
            return DEVICE_LIST[0]
        return None, DEFAULT_DEVICE_LABEL
    if is_default_input_available():
        return None, DEFAULT_DEVICE_LABEL
    for idx, label in DEVICE_LIST:
        if idx != preferred_index:
            return idx, label
    return None, DEFAULT_DEVICE_LABEL


def resolve_device_descriptor(descriptor: str) -> tuple[int | None, str, bool]:
    descriptor = (descriptor or "").strip()
    if not descriptor:
        return None, DEFAULT_DEVICE_LABEL, True
    if descriptor.isdigit():
        idx = int(descriptor)
        label, ok = describe_device(idx)
        return (idx if ok else None), label, ok
    idx, label = lookup_input_device_by_name(descriptor)
    if idx is None:
        return None, DEFAULT_DEVICE_LABEL, False
    return idx, label, True


def log_device_selection(role: str, index: int | None, label: str) -> None:
    index_text = index if index is not None else "default"
    log(f"[Audio] {role} device -> {label} (index={index_text})")


def system_audio_search_hint() -> str:
    return SYSTEM_AUDIO_DEVICE or STEREO_MIX_SEARCH


def resolve_system_audio_input_device() -> tuple[int | None, str, bool]:
    descriptor = SYSTEM_AUDIO_DEVICE
    if descriptor:
        idx, label, ok = resolve_device_descriptor(descriptor)
        if ok:
            return idx, label, True
        return None, "", False

    with state.lock:
        cached_index = state.stereo_mix_device_index
        cached_label = state.stereo_mix_device_label
    if cached_index is not None and cached_label:
        return cached_index, cached_label, True

    idx, label = lookup_input_device_by_name(STEREO_MIX_SEARCH)
    if idx is None:
        return None, "", False

    with state.lock:
        state.stereo_mix_device_index = idx
        state.stereo_mix_device_label = label
    return idx, label, True


def initialize_device_state() -> None:
    fallback_index = DEVICE_INDEX
    fallback_label = DEFAULT_DEVICE_LABEL
    if fallback_index is not None:
        label, ok = describe_device(fallback_index)
        if ok:
            fallback_label = label
        else:
            log(f"[Audio] DEVICE_INDEX={fallback_index} unavailable; using system default.")
            fallback_index = None
            fallback_label = DEFAULT_DEVICE_LABEL

    dictation_descriptor = os.getenv("DICTATION_DEVICE", "").strip()
    worklog_descriptor = os.getenv("WORKLOG_DEVICE", "").strip()

    dictation_index = fallback_index
    dictation_label = fallback_label
    if dictation_descriptor:
        idx, label, ok = resolve_device_descriptor(dictation_descriptor)
        if ok:
            dictation_index, dictation_label = idx, label
        else:
            log(
                f"[Audio] Dictation device '{dictation_descriptor}' not found; using {fallback_label}."
            )

    worklog_index = dictation_index
    worklog_label = dictation_label
    if worklog_descriptor:
        idx, label, ok = resolve_device_descriptor(worklog_descriptor)
        if ok:
            worklog_index, worklog_label = idx, label
        else:
            log(
                f"[Audio] Worklog device '{worklog_descriptor}' not found; using {dictation_label}."
            )

    state.dictation_device_index = dictation_index
    state.dictation_device_label = dictation_label
    state.worklog_default_device_index = dictation_index
    state.worklog_default_device_label = dictation_label
    state.worklog_device_index = worklog_index
    state.worklog_device_label = worklog_label
    state.worklog_uses_stereo_mix = False
    state.stereo_mix_device_index = None
    state.stereo_mix_device_label = ""

    log_device_selection("Dictation", dictation_index, dictation_label)
    log_device_selection("Worklog", worklog_index, worklog_label)


def is_device_selected(role: str, idx: int | None) -> bool:
    with state.lock:
        if role == MODE_DICTATION:
            return state.dictation_device_index == idx
        return state.worklog_device_index == idx


def set_device_for_role(role: str, idx: int | None, label: str) -> None:
    with state.lock:
        if role == MODE_DICTATION:
            state.dictation_device_index = idx
            state.dictation_device_label = label
            if state.is_listening and state.mode == MODE_DICTATION:
                state.active_device_label = label
        else:
            state.worklog_default_device_index = idx
            state.worklog_default_device_label = label
            state.worklog_device_index = idx
            state.worklog_device_label = label
            state.worklog_uses_stereo_mix = False
            if state.is_listening and state.mode == MODE_WORKLOG:
                state.active_device_label = label
    log_device_selection(role.capitalize(), idx, label)
    refresh_tray_menu()


# -------------------- Audio Capture --------------------

initialize_device_state()
refresh_device_list()


class AudioRecorder:
    def __init__(
        self,
        device_index: int | None,
        buffer: list,
        buffer_lock: threading.Lock,
        on_chunk: Callable[[np.ndarray], None] | None = None,
    ):
        self.stream = None
        self.device_index = device_index
        self.buffer = buffer
        self.buffer_lock = buffer_lock
        self.on_chunk = on_chunk

    def _callback(self, indata, frames, time_info, status):
        if status:
            return
        pcm = np.clip(indata[:, 0], -1.0, 1.0)
        rms = float(np.sqrt(np.mean(pcm * pcm))) if pcm.size else 0.0
        now = time.monotonic()
        with state.lock:
            state.last_audio_rms = rms
            if rms >= MUTE_RMS_THRESHOLD:
                state.last_audio_time = now
        pcm_i16 = (pcm * 32767.0).astype(np.int16)
        with self.buffer_lock:
            self.buffer.append(pcm_i16.copy())
        if self.on_chunk is not None:
            try:
                self.on_chunk(pcm_i16.copy())
            except Exception:  # pylint: disable=broad-except
                return

    def start(self):
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=BLOCK_SIZE,
            device=self.device_index,
            callback=self._callback,
        )
        self.stream.start()

    def stop(self):
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        finally:
            self.stream = None


def start_recorder_with_fallback(
    recorder: AudioRecorder,
    role: str,
    device_label: str,
    retries: int = 2,
) -> tuple[bool, int | None, str]:
    label_text = device_label or DEFAULT_DEVICE_LABEL
    for attempt in range(retries + 1):
        index_text = recorder.device_index if recorder.device_index is not None else "default"
        try:
            recorder.start()
            return True, recorder.device_index, label_text
        except Exception as exc:  # pylint: disable=broad-except
            log(
                "[Audio] Unable to start",
                f"{role} input ({label_text}, index={index_text}): {exc}",
            )
            if attempt < retries:
                time.sleep(0.25)
                refresh_device_list()

    fallback_index, fallback_label = pick_fallback_input_device(recorder.device_index)
    fallback_text = fallback_label or DEFAULT_DEVICE_LABEL
    if fallback_index == recorder.device_index and fallback_text == label_text:
        return False, recorder.device_index, label_text

    fallback_index_text = fallback_index if fallback_index is not None else "default"
    log(
        "[Audio] Retrying",
        f"{role} input with fallback {fallback_text} (index={fallback_index_text}).",
    )
    recorder.device_index = fallback_index
    try:
        recorder.start()
    except Exception as exc:  # pylint: disable=broad-except
        log(
            "[Audio] Unable to start fallback",
            f"{role} input ({fallback_text}, index={fallback_index_text}): {exc}",
        )
        return False, fallback_index, fallback_text
    return True, fallback_index, fallback_text


# -------------------- Whisper transcription --------------------


def transcribe_with_whisper(chunks: list) -> str:
    """Send recorded buffer to Whisper; return the transcript or ''."""
    if not chunks:
        return ""
    try:
        import io
        import wave

        client = get_openai_client()
        pcm = np.concatenate(chunks, axis=0)
        wav_bytes = io.BytesIO()
        with wave.open(wav_bytes, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm.tobytes())
        wav_bytes.seek(0)
        wav_bytes.name = "recording.wav"  # hint format to the API

        request_args: dict[str, Any] = {
            "model": WHISPER_MODEL,
            "file": wav_bytes,
            "response_format": "json",
        }
        if WHISPER_PROMPT:
            request_args["prompt"] = WHISPER_PROMPT

        response = client.audio.transcriptions.create(
            **request_args,
        )
        return getattr(response, "text", "") or ""
    except Exception as exc:  # pylint: disable=broad-except
        log("[Whisper error]", exc)
        return ""


# -------------------- Orchestration --------------------
def resample_pcm16_mono(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if pcm.size == 0:
        return np.array([], dtype=np.int16)
    if src_rate == dst_rate:
        return pcm.astype(np.int16, copy=False)
    src = pcm.astype(np.float32)
    src_index = np.arange(src.shape[0], dtype=np.float32)
    dst_len = max(1, round(src.shape[0] * dst_rate / src_rate))
    dst_index = np.linspace(0, max(0, src.shape[0] - 1), num=dst_len, dtype=np.float32)
    dst = np.interp(dst_index, src_index, src)
    return np.clip(np.round(dst), -32768, 32767).astype(np.int16)


def transcribe_pcm16_with_models(
    client: Any,
    pcm: np.ndarray,
    *,
    sample_rate: int,
    model_candidates: list[str],
    prompt: str = "",
    language: str = "",
) -> tuple[str, str]:
    if pcm.size == 0:
        return "", ""

    import io
    import wave

    wav_bytes = io.BytesIO()
    with wave.open(wav_bytes, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.astype(np.int16, copy=False).tobytes())
    wav_bytes.name = "recording.wav"

    request_args: dict[str, Any] = {"file": wav_bytes, "response_format": "json"}
    if language:
        request_args["language"] = language
    if prompt:
        request_args["prompt"] = prompt

    last_error: BaseException | None = None
    for model_name in model_candidates:
        try:
            wav_bytes.seek(0)
            response = client.audio.transcriptions.create(
                model=model_name,
                **request_args,
            )
            text = (getattr(response, "text", "") or "").strip()
            if text:
                return text, model_name
        except Exception as exc:  # pylint: disable=broad-except
            last_error = exc
            continue

    if last_error:
        log("[GPT-4o Realtime error]", last_error)
    return "", ""


def build_transcription_session_update_event(model_name: str) -> dict[str, Any]:
    session: dict[str, Any] = {
        "type": "transcription",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": REALTIME_INPUT_SAMPLE_RATE},
                "transcription": {
                    "model": model_name,
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": REALTIME_SERVER_VAD_THRESHOLD,
                    "prefix_padding_ms": REALTIME_SERVER_VAD_PREFIX_MS,
                    "silence_duration_ms": REALTIME_SERVER_VAD_SILENCE_MS,
                },
            }
        },
    }
    if REALTIME_TRANSCRIBE_LANGUAGE:
        session["audio"]["input"]["transcription"]["language"] = REALTIME_TRANSCRIBE_LANGUAGE
    if REALTIME_TRANSCRIBE_PROMPT:
        session["audio"]["input"]["transcription"]["prompt"] = REALTIME_TRANSCRIBE_PROMPT
    return {
        "type": "session.update",
        "session": session,
    }


def build_realtime_session_config(*, server_vad: bool, transcribe_model: str) -> dict[str, Any]:
    session_config: dict[str, Any] = {
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": REALTIME_INPUT_SAMPLE_RATE},
                "transcription": {"model": transcribe_model},
            }
        },
    }
    if server_vad:
        session_config["audio"]["input"]["turn_detection"] = {"type": "server_vad"}
    else:
        session_config["audio"]["input"]["turn_detection"] = None
    if REALTIME_TRANSCRIBE_LANGUAGE:
        session_config["audio"]["input"]["transcription"]["language"] = REALTIME_TRANSCRIBE_LANGUAGE
    if REALTIME_TRANSCRIBE_PROMPT:
        session_config["audio"]["input"]["transcription"]["prompt"] = REALTIME_TRANSCRIBE_PROMPT
    return session_config


def transcribe_with_gpt4o_realtime(chunks: list) -> str:
    """Transcribe full audio with GPT-4o transcribe-family models."""
    if not chunks:
        return ""
    dep_error = realtime_dependency_error()
    if dep_error:
        log("[GPT-4o Realtime]", dep_error)
        return ""
    try:
        pcm = np.concatenate(chunks, axis=0).astype(np.int16, copy=False)
        if pcm.size == 0:
            return ""

        client = get_openai_client()
        text, used_model = transcribe_pcm16_with_models(
            client,
            pcm,
            sample_rate=SAMPLE_RATE,
            model_candidates=realtime_transcribe_model_candidates(),
            prompt=REALTIME_TRANSCRIBE_PROMPT,
            language=REALTIME_TRANSCRIBE_LANGUAGE,
        )
        if text:
            log(f"[GPT-4o Realtime] Used model '{used_model}' for full transcription.")
        return text
    except Exception as exc:  # pylint: disable=broad-except
        log("[GPT-4o Realtime error]", exc)
        return ""


def transcribe_with_gpt4o_realtime_stream_chunked(
    audio_queue: "queue.Queue[np.ndarray]",
    stop_event: threading.Event,
    on_delta: Callable[[str], None] | None = None,
) -> str:
    """Legacy local chunking path (retained for rollback); strict mode does not call this."""
    dep_error = realtime_dependency_error()
    if dep_error:
        log("[GPT-4o Realtime]", dep_error)
        return ""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        model_candidates = realtime_transcribe_model_candidates()
        live_text = ""
        pending_chunks: list[np.ndarray] = []
        pending_samples = 0
        min_chunk_s = max(0.35, REALTIME_LIVE_MIN_CHUNK_S)
        max_chunk_s = max(min_chunk_s, REALTIME_LIVE_MAX_CHUNK_S)
        silence_required_s = max(0.2, REALTIME_LIVE_SILENCE_S)
        silence_rms_threshold = max(0.001, REALTIME_LIVE_SILENCE_RMS)
        flush_interval_s = max(0.4, REALTIME_COMMIT_INTERVAL_S)
        min_flush_samples = int(SAMPLE_RATE * min_chunk_s)
        max_flush_samples = int(SAMPLE_RATE * max_chunk_s)
        last_flush_at = time.monotonic()
        silence_run_s = 0.0
        logged_live = False
        active_model = ""
        log(
            "[Realtime]",
            f"Live chunking min={min_chunk_s:.2f}s max={max_chunk_s:.2f}s",
            f"silence={silence_required_s:.2f}s (rms<={silence_rms_threshold:.4f}).",
        )

        while True:
            should_finish = stop_event.is_set() and audio_queue.empty()
            chunk: np.ndarray | None = None
            if not should_finish:
                try:
                    chunk = audio_queue.get(timeout=0.05)
                except queue.Empty:
                    chunk = None
            if chunk is not None and chunk.size:
                chunk_i16 = chunk.astype(np.int16, copy=False)
                pending_chunks.append(chunk_i16)
                chunk_samples = int(chunk_i16.size)
                pending_samples += chunk_samples
                if chunk_samples:
                    chunk_norm = chunk_i16.astype(np.float32) / 32767.0
                    chunk_rms = float(np.sqrt(np.mean(chunk_norm * chunk_norm)))
                    if chunk_rms <= silence_rms_threshold:
                        silence_run_s += chunk_samples / SAMPLE_RATE
                    else:
                        silence_run_s = 0.0

            now = time.monotonic()
            should_flush = False
            if pending_samples >= max_flush_samples or (
                pending_samples >= min_flush_samples
                and silence_run_s >= silence_required_s
                and (now - last_flush_at) >= flush_interval_s
            ):
                should_flush = True
            if should_finish and pending_samples > 0:
                should_flush = True

            if should_flush:
                segment_pcm = np.concatenate(pending_chunks, axis=0).astype(np.int16, copy=False)
                pending_chunks.clear()
                pending_samples = 0
                last_flush_at = now
                silence_run_s = 0.0

                segment_text, used_model = transcribe_pcm16_with_models(
                    client,
                    segment_pcm,
                    sample_rate=SAMPLE_RATE,
                    model_candidates=model_candidates,
                    prompt=REALTIME_TRANSCRIBE_PROMPT,
                    language=REALTIME_TRANSCRIBE_LANGUAGE,
                )
                if used_model and used_model != active_model:
                    active_model = used_model
                    log(f"[Realtime] Live chunk model: {used_model}.")
                if segment_text:
                    if not logged_live:
                        log("[Realtime] Live chunk transcription active.")
                        logged_live = True
                    delta_text = segment_text.strip()
                    if delta_text:
                        if live_text and not live_text.endswith((" ", "\n")):
                            delta_text = " " + delta_text
                        live_text += delta_text
                        if on_delta is not None:
                            on_delta(delta_text)

            if should_finish and pending_samples == 0:
                break

        return live_text.strip()
    except Exception as exc:  # pylint: disable=broad-except
        log("[GPT-4o Realtime error]", exc)
        return ""


def transcribe_with_gpt4o_realtime_stream_server_vad(
    audio_queue: "queue.Queue[np.ndarray]",
    stop_event: threading.Event,
    on_delta: Callable[[str], None] | None = None,
) -> str:
    try:
        from websockets.sync.client import connect
    except Exception as exc:  # pylint: disable=broad-except
        log("[Realtime] Server VAD websocket unavailable:", exc)
        return ""

    model_candidates = realtime_transcribe_model_candidates()
    last_error: BaseException | None = None
    for model_name in model_candidates:
        try:
            ws_url = REALTIME_WS_URL
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
            if REALTIME_WS_USE_BETA_HEADER:
                headers["OpenAI-Beta"] = "realtime=v1"
            log(f"[Realtime] Server VAD connect url='{ws_url}' model='{model_name}'.")
            with connect(
                ws_url,
                additional_headers=headers,
                open_timeout=8,
                close_timeout=2,
                max_size=2**22,
            ) as ws:
                ws.send(json.dumps(build_transcription_session_update_event(model_name)))

                transcripts: list[str] = []
                partials: list[str] = []
                idle_after_stop_s = 0.0
                saw_delta_since_completed = False
                logged_live_delta = False
                pending_commit = False
                stream_finished = False

                while True:
                    while True:
                        try:
                            chunk = audio_queue.get_nowait()
                        except queue.Empty:
                            break
                        if chunk.size == 0:
                            continue
                        pcm_resampled = resample_pcm16_mono(
                            chunk.astype(np.int16, copy=False),
                            SAMPLE_RATE,
                            REALTIME_INPUT_SAMPLE_RATE,
                        )
                        if pcm_resampled.size == 0:
                            continue
                        ws.send(
                            json.dumps(
                                {
                                    "type": "input_audio_buffer.append",
                                    "audio": base64.b64encode(pcm_resampled.tobytes()).decode(
                                        "ascii"
                                    ),
                                }
                            )
                        )
                        pending_commit = True

                    if stop_event.is_set() and audio_queue.empty() and not stream_finished:
                        if pending_commit:
                            ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                            pending_commit = False
                        stream_finished = True

                    try:
                        message = ws.recv(timeout=0.1)
                    except TimeoutError:
                        message = None
                    except Exception as exc:  # pylint: disable=broad-except
                        if stream_finished:
                            break
                        raise exc

                    if message is None:
                        if stream_finished:
                            idle_after_stop_s += 0.1
                            if idle_after_stop_s >= REALTIME_FINAL_WAIT_S:
                                break
                        continue

                    idle_after_stop_s = 0.0
                    if isinstance(message, bytes):
                        message = message.decode("utf-8", errors="replace")
                    try:
                        event = json.loads(message)
                    except Exception:  # pylint: disable=broad-except
                        continue
                    event_type = str(event.get("type", ""))
                    if event_type == "session.created":
                        session_obj = event.get("session", {})
                        if isinstance(session_obj, dict):
                            session_type = str(session_obj.get("type", "") or "")
                            if session_type and session_type != "transcription":
                                raise RuntimeError(
                                    f"Realtime session type mismatch: expected transcription, got {session_type!r}."
                                )
                        continue
                    if event_type == "session.updated":
                        continue
                    if event_type == "conversation.item.input_audio_transcription.delta":
                        delta_text = str(event.get("delta", "") or "")
                        if delta_text:
                            saw_delta_since_completed = True
                            partials.append(delta_text)
                            if not logged_live_delta:
                                log("[Realtime] Server VAD delta stream active.")
                                logged_live_delta = True
                            if on_delta is not None:
                                on_delta(delta_text)
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        transcript = str(event.get("transcript", "") or "").strip()
                        if transcript:
                            transcripts.append(transcript)
                            if on_delta is not None and not saw_delta_since_completed:
                                on_delta(transcript + " ")
                        saw_delta_since_completed = False
                    elif event_type == "error":
                        err_obj = event.get("error", {})
                        if isinstance(err_obj, dict):
                            err_msg = str(
                                err_obj.get("message")
                                or err_obj.get("type")
                                or "unknown realtime error"
                            )
                        else:
                            err_msg = str(err_obj)
                        raise RuntimeError(f"Realtime error: {err_msg}")

                transcript_text = " ".join(part for part in transcripts if part).strip()
                if transcript_text:
                    return transcript_text
                partial_text = "".join(partials).strip()
                if partial_text:
                    return partial_text
        except Exception as exc:  # pylint: disable=broad-except
            last_error = exc
            continue

    if last_error:
        log("[GPT-4o Realtime error]", last_error)
        model_list = ", ".join(model_candidates)
        log(
            "[Realtime]",
            f"Server VAD model candidates tried: {model_list}.",
        )
    return ""


def transcribe_with_gpt4o_realtime_stream(
    audio_queue: "queue.Queue[np.ndarray]",
    stop_event: threading.Event,
    on_delta: Callable[[str], None] | None = None,
) -> str:
    text = transcribe_with_gpt4o_realtime_stream_server_vad(
        audio_queue,
        stop_event,
        on_delta=on_delta,
    )
    if not text:
        log("[Realtime] Server-side realtime produced no transcript.")
    return text


def transcribe_audio(chunks: list, engine: str) -> str:
    normalized = normalize_transcription_engine(engine)
    if normalized == TRANSCRIPTION_ENGINE_GPT4O_REALTIME:
        return transcribe_with_gpt4o_realtime(chunks)
    return transcribe_with_whisper(chunks)


# -------------------- Orchestration --------------------


def begin_session_start(
    mode: str,
    hotkey_name: str,
    hotkey_kind: str,
    hotkey_tokens: tuple[str, ...],
    device_index: int | None,
    device_label: str,
) -> bool:
    with state.lock:
        if state.is_listening or state.session_start_pending:
            return False
        state.session_start_pending = True
    try:
        threading.Thread(
            target=start_listening,
            args=(mode, hotkey_name, hotkey_kind, hotkey_tokens, device_index, device_label),
            daemon=True,
        ).start()
    except Exception:
        with state.lock:
            state.session_start_pending = False
        raise
    return True


def start_listening(
    mode: str,
    hotkey_name: str,
    hotkey_kind: str,
    hotkey_tokens: tuple[str, ...],
    device_index: int | None,
    device_label: str,
):
    if not OPENAI_API_KEY:
        log("ERROR: OPENAI_API_KEY not set.")
        with state.lock:
            state.session_start_pending = False
        return
    enforce_transcription_engine_dependencies()

    label_text = device_label or DEFAULT_DEVICE_LABEL
    record_buffer: list[np.ndarray] = []
    buffer_lock = threading.Lock()

    with state.lock:
        if state.is_listening:
            state.session_start_pending = False
            return
        state.session_counter += 1
        session_id = state.session_counter
        state.active_session_id = session_id
        state.session_start_pending = False
        state.is_listening = True
        state.is_transcribing = False
        state.should_stop = False
        state.transcript_final = ""
        state.muted_warning = False
        state.last_audio_time = time.monotonic()
        state.last_audio_rms = 0.0
        state.mode = mode
        state.active_hotkey = hotkey_name
        state.active_hotkey_kind = hotkey_kind
        state.active_hotkey_tokens = hotkey_tokens
        state.active_device_label = label_text
    update_tray_status()

    with state.lock:
        toggle_mode = state.toggle_mode_enabled
        transcription_engine = state.transcription_engine
    label = "Dictate" if mode == MODE_DICTATION else "Log"
    action = "Tap" if toggle_mode else "Hold"
    use_realtime_streaming = transcription_engine == TRANSCRIPTION_ENGINE_GPT4O_REALTIME
    realtime_worker: threading.Thread | None = None
    realtime_queue: queue.Queue[np.ndarray] | None = None
    realtime_stop_event: threading.Event | None = None
    realtime_result: dict[str, str] = {"text": ""}
    realtime_delta_parts: list[str] = []
    realtime_delta_lock = threading.Lock()
    with state.output_condition:
        session_is_next_output = state.next_output_session_id == session_id
    live_typing_enabled = (
        use_realtime_streaming
        and mode == MODE_DICTATION
        and REALTIME_LIVE_TYPING_ENABLED
        and session_is_next_output
    )

    on_audio_chunk: Callable[[np.ndarray], None] | None = None
    if use_realtime_streaming:
        realtime_queue = queue.Queue(maxsize=512)
        realtime_stop_event = threading.Event()

        def on_delta_text(delta_text: str) -> None:
            with realtime_delta_lock:
                realtime_delta_parts.append(delta_text)
            if not live_typing_enabled:
                return
            try:
                type_text_direct(delta_text)
            except Exception as exc:  # pylint: disable=broad-except
                log("[Realtime] Live typing output failed, retrying via paste:", exc)
                try:
                    pyperclip.copy(delta_text)
                    time.sleep(0.01)
                    send_paste_shortcut()
                except Exception as paste_exc:  # pylint: disable=broad-except
                    log("[Realtime] Live typing paste fallback failed:", paste_exc)

        def run_realtime_stream() -> None:
            assert realtime_queue is not None
            assert realtime_stop_event is not None
            realtime_result["text"] = transcribe_with_gpt4o_realtime_stream(
                realtime_queue,
                realtime_stop_event,
                on_delta=on_delta_text,
            )

        realtime_worker = threading.Thread(target=run_realtime_stream, daemon=True)

        def on_audio_chunk(chunk: np.ndarray) -> None:
            assert realtime_queue is not None
            try:
                realtime_queue.put_nowait(chunk)
            except queue.Full:
                return

    recorder = AudioRecorder(
        device_index=device_index,
        buffer=record_buffer,
        buffer_lock=buffer_lock,
        on_chunk=on_audio_chunk,
    )
    started, _active_index, active_label = start_recorder_with_fallback(
        recorder,
        label,
        label_text,
    )
    if not started:
        if realtime_stop_event is not None:
            realtime_stop_event.set()
        if realtime_worker is not None:
            realtime_worker.join(timeout=1.0)
        log(f"[Audio] {label} start aborted; no usable input device.")
        with state.lock:
            if state.active_session_id == session_id:
                state.session_start_pending = False
                state.is_listening = False
                state.active_hotkey = ""
                state.active_hotkey_kind = ""
                state.active_hotkey_tokens = ()
                state.active_device_label = ""
                state.should_stop = False
                state.muted_warning = False
        update_tray_status()
        return

    label_text = active_label or DEFAULT_DEVICE_LABEL
    with state.lock:
        if state.active_session_id == session_id:
            state.active_device_label = label_text
    update_tray_status()
    if realtime_worker is not None and not realtime_worker.is_alive():
        realtime_worker.start()
    log(f"\n[Listening-{label}] {action} {hotkey_name}... (device: {label_text})")
    maybe_beep(BEEP_START_PATTERN)

    try:
        while True:
            time.sleep(0.02)
            with state.lock:
                monitor_enabled = state.monitor_enabled
                last_audio_time = state.last_audio_time
                muted_warning = state.muted_warning
            with state.lock:
                should_stop = state.should_stop and state.active_session_id == session_id
            if should_stop:
                break
            if monitor_enabled:
                idle_s = time.monotonic() - last_audio_time
                if idle_s >= MUTE_WARNING_AFTER_S and not muted_warning:
                    with state.lock:
                        state.muted_warning = True
                    log("[Audio] You may be muted or too quiet.")
                    update_tray_status()
                elif idle_s < MUTE_WARNING_AFTER_S and muted_warning:
                    with state.lock:
                        state.muted_warning = False
                    update_tray_status()
    finally:
        recorder.stop()
        maybe_beep(BEEP_STOP_PATTERN)

    with state.lock:
        if state.active_session_id == session_id:
            state.session_start_pending = False
            state.is_listening = False
            state.active_hotkey = ""
            state.active_hotkey_kind = ""
            state.active_hotkey_tokens = ()
            state.active_device_label = ""
            state.should_stop = False
            state.muted_warning = False
    update_tray_status()

    with buffer_lock:
        chunks = [chunk.copy() for chunk in record_buffer]

    mark_transcription_started()
    log(f"\n[Transcribing] {transcription_engine_label(transcription_engine)} request sent...")

    audio_duration_s = sum(len(chunk) for chunk in chunks) / SAMPLE_RATE if chunks else 0.0
    transcribe_start = time.perf_counter()
    transcript_text = ""
    transcription_engine_used = transcription_engine
    transcribe_error: Exception | None = None
    final_text = ""
    try:
        if (
            use_realtime_streaming
            and realtime_worker is not None
            and realtime_stop_event is not None
        ):
            realtime_stop_event.set()
            join_timeout_s = max(6.0, min(20.0, audio_duration_s + 4.0))
            realtime_worker.join(timeout=join_timeout_s)
            if realtime_worker.is_alive():
                log(
                    "[Realtime] Stream worker timed out; strict server-side mode will not fall back."
                )
                log(
                    "[Realtime] Check network stability and realtime model access for your API key."
                )
                transcript_text = ""
                transcription_engine_used = TRANSCRIPTION_ENGINE_GPT4O_REALTIME
            else:
                transcript_text = (realtime_result.get("text", "") or "").strip()
                if not transcript_text:
                    log("[Realtime] No transcript returned from server-side realtime.")
                    log(
                        "[Realtime] Strict mode keeps realtime-only behavior; no Whisper fallback is applied."
                    )
                    transcription_engine_used = TRANSCRIPTION_ENGINE_GPT4O_REALTIME
        else:
            transcript_text = transcribe_audio(chunks, transcription_engine)
            if (
                transcription_engine == TRANSCRIPTION_ENGINE_GPT4O_REALTIME and transcript_text
            ) or transcription_engine == TRANSCRIPTION_ENGINE_GPT4O_REALTIME:
                transcription_engine_used = TRANSCRIPTION_ENGINE_GPT4O_REALTIME
        final_text = apply_punctuation_options(transcript_text)
    except Exception as exc:  # pylint: disable=broad-except
        transcribe_error = exc
        log("[Transcription error]", exc)
    transcribe_end = time.perf_counter()

    transcription_ms = (transcribe_end - transcribe_start) * 1000.0
    audio_minutes = audio_duration_s / 60.0
    transcription_minutes = (transcribe_end - transcribe_start) / 60.0
    total_minutes = audio_minutes + transcription_minutes
    word_count = len(final_text.split())
    wpm = word_count / total_minutes if total_minutes > 0 else 0.0

    with state.output_condition:
        waiting_for_earlier_output = session_id != state.next_output_session_id
    if waiting_for_earlier_output:
        log(f"[Session {session_id}] Waiting for earlier transcript output.")
    wait_for_output_turn(session_id)
    try:
        with state.lock:
            state.transcript_final = final_text

        if transcribe_error is not None:
            log(f"[Session {session_id}] Transcription failed; skipping output.")
            return

        used_label = transcription_engine_label(transcription_engine_used)
        log(
            f"[Metrics] {used_label} {transcription_ms:.0f} ms | "
            f"WPM {wpm:.1f} (words={word_count}, audio={audio_duration_s * 1000:.0f} ms)"
        )

        if final_text:
            if mode == MODE_WORKLOG:
                append_work_log_entry(final_text)
            else:
                log("\n[Final]:", final_text)
                if PASTE_ON_RELEASE:
                    prepared_final = prepare_clipboard_text(final_text)
                    live_applied = False
                    live_text = ""
                    if live_typing_enabled:
                        with realtime_delta_lock:
                            live_text = "".join(realtime_delta_parts)
                        live_applied = try_apply_live_dictation_correction(
                            live_text, prepared_final
                        )
                        if live_applied:
                            log("[Realtime] Live dictation finalized in place.")
                    if live_typing_enabled and live_text and not live_applied:
                        log(
                            "[Realtime] Live text changed too much to auto-correct; skipped final paste to avoid duplicates."
                        )
                    elif not live_applied:
                        if paste_text(final_text):
                            log("[Pasted] Sent clipboard text to the active window.")
                        else:
                            log("[Clipboard] Transcript copied, but paste was not sent.")
        else:
            log("\n(No speech captured.)")
    finally:
        advance_output_turn()
        mark_transcription_finished(final_text)


# -------------------- Hotkey handling --------------------


def console_is_foreground() -> bool:
    if not supports_foreground_console_detection() or USER32 is None or KERNEL32 is None:
        return False
    try:
        foreground = USER32.GetForegroundWindow()
        console = KERNEL32.GetConsoleWindow()
    except Exception:  # pylint: disable=broad-except
        return False
    return foreground != 0 and console != 0 and foreground == console


def toggle_worklog_stereo_mix() -> None:
    with state.lock:
        currently_stereo = state.worklog_uses_stereo_mix
        default_index = state.worklog_default_device_index
        default_label = state.worklog_default_device_label
        is_active = state.is_listening and state.mode == MODE_WORKLOG

    if currently_stereo:
        index_text = default_index if default_index is not None else "default"
        with state.lock:
            state.worklog_device_index = default_index
            state.worklog_device_label = default_label
            state.worklog_uses_stereo_mix = False
            if is_active:
                state.active_device_label = default_label
        log(f"[Worklog audio] Reverted to {default_label} (index={index_text}).")
        refresh_tray_menu()
        return

    idx, label = lookup_input_device_by_name(STEREO_MIX_SEARCH)
    if idx is None:
        log(f"[Worklog audio] Stereo mix device matching '{STEREO_MIX_SEARCH}' not found.")
        return

    with state.lock:
        state.worklog_device_index = idx
        state.worklog_device_label = label
        state.worklog_uses_stereo_mix = True
        state.stereo_mix_device_index = idx
        state.stereo_mix_device_label = label
        if is_active:
            state.active_device_label = label
    log(f"[Worklog audio] Stereo mix enabled -> {label} (index={idx}).")
    refresh_tray_menu()


def handle_spacebar_press() -> None:
    if not console_is_foreground():
        return
    with state.lock:
        if state.is_listening:
            return
    toggle_worklog_stereo_mix()


def get_key_name(key) -> str:
    if isinstance(key, pynput_keyboard.KeyCode):
        return canonicalize_hotkey_token(key.char or "")
    try:
        name = key.name
    except AttributeError:
        return ""
    return canonicalize_hotkey_token(name or "")


def is_shift_key_name(key_name: str) -> bool:
    return key_name == "SHIFT"


def dictation_uses_system_audio() -> bool:
    with state.lock:
        shift_active = bool(state.shift_keys_down)
        uses_shift_in_hotkey = "SHIFT" in state.dictation_hotkey_tokens
    return shift_active and not uses_shift_in_hotkey


def start_worklog_after_hold(press_token: int) -> None:
    time.sleep(WORKLOG_TAP_MAX_S)
    with state.lock:
        if state.worklog_press_token != press_token:
            return
        if not state.worklog_is_pressed:
            return
        if state.worklog_double_tap_active:
            return
        if state.is_listening or state.session_start_pending:
            return
        device_index = state.worklog_device_index
        device_label = state.worklog_device_label
    begin_session_start(
        MODE_WORKLOG,
        HOTKEY_WORKLOG,
        HOTKEY_KIND_KEYBOARD,
        (HOTKEY_WORKLOG,),
        device_index,
        device_label,
    )


def on_press(key):
    key_name = get_key_name(key)
    if not key_name:
        return

    if key_name == "SPACE":
        handle_spacebar_press()

    double_tap = False
    dictation_start = False
    worklog_start_immediate = False
    worklog_press_token: int | None = None
    dictation_hotkey_kind = HOTKEY_KIND_KEYBOARD
    dictation_hotkey_tokens: tuple[str, ...] = ()
    dictation_device_index: int | None = None
    dictation_device_label = ""
    worklog_device_index: int | None = None
    worklog_device_label = ""
    with state.lock:
        state.pressed_keys.add(key_name)
        if is_shift_key_name(key_name):
            state.shift_keys_down.add(key_name)
        if state.is_listening:
            if (
                state.toggle_mode_enabled
                and state.active_hotkey_kind == HOTKEY_KIND_KEYBOARD
                and key_name in state.active_hotkey_tokens
                and state.pressed_keys == set(state.active_hotkey_tokens)
            ):
                state.should_stop = True
            return
        if state.session_start_pending:
            return
        dictation_tokens = set(state.dictation_hotkey_tokens)
        pressed_matches_dictation = state.pressed_keys == dictation_tokens
        pressed_matches_shift_dictation = (
            "SHIFT" not in dictation_tokens and state.pressed_keys == (dictation_tokens | {"SHIFT"})
        )
        if (
            state.dictation_hotkey_kind == HOTKEY_KIND_KEYBOARD
            and dictation_tokens
            and (pressed_matches_dictation or pressed_matches_shift_dictation)
        ):
            dictation_start = True
            dictation_hotkey_kind = state.dictation_hotkey_kind
            dictation_hotkey_tokens = state.dictation_hotkey_tokens
            dictation_device_index = state.dictation_device_index
            dictation_device_label = state.dictation_device_label
        if key_name == HOTKEY_WORKLOG:
            if state.toggle_mode_enabled:
                worklog_start_immediate = True
                worklog_device_index = state.worklog_device_index
                worklog_device_label = state.worklog_device_label
            else:
                now = time.monotonic()
                last_tap = state.last_worklog_tap_time
                state.worklog_press_time = now
                state.worklog_is_pressed = True
                if last_tap and (now - last_tap) <= WORKLOG_DOUBLE_TAP_WINDOW_S:
                    state.last_worklog_tap_time = 0.0
                    state.worklog_double_tap_active = True
                    double_tap = True
                else:
                    state.worklog_press_token += 1
                    worklog_press_token = state.worklog_press_token

    if double_tap:
        open_work_log()
        return

    if dictation_start:
        session_hotkey_tokens = dictation_hotkey_tokens
        if dictation_uses_system_audio():
            dictation_device_index, dictation_device_label, ok = resolve_system_audio_input_device()
            if not ok:
                log(
                    "[Audio] System audio capture unavailable."
                    f" No input device matched '{system_audio_search_hint()}'."
                )
                return
            session_hotkey_tokens = canonicalize_hotkey_tokens([*dictation_hotkey_tokens, "SHIFT"])
        begin_session_start(
            MODE_DICTATION,
            HOTKEY_DICTATION,
            dictation_hotkey_kind,
            session_hotkey_tokens,
            dictation_device_index,
            dictation_device_label,
        )

    if key_name == HOTKEY_WORKLOG:
        if worklog_start_immediate:
            begin_session_start(
                MODE_WORKLOG,
                HOTKEY_WORKLOG,
                HOTKEY_KIND_KEYBOARD,
                (HOTKEY_WORKLOG,),
                worklog_device_index,
                worklog_device_label,
            )
        elif worklog_press_token is not None:
            threading.Thread(
                target=start_worklog_after_hold,
                args=(worklog_press_token,),
                daemon=True,
            ).start()


def on_release(key):
    key_name = get_key_name(key)
    if not key_name:
        return

    now = time.monotonic()
    with state.lock:
        if key_name == HOTKEY_WORKLOG:
            if state.worklog_double_tap_active:
                state.worklog_double_tap_active = False
                state.worklog_press_time = 0.0
                state.worklog_is_pressed = False
                state.pressed_keys.discard(key_name)
                if is_shift_key_name(key_name):
                    state.shift_keys_down.discard(key_name)
                return
            press_time = state.worklog_press_time
            state.worklog_press_time = 0.0
            state.worklog_is_pressed = False
            if press_time and (now - press_time) <= WORKLOG_TAP_MAX_S:
                state.last_worklog_tap_time = now
            else:
                state.last_worklog_tap_time = 0.0
        if state.toggle_mode_enabled:
            state.pressed_keys.discard(key_name)
            if is_shift_key_name(key_name):
                state.shift_keys_down.discard(key_name)
            return
        if (
            state.is_listening
            and state.active_hotkey_kind == HOTKEY_KIND_KEYBOARD
            and key_name in state.active_hotkey_tokens
        ):
            state.should_stop = True
        state.pressed_keys.discard(key_name)
        if is_shift_key_name(key_name):
            state.shift_keys_down.discard(key_name)


def on_mouse_click(_x, _y, button, pressed):
    if button != DICTATION_MOUSE_BUTTON:
        return

    if pressed:
        with state.lock:
            if state.is_listening:
                if state.toggle_mode_enabled and state.active_hotkey_kind == HOTKEY_KIND_MOUSE:
                    state.should_stop = True
                return
            if state.session_start_pending or state.dictation_hotkey_kind != HOTKEY_KIND_MOUSE:
                return
            device_index = state.dictation_device_index
            device_label = state.dictation_device_label
        if dictation_uses_system_audio():
            device_index, device_label, ok = resolve_system_audio_input_device()
            if not ok:
                log(
                    "[Audio] System audio capture unavailable."
                    f" No input device matched '{system_audio_search_hint()}'."
                )
                return
        begin_session_start(
            MODE_DICTATION,
            HOTKEY_DICTATION,
            HOTKEY_KIND_MOUSE,
            (),
            device_index,
            device_label,
        )
        return

    with state.lock:
        if (
            not state.toggle_mode_enabled
            and state.is_listening
            and state.active_hotkey_kind == HOTKEY_KIND_MOUSE
        ):
            state.should_stop = True


# -------------------- Main --------------------


def ensure_work_log_exists() -> None:
    try:
        WORK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not WORK_LOG_PATH.exists():
            WORK_LOG_PATH.touch()
    except Exception as exc:  # pylint: disable=broad-except
        log("[Tray] Unable to create work log file:", exc)


def open_work_log(_icon=None, _item=None) -> None:
    ensure_work_log_exists()
    try:
        if IS_WINDOWS and hasattr(os, "startfile"):
            os.startfile(str(WORK_LOG_PATH))  # type: ignore[attr-defined]
        else:
            log(f"[Tray] Work log located at {WORK_LOG_PATH}")
    except Exception as exc:  # pylint: disable=broad-except
        log("[Tray] Unable to open work log file:", exc)


def update_tray_icon() -> None:
    if tray_icon is None:
        return
    with state.lock:
        is_listening = state.is_listening
        is_transcribing = state.is_transcribing
        spinner_step = state.tray_spinner_step
    if is_listening:
        color = TRAY_COLOR_LISTENING
    elif is_transcribing:
        color = TRAY_COLOR_TRANSCRIBING
    else:
        color = TRAY_COLOR_READY
    spinner = spinner_step if is_transcribing and not is_listening else None
    try:
        tray_icon.icon = create_tray_icon_image(color=color, spinner_step=spinner)
    except Exception as exc:  # pylint: disable=broad-except
        log("[Tray] Icon update failed:", exc)


def update_tray_tooltip() -> None:
    if tray_icon is None:
        return
    with state.lock:
        tooltip_enabled = state.tooltip_enabled
        is_listening = state.is_listening
        is_transcribing = state.is_transcribing
        mode = state.mode
        device_label = state.active_device_label
        muted_warning = state.muted_warning
        transcription_engine = state.transcription_engine

    if not tooltip_enabled:
        tray_icon.title = TRAY_TITLE
        return

    status = "Ready"
    if is_transcribing:
        status = "Transcribing"
    elif is_listening:
        status = "Listening"

    details = []
    if is_listening or is_transcribing:
        details.append("Dictation" if mode == MODE_DICTATION else "Worklog")
        if device_label:
            details.append(device_label)
        details.append(transcription_engine_label(transcription_engine))
    if muted_warning:
        details.append("Muted?")

    detail_text = ", ".join(details)
    if detail_text:
        tray_icon.title = f"{TRAY_TITLE} - {status} ({detail_text})"
    else:
        tray_icon.title = f"{TRAY_TITLE} - {status}"


def update_tray_status() -> None:
    update_tray_tooltip()
    update_tray_icon()


def refresh_tray_menu() -> None:
    if tray_icon is None:
        return
    update_tray_status()
    if hasattr(tray_icon, "update_menu"):
        tray_icon.update_menu()


def rebuild_tray_menu() -> None:
    if tray_icon is None:
        return
    tray_icon.menu = build_menu()
    refresh_tray_menu()


def toggle_attr(attr: str) -> None:
    with state.lock:
        current = getattr(state, attr)
        setattr(state, attr, not current)
    refresh_tray_menu()


def set_paste_suffix_mode(mode: str) -> None:
    with state.lock:
        state.paste_suffix_mode = mode
    refresh_tray_menu()


def set_transcription_engine(engine: str) -> None:
    normalized = normalize_transcription_engine(engine)
    if normalized == TRANSCRIPTION_ENGINE_GPT4O_REALTIME:
        dep_error = realtime_dependency_error()
        if dep_error:
            log("[Transcription engine]", dep_error)
            return
    with state.lock:
        state.transcription_engine = normalized
    save_settings_to_disk()
    log(f"[Transcription engine] {transcription_engine_label(normalized)}")
    if normalized == TRANSCRIPTION_ENGINE_GPT4O_REALTIME:
        log("[Transcription engine] Strict server-side realtime mode enabled (no local fallback).")
    refresh_tray_menu()


def toggle_punctuation_terminal(_icon=None, _item=None) -> None:
    toggle_attr("punctuation_terminal")


def toggle_punctuation_capitalize(_icon=None, _item=None) -> None:
    toggle_attr("punctuation_capitalize")


def toggle_punctuation_normalize(_icon=None, _item=None) -> None:
    toggle_attr("punctuation_normalize_spaces")


def toggle_beeps(_icon=None, _item=None) -> None:
    toggle_attr("beeps_enabled")
    with state.lock:
        enabled = state.beeps_enabled
    if enabled and (not IS_WINDOWS or winsound is None):
        log("[Beep] System beeps are unavailable on this platform.")


def toggle_tooltip(_icon=None, _item=None) -> None:
    toggle_attr("tooltip_enabled")


def toggle_toggle_mode(_icon=None, _item=None) -> None:
    toggle_attr("toggle_mode_enabled")


def toggle_monitor(_icon=None, _item=None) -> None:
    with state.lock:
        state.monitor_enabled = not state.monitor_enabled
        if state.monitor_enabled:
            state.last_audio_time = time.monotonic()
            state.muted_warning = False
        else:
            state.muted_warning = False
    refresh_tray_menu()


def apply_default_preset(_icon=None, _item=None) -> None:
    with state.lock:
        state.beeps_enabled = False
        state.tooltip_enabled = False
        state.toggle_mode_enabled = False
        state.monitor_enabled = False
        state.paste_suffix_mode = DEFAULT_SUFFIX_MODE
        state.punctuation_terminal = True
        state.punctuation_capitalize = False
        state.punctuation_normalize_spaces = False
        state.transcription_engine = TRANSCRIPTION_ENGINE_WHISPER
    save_settings_to_disk()
    refresh_tray_menu()


def apply_bells_preset(_icon=None, _item=None) -> None:
    with state.lock:
        state.beeps_enabled = True
        state.tooltip_enabled = True
        state.monitor_enabled = True
    refresh_tray_menu()


def is_systemd_managed() -> bool:
    value = os.getenv(SYSTEMD_MANAGED_ENV, "")
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def run_systemd_action(action: str) -> bool:
    try:
        subprocess.run(
            ["systemctl", "--user", action, SYSTEMD_SERVICE_NAME],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pylint: disable=broad-except
        log(f"[Tray] Unable to {action} {SYSTEMD_SERVICE_NAME}:", exc)
        return False
    return True


def parse_hotkey_capture_output(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def prompt_for_hotkey(_icon=None, _item=None) -> None:
    if not HOTKEY_CAPTURE_HELPER_PATH.exists():
        log(f"[Tray] Hotkey capture helper not found: {HOTKEY_CAPTURE_HELPER_PATH}")
        return
    try:
        completed = subprocess.run(
            [sys.executable, str(HOTKEY_CAPTURE_HELPER_PATH)],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(SCRIPT_DIR),
        )
    except Exception as exc:  # pylint: disable=broad-except
        log("[Tray] Unable to launch the hotkey capture helper:", exc)
        return

    payload = parse_hotkey_capture_output(completed.stdout)
    if not payload.get("accepted"):
        log("[Tray] Hotkey change canceled.")
        return

    tokens = canonicalize_hotkey_tokens(payload.get("tokens", []))
    if not tokens:
        log("[Tray] No capturable hotkey was drafted.")
        return

    set_dictation_hotkey(HOTKEY_KIND_KEYBOARD, tokens)
    log(f"[Tray] Dictation hotkey set to {dictation_hotkey_summary()}.")


def use_touchpad_hotkey(_icon=None, _item=None) -> None:
    set_dictation_hotkey(HOTKEY_KIND_MOUSE)
    log(f"[Tray] Dictation hotkey set to {DEFAULT_DICTATION_HOTKEY_LABEL}.")


def quit_app(icon: TrayIconLike | None = None, _item=None) -> None:
    if is_systemd_managed():
        run_systemd_action("stop")
        return
    tray_exit(icon or tray_icon)


def restart_app(icon: TrayIconLike | None = None, _item=None) -> None:
    if is_systemd_managed():
        run_systemd_action("restart")
        return
    try:
        subprocess.Popen(
            [sys.executable, str(SCRIPT_DIR / "push_to_talk_realtime.py")],
            cwd=str(SCRIPT_DIR),
        )
    except Exception as exc:  # pylint: disable=broad-except
        log("[Tray] Unable to restart the app:", exc)
        return
    tray_exit(icon or tray_icon)


def refresh_audio_devices(_icon=None, _item=None) -> None:
    refresh_device_list()
    rebuild_tray_menu()


def make_device_action(role: str, idx: int | None, label: str):
    def action(_icon, _item):
        set_device_for_role(role, idx, label)

    return action


def build_device_menu(role: str) -> pystray.Menu:
    items = [
        pystray.MenuItem(
            DEFAULT_DEVICE_LABEL,
            make_device_action(role, None, DEFAULT_DEVICE_LABEL),
            radio=True,
            checked=lambda _item: is_device_selected(role, None),
        )
    ]
    for idx, label in DEVICE_LIST:
        items.append(
            pystray.MenuItem(
                label,
                make_device_action(role, idx, label),
                radio=True,
                checked=lambda _item, idx=idx: is_device_selected(role, idx),
            )
        )
    if role == MODE_WORKLOG:
        items.insert(
            0,
            pystray.MenuItem(
                "Toggle Stereo Mix (work log)",
                toggle_worklog_stereo_mix,
                checked=lambda _item: state.worklog_uses_stereo_mix,
            ),
        )
    return pystray.Menu(*items)


def build_transcription_menu() -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(
            "Whisper",
            lambda _icon, _item: set_transcription_engine(TRANSCRIPTION_ENGINE_WHISPER),
            radio=True,
            checked=lambda _item: state.transcription_engine == TRANSCRIPTION_ENGINE_WHISPER,
        ),
        pystray.MenuItem(
            "GPT-4o Realtime",
            lambda _icon, _item: set_transcription_engine(TRANSCRIPTION_ENGINE_GPT4O_REALTIME),
            radio=True,
            checked=lambda _item: state.transcription_engine == TRANSCRIPTION_ENGINE_GPT4O_REALTIME,
        ),
    )


def build_punctuation_menu() -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(
            "Suffix: None",
            lambda _icon, _item: set_paste_suffix_mode(SUFFIX_NONE),
            radio=True,
            checked=lambda _item: state.paste_suffix_mode == SUFFIX_NONE,
        ),
        pystray.MenuItem(
            "Suffix: Space",
            lambda _icon, _item: set_paste_suffix_mode(SUFFIX_SPACE),
            radio=True,
            checked=lambda _item: state.paste_suffix_mode == SUFFIX_SPACE,
        ),
        pystray.MenuItem(
            "Suffix: Newline",
            lambda _icon, _item: set_paste_suffix_mode(SUFFIX_NEWLINE),
            radio=True,
            checked=lambda _item: state.paste_suffix_mode == SUFFIX_NEWLINE,
        ),
        pystray.MenuItem(
            "Ensure terminal punctuation",
            toggle_punctuation_terminal,
            checked=lambda _item: state.punctuation_terminal,
        ),
        pystray.MenuItem(
            "Capitalize first letter",
            toggle_punctuation_capitalize,
            checked=lambda _item: state.punctuation_capitalize,
        ),
        pystray.MenuItem(
            "Normalize whitespace",
            toggle_punctuation_normalize,
            checked=lambda _item: state.punctuation_normalize_spaces,
        ),
    )


def build_options_menu() -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(
            "Toggle mode (tap to start/stop)",
            toggle_toggle_mode,
            checked=lambda _item: state.toggle_mode_enabled,
        ),
        pystray.MenuItem(
            "Beeps",
            toggle_beeps,
            checked=lambda _item: state.beeps_enabled,
        ),
        pystray.MenuItem(
            "Status tooltip",
            toggle_tooltip,
            checked=lambda _item: state.tooltip_enabled,
        ),
        pystray.MenuItem(
            "Mute monitor",
            toggle_monitor,
            checked=lambda _item: state.monitor_enabled,
        ),
    )


def build_menu() -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(f"Dictation hotkey: {dictation_hotkey_summary()}", None, enabled=False),
        pystray.MenuItem(f"Work log hotkey: {HOTKEY_WORKLOG}", None, enabled=False),
        pystray.MenuItem("Set Hotkey...", prompt_for_hotkey),
        pystray.MenuItem(
            "Use Three-Finger Touchpad Press",
            use_touchpad_hotkey,
            checked=lambda _item: state.dictation_hotkey_kind == HOTKEY_KIND_MOUSE,
        ),
        pystray.MenuItem("Default (no frills)", apply_default_preset),
        pystray.MenuItem("Bells and whistles", apply_bells_preset),
        pystray.MenuItem("Options", build_options_menu()),
        pystray.MenuItem("Transcription engine", build_transcription_menu()),
        pystray.MenuItem("Punctuation", build_punctuation_menu()),
        pystray.MenuItem("Dictation input device", build_device_menu(MODE_DICTATION)),
        pystray.MenuItem("Work log input device", build_device_menu(MODE_WORKLOG)),
        pystray.MenuItem("Refresh audio devices", refresh_audio_devices),
        pystray.MenuItem("Open work log", open_work_log),
        pystray.MenuItem("Restart", restart_app),
        pystray.MenuItem("Quit", quit_app),
    )


def create_tray_icon_image(
    size: int = TRAY_ICON_SIZE,
    color: tuple[int, int, int, int] = TRAY_COLOR_READY,
    spinner_step: int | None = None,
) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, size, size), fill=color)
    inset = int(size * 0.28)
    processing = spinner_step is not None
    if processing:
        inner_inset = int(size * 0.22)
        draw.ellipse(
            (inner_inset, inner_inset, size - inner_inset, size - inner_inset),
            fill=(30, 34, 40, 255),
        )
        ring_inset = int(size * 0.08)
        ring_width = max(4, int(size * 0.12))
        draw.arc(
            (ring_inset, ring_inset, size - ring_inset, size - ring_inset),
            start=0,
            end=359,
            fill=(255, 244, 214, 120),
            width=ring_width,
        )
        bar_left = int(size * 0.3)
        bar_right = int(size * 0.7)
        for y in (int(size * 0.36), int(size * 0.5), int(size * 0.64)):
            draw.rounded_rectangle(
                (bar_left, y - 2, bar_right, y + 2),
                radius=2,
                fill=(255, 244, 214, 255),
            )
    else:
        draw.rectangle((inset, inset, size - inset, size - inset), fill=(255, 255, 255, 255))
    if spinner_step is not None:
        ring_inset = int(size * 0.08)
        ring_width = max(4, int(size * 0.12))
        step_degrees = 360 / TRAY_SPINNER_STEPS
        start_angle = int((spinner_step % TRAY_SPINNER_STEPS) * step_degrees)
        draw.arc(
            (ring_inset, ring_inset, size - ring_inset, size - ring_inset),
            start=start_angle,
            end=start_angle + TRAY_SPINNER_SWEEP_DEG,
            fill=(255, 255, 255, 255),
            width=ring_width,
        )
    return image


def tray_animation_loop() -> None:
    while not shutdown_event.is_set():
        with state.lock:
            is_listening = state.is_listening
            is_transcribing = state.is_transcribing
        if is_transcribing and not is_listening:
            with state.lock:
                state.tray_spinner_step = (state.tray_spinner_step + 1) % TRAY_SPINNER_STEPS
            update_tray_icon()
            time.sleep(TRAY_SPINNER_INTERVAL_S)
            continue

        should_refresh = False
        with state.lock:
            if state.tray_spinner_step != 0:
                state.tray_spinner_step = 0
                should_refresh = True
        if should_refresh:
            update_tray_icon()
        time.sleep(0.1)


def start_tray_animation_loop() -> None:
    global tray_animation_thread
    if tray_animation_thread is not None and tray_animation_thread.is_alive():
        return
    tray_animation_thread = threading.Thread(target=tray_animation_loop, daemon=True)
    tray_animation_thread.start()


def tray_setup(_icon: TrayIconLike) -> None:
    _icon.visible = True  # required when using a custom setup callback
    enforce_transcription_engine_dependencies()
    start_transcription_warmup()
    with state.lock:
        selected_engine = state.transcription_engine
        engine_label = transcription_engine_label(selected_engine)
    log(
        f"Push-to-talk ready. {HOTKEY_DICTATION} for dictation/paste, "
        f"{HOTKEY_WORKLOG} for work log. Engine: {engine_label}."
    )
    if selected_engine == TRANSCRIPTION_ENGINE_GPT4O_REALTIME:
        log("[Transcription engine] Strict server-side realtime mode enabled (no local fallback).")
    refresh_tray_menu()
    start_tray_animation_loop()
    start_input_listeners()


def tray_exit(icon: TrayIconLike | None, _item=None) -> None:
    shutdown_event.set()
    stop_input_listeners()
    if icon is None:
        return
    icon.visible = False
    icon.stop()


def main() -> None:
    global tray_icon
    refresh_device_list()
    tray_icon = pystray.Icon(
        "push_to_talk_realtime",
        create_tray_icon_image(),
        TRAY_TITLE,
        build_menu(),
    )

    try:
        tray_icon.run(setup=tray_setup)
    except KeyboardInterrupt:
        log("\nExiting...")
        tray_exit(tray_icon)
    finally:
        stop_input_listeners()
        shutdown_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
