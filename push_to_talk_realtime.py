#!/usr/bin/env python3
"""
Push-to-talk Whisper transcription (Windows):
- Hold F8 to dictate and paste upon release.
- Hold F9 to capture audio and log the transcript as a timestamped work entry.

Notes
-----
- Captures 16 kHz mono PCM from the default input device (set DEVICE_INDEX if needed).
- Uses Ctrl+V to paste the final text. Run the console as Administrator if an app blocks paste.
- Requires OPENAI_API_KEY with Whisper access in the environment or a .env file.
"""

import os
import sys
import threading
import time
import signal
import platform
import ctypes
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

import numpy as np
import sounddevice as sd
from pynput import keyboard as pynput_keyboard
import pyperclip
import keyboard  # to send ctrl+v
from dotenv import load_dotenv

# -------------------- Configuration --------------------

load_dotenv()  # loads OPENAI_API_KEY from .env if present

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
WHISPER_MODEL = os.getenv("OPENAI_WHISPER_MODEL", "whisper-1")

# Audio capture
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_DUR_S = 0.04           # 40 ms per audio chunk
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_DUR_S)
DEVICE_INDEX = None          # set to an index from sd.query_devices() if needed

# Behavior
MODE_DICTATION = "dictation"
MODE_WORKLOG = "worklog"

HOTKEY_DICTATION = "F8"
HOTKEY_WORKLOG = "F9"
PASTE_ON_RELEASE = True
CLIP_SUFFIX = ""             # e.g. " " to auto-space after paste
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

# -------------------- State --------------------

@dataclass
class SessionState:
    is_listening: bool = False
    should_stop: bool = False
    transcript_final: str = ""
    audio_buffer: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    mode: str = MODE_DICTATION
    active_hotkey: str = ""
    active_device_label: str = ""
    dictation_device_index: Optional[int] = DEVICE_INDEX
    dictation_device_label: str = DEFAULT_DEVICE_LABEL
    worklog_device_index: Optional[int] = DEVICE_INDEX
    worklog_device_label: str = DEFAULT_DEVICE_LABEL
    worklog_default_device_index: Optional[int] = DEVICE_INDEX
    worklog_default_device_label: str = DEFAULT_DEVICE_LABEL
    worklog_uses_stereo_mix: bool = False
    stereo_mix_device_index: Optional[int] = None
    stereo_mix_device_label: str = ""

state = SessionState()

# -------------------- Utilities --------------------

def log(*a):
    print(*a, flush=True)

def paste_text(text: str):
    """Paste text into the active control using clipboard + Ctrl+V."""
    if not text:
        return
    pyperclip.copy(text + CLIP_SUFFIX)
    time.sleep(0.02)
    keyboard.press_and_release("ctrl+v")


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

def describe_device(index: Optional[int]) -> Tuple[str, bool]:
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


def lookup_input_device_by_name(search_term: str) -> Tuple[Optional[int], str]:
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
        hostapi_name = hostapi_names.get(hostapi_index, "") if isinstance(hostapi_index, int) else ""
        label = device.get("name", str(idx))
        if hostapi_name:
            label = f"{label} ({hostapi_name})"
        combined = " ".join(part for part in [device.get("name", ""), hostapi_name] if part)
        if term in combined.lower():
            return idx, label

    return None, ""


def resolve_device_descriptor(descriptor: str) -> Tuple[Optional[int], str, bool]:
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


def log_device_selection(role: str, index: Optional[int], label: str) -> None:
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
            log(f"[Audio] Dictation device '{dictation_descriptor}' not found; using {fallback_label}.")

    worklog_index = dictation_index
    worklog_label = dictation_label
    if worklog_descriptor:
        idx, label, ok = resolve_device_descriptor(worklog_descriptor)
        if ok:
            worklog_index, worklog_label = idx, label
        else:
            log(f"[Audio] Worklog device '{worklog_descriptor}' not found; using {dictation_label}.")

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


# -------------------- Audio Capture --------------------

initialize_device_state()

