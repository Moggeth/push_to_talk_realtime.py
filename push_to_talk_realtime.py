#!/usr/bin/env python3
"""
Push-to-talk Whisper transcription:
- Hold F8 to dictate and paste upon release.
- Hold F9 to capture audio and log the transcript as a timestamped work entry.

Notes
-----
- Captures 16 kHz mono PCM from the default input device (set DEVICE_INDEX if needed).
- Uses Ctrl+V on Windows/Linux and Cmd+V on macOS to paste the final text.
- Requires OPENAI_API_KEY with Whisper access in the environment or a .env file.
"""

import ctypes
import os
import platform
import signal
import sys
import threading
import time
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

# Audio capture
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_DUR_S = 0.04  # 40 ms per audio chunk
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_DUR_S)
DEVICE_INDEX = None  # set to an index from sd.query_devices() if needed

# Behavior
MODE_DICTATION = "dictation"
MODE_WORKLOG = "worklog"

HOTKEY_DICTATION = "F8"
HOTKEY_WORKLOG = "F9"
PASTE_ON_RELEASE = True
DEFAULT_SUFFIX_MODE = SUFFIX_SPACE
WORKLOG_DOUBLE_TAP_WINDOW_S = 0.4
WORKLOG_TAP_MAX_S = 0.25
SCRIPT_DIR = Path(__file__).resolve().parent
WORK_LOG_PATH = Path(os.getenv("WORK_LOG_PATH") or (SCRIPT_DIR / "work_log.txt"))
DEFAULT_DEVICE_LABEL = "System default input"
STEREO_MIX_SEARCH = os.getenv("STEREO_MIX_SEARCH", "Stereo Mix")
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

# -------------------- State --------------------

TRAY_ICON_SIZE = 64
TRAY_TITLE = "Push-to-talk Whisper"
TRAY_COLOR_READY = (46, 160, 67, 255)
TRAY_COLOR_LISTENING = (220, 53, 69, 255)


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
    should_stop: bool = False
    transcript_final: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)
    mode: str = MODE_DICTATION
    active_hotkey: str = ""
    active_device_label: str = ""
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
    punctuation_terminal: bool = False
    punctuation_capitalize: bool = False
    punctuation_normalize_spaces: bool = False
    worklog_press_time: float = 0.0
    last_worklog_tap_time: float = 0.0
    worklog_double_tap_active: bool = False


state = SessionState()
shutdown_event = threading.Event()
keyboard_listener: pynput_keyboard.Listener | None = None
tray_icon: TrayIconLike | None = None
DEVICE_LIST: list[tuple[int, str]] = []

# -------------------- Utilities --------------------


def log(*a):
    print(*a, flush=True)


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
    def __init__(self, device_index: int | None, buffer: list, buffer_lock: threading.Lock):
        self.stream = None
        self.device_index = device_index
        self.buffer = buffer
        self.buffer_lock = buffer_lock

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

        from openai import OpenAI  # pip install openai

        client = OpenAI(api_key=OPENAI_API_KEY)

        pcm = np.concatenate(chunks, axis=0)
        wav_bytes = io.BytesIO()
        with wave.open(wav_bytes, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm.tobytes())
        wav_bytes.seek(0)
        wav_bytes.name = "recording.wav"  # hint format to the API

        response = client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=wav_bytes,
            response_format="json",
        )
        return getattr(response, "text", "") or ""
    except Exception as exc:  # pylint: disable=broad-except
        log("[Whisper error]", exc)
        return ""


# -------------------- Orchestration --------------------


def start_listening(
    mode: str,
    hotkey_name: str,
    device_index: int | None,
    device_label: str,
):
    if not OPENAI_API_KEY:
        log("ERROR: OPENAI_API_KEY not set.")
        return

    label_text = device_label or DEFAULT_DEVICE_LABEL
    record_buffer: list[np.ndarray] = []
    buffer_lock = threading.Lock()

    with state.lock:
        state.session_counter += 1
        session_id = state.session_counter
        state.active_session_id = session_id
        state.is_listening = True
        state.is_transcribing = False
        state.should_stop = False
        state.transcript_final = ""
        state.muted_warning = False
        state.last_audio_time = time.monotonic()
        state.last_audio_rms = 0.0
        state.mode = mode
        state.active_hotkey = hotkey_name
        state.active_device_label = label_text
    update_tray_status()

    with state.lock:
        toggle_mode = state.toggle_mode_enabled
    label = "Dictate" if mode == MODE_DICTATION else "Log"
    action = "Tap" if toggle_mode else "Hold"
    recorder = AudioRecorder(
        device_index=device_index, buffer=record_buffer, buffer_lock=buffer_lock
    )
    started, _active_index, active_label = start_recorder_with_fallback(
        recorder,
        label,
        label_text,
    )
    if not started:
        log(f"[Audio] {label} start aborted; no usable input device.")
        with state.lock:
            if state.active_session_id == session_id:
                state.is_listening = False
                state.active_hotkey = ""
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
            state.is_listening = False
            state.active_hotkey = ""
            state.active_device_label = ""
            state.should_stop = False
            state.muted_warning = False
    update_tray_status()

    with buffer_lock:
        chunks = [chunk.copy() for chunk in record_buffer]

    with state.lock:
        if state.active_session_id == session_id:
            state.is_transcribing = True
    update_tray_status()
    log("\n[Transcribing] Whisper request sent...")

    audio_duration_s = sum(len(chunk) for chunk in chunks) / SAMPLE_RATE if chunks else 0.0
    transcribe_start = time.perf_counter()
    final_text = apply_punctuation_options(transcribe_with_whisper(chunks))
    transcribe_end = time.perf_counter()

    transcription_ms = (transcribe_end - transcribe_start) * 1000.0
    audio_minutes = audio_duration_s / 60.0
    transcription_minutes = (transcribe_end - transcribe_start) / 60.0
    total_minutes = audio_minutes + transcription_minutes
    word_count = len(final_text.split())
    wpm = word_count / total_minutes if total_minutes > 0 else 0.0

    with state.lock:
        if state.active_session_id == session_id:
            state.transcript_final = final_text
            state.is_transcribing = False
    update_tray_status()

    log(
        f"[Metrics] Whisper {transcription_ms:.0f} ms | WPM {wpm:.1f} (words={word_count}, audio={audio_duration_s * 1000:.0f} ms)"
    )

    if final_text:
        if mode == MODE_WORKLOG:
            append_work_log_entry(final_text)
        else:
            log("\n[Final]:", final_text)
            if PASTE_ON_RELEASE:
                if paste_text(final_text):
                    log("[Pasted] Sent clipboard text to the active window.")
                else:
                    log("[Clipboard] Transcript copied, but paste was not sent.")
    else:
        log("\n(No speech captured.)")


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
        return (key.char or "").upper()
    try:
        name = key.name
    except AttributeError:
        return ""
    return name.upper() if name else ""


