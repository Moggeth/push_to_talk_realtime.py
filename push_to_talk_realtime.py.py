#!/usr/bin/env python3
"""
Push-to-talk Whisper transcription (Windows):
- Hold F9 to start recording the microphone.
- Release F9 to transcribe with Whisper and paste the text into the active control.

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
from dataclasses import dataclass, field

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
HOTKEY = "F8"
PASTE_ON_RELEASE = True
CLIP_SUFFIX = ""             # e.g. " " to auto-space after paste

# -------------------- State --------------------

@dataclass
class SessionState:
    is_listening: bool = False
    should_stop: bool = False
    transcript_final: str = ""
    audio_buffer: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

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

# -------------------- Audio Capture --------------------

class AudioRecorder:
    def __init__(self):
        self.stream = None

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
            device=DEVICE_INDEX,
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

def start_listening():
    if not OPENAI_API_KEY:
        log("ERROR: OPENAI_API_KEY not set.")
        return

    with state.lock:
        state.is_listening = True
        state.should_stop = False
        state.transcript_final = ""
        state.audio_buffer.clear()

    log(f"\n[Listening] Hold {HOTKEY}...")
    recorder = AudioRecorder()
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

    log(f"[Metrics] Whisper {transcription_ms:.0f} ms | WPM {wpm:.1f} (words={word_count}, audio={audio_duration_s * 1000:.0f} ms)")

    if final_text:
        log("\n[Final]:", final_text)
        if PASTE_ON_RELEASE:
            paste_text(final_text)
            log("[Pasted] Sent clipboard text to the active window.")
    else:
        log("\n(No speech captured.)")

# -------------------- Hotkey handling --------------------

def on_press(key):
    try:
        if key.name.upper() == HOTKEY and not state.is_listening:
            threading.Thread(target=start_listening, daemon=True).start()
    except AttributeError:
        return

def on_release(key):
    try:
        if key.name.upper() == HOTKEY and state.is_listening:
            with state.lock:
                state.should_stop = True
    except AttributeError:
        return

# -------------------- Main --------------------

def main():
    log(f"Push-to-talk ready. Hold {HOTKEY} to dictate; release to paste.\n"
        "Press Ctrl+C to exit.")
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