class AudioRecorder:
    def __init__(self, device_index: Optional[int]):
        self.stream = None
        self.device_index = device_index

    def _callback(self, indata, frames, time_info, status):
        if status:
            return
        pcm = np.clip(indata[:, 0], -1.0, 1.0)
        pcm_i16 = (pcm * 32767.0).astype(np.int16)
        with state.lock:
            state.audio_buffer.append(pcm_i16.copy())

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
    device_index: Optional[int],
    device_label: str,
):
    if not OPENAI_API_KEY:
        log("ERROR: OPENAI_API_KEY not set.")
        return

    label_text = device_label or DEFAULT_DEVICE_LABEL

    with state.lock:
        state.is_listening = True
        state.should_stop = False
        state.transcript_final = ""
        state.audio_buffer.clear()
        state.mode = mode
        state.active_hotkey = hotkey_name
        state.active_device_label = label_text

    label = "Dictate" if mode == MODE_DICTATION else "Log"
    log(f"\n[Listening-{label}] Hold {hotkey_name}... (device: {label_text})")
    recorder = AudioRecorder(device_index=device_index)
    recorder.start()

    try:
        while True:
            time.sleep(0.02)
            with state.lock:
                if state.should_stop:
                    break
    finally:
        recorder.stop()

    with state.lock:
        chunks = state.audio_buffer[:]

    log("\n[Transcribing] Whisper request sent...")

    audio_duration_s = sum(len(chunk) for chunk in chunks) / SAMPLE_RATE if chunks else 0.0
    transcribe_start = time.perf_counter()
    final_text = transcribe_with_whisper(chunks).strip()
    transcribe_end = time.perf_counter()

    transcription_ms = (transcribe_end - transcribe_start) * 1000.0
    audio_minutes = audio_duration_s / 60.0
    transcription_minutes = (transcribe_end - transcribe_start) / 60.0
    total_minutes = audio_minutes + transcription_minutes
    word_count = len(final_text.split())
    wpm = word_count / total_minutes if total_minutes > 0 else 0.0

    with state.lock:
        state.transcript_final = final_text
        state.is_listening = False
        state.active_hotkey = ""
        state.active_device_label = ""

    log(f"[Metrics] Whisper {transcription_ms:.0f} ms | WPM {wpm:.1f} (words={word_count}, audio={audio_duration_s * 1000:.0f} ms)")

    if final_text:
        if mode == MODE_WORKLOG:
            append_work_log_entry(final_text)
        else:
            log("\n[Final]:", final_text)
            if PASTE_ON_RELEASE:
                paste_text(final_text)
                log("[Pasted] Sent clipboard text to the active window.")
    else:
        log("\n(No speech captured.)")


# -------------------- Hotkey handling --------------------


def console_is_foreground() -> bool:
    if not IS_WINDOWS or USER32 is None or KERNEL32 is None:
        return True
    try:
        foreground = USER32.GetForegroundWindow()
        console = KERNEL32.GetConsoleWindow()
    except Exception:  # pylint: disable=broad-except
        return True
    return foreground != 0 and console != 0 and foreground == console


def toggle_worklog_stereo_mix() -> None:
    with state.lock:
        currently_stereo = state.worklog_uses_stereo_mix
        default_index = state.worklog_default_device_index
        default_label = state.worklog_default_device_label

    if currently_stereo:
        index_text = default_index if default_index is not None else "default"
        with state.lock:
            state.worklog_device_index = default_index
            state.worklog_device_label = default_label
            state.worklog_uses_stereo_mix = False
        log(f"[Worklog audio] Reverted to {default_label} (index={index_text}).")
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
    log(f"[Worklog audio] Stereo mix enabled -> {label} (index={idx}).")


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

    with state.lock:
        if state.is_listening:
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

    with state.lock:
        if state.is_listening and key_name == state.active_hotkey:
            state.should_stop = True

# -------------------- Main --------------------

def main():
    log(
        f"Push-to-talk ready. Hold {HOTKEY_DICTATION} to dictate/paste, "
        f"{HOTKEY_WORKLOG} to log work.\nPress Ctrl+C to exit."
    )
    listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        log("\nExiting...")
        os._exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