def on_press(key):
    key_name = get_key_name(key)
    if not key_name:
        return

    if key_name == "SPACE":
        handle_spacebar_press()
        return

    double_tap = False
    with state.lock:
        if state.is_listening:
            if state.toggle_mode_enabled and key_name == state.active_hotkey:
                state.should_stop = True
            return
        if key_name == HOTKEY_WORKLOG:
            now = time.monotonic()
            last_tap = state.last_worklog_tap_time
            state.worklog_press_time = now
            if last_tap and (now - last_tap) <= WORKLOG_DOUBLE_TAP_WINDOW_S:
                state.last_worklog_tap_time = 0.0
                state.worklog_double_tap_active = True
                double_tap = True

    if double_tap:
        open_work_log()
        return

    if key_name == HOTKEY_DICTATION:
        with state.lock:
            device_index = state.dictation_device_index
            device_label = state.dictation_device_label
        threading.Thread(
            target=start_listening,
            args=(MODE_DICTATION, HOTKEY_DICTATION, device_index, device_label),
            daemon=True,
        ).start()
    elif key_name == HOTKEY_WORKLOG:
        with state.lock:
            device_index = state.worklog_device_index
            device_label = state.worklog_device_label
        threading.Thread(
            target=start_listening,
            args=(MODE_WORKLOG, HOTKEY_WORKLOG, device_index, device_label),
            daemon=True,
        ).start()


def on_release(key):
    key_name = get_key_name(key)
    if not key_name:
        return

    now = time.monotonic()
    if key_name == HOTKEY_WORKLOG:
        with state.lock:
            if state.worklog_double_tap_active:
                state.worklog_double_tap_active = False
                state.worklog_press_time = 0.0
                return
            press_time = state.worklog_press_time
            state.worklog_press_time = 0.0
            if press_time and (now - press_time) <= WORKLOG_TAP_MAX_S:
                state.last_worklog_tap_time = now
            else:
                state.last_worklog_tap_time = 0.0

    with state.lock:
        if state.toggle_mode_enabled:
            return
        if state.is_listening and key_name == state.active_hotkey:
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
    color = TRAY_COLOR_LISTENING if is_listening else TRAY_COLOR_READY
    tray_icon.icon = create_tray_icon_image(color=color)


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
        state.punctuation_terminal = False
        state.punctuation_capitalize = False
        state.punctuation_normalize_spaces = False
    refresh_tray_menu()


def apply_bells_preset(_icon=None, _item=None) -> None:
    with state.lock:
        state.beeps_enabled = True
        state.tooltip_enabled = True
        state.monitor_enabled = True
    refresh_tray_menu()


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
        pystray.MenuItem("Default (no frills)", apply_default_preset),
        pystray.MenuItem("Bells and whistles", apply_bells_preset),
        pystray.MenuItem("Options", build_options_menu()),
        pystray.MenuItem("Punctuation", build_punctuation_menu()),
        pystray.MenuItem("Dictation input device", build_device_menu(MODE_DICTATION)),
        pystray.MenuItem("Work log input device", build_device_menu(MODE_WORKLOG)),
        pystray.MenuItem("Refresh audio devices", refresh_audio_devices),
        pystray.MenuItem("Open work log", open_work_log),
        pystray.MenuItem("Exit", tray_exit),
    )


def create_tray_icon_image(
    size: int = TRAY_ICON_SIZE,
    color: tuple[int, int, int, int] = TRAY_COLOR_READY,
) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, size, size), fill=color)
    inset = int(size * 0.28)
    draw.rectangle((inset, inset, size - inset, size - inset), fill=(255, 255, 255, 255))
    return image


def tray_setup(_icon: TrayIconLike) -> None:
    _icon.visible = True  # required when using a custom setup callback
    log(
        f"Push-to-talk ready. {HOTKEY_DICTATION} for dictation/paste, "
        f"{HOTKEY_WORKLOG} for work log."
    )
    refresh_tray_menu()
    start_keyboard_listener()


def tray_exit(icon: TrayIconLike, _item=None) -> None:
    shutdown_event.set()
    stop_keyboard_listener()
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
        stop_keyboard_listener()
        shutdown_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
