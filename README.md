Push-to-talk Transcription (Windows)
====================================

Hold a hotkey to record from your microphone, transcribe audio, and paste
the transcript into the active window. A second hotkey logs dictations as
timestamped work entries. The app runs from a system tray icon with submenus
for input device selection, punctuation rules, and QoL toggles.

Features
--------
- Push-to-talk dictation: record and paste on release.
- Shift-modified dictation: hold `Shift` while pressing the dictation hotkey to capture system audio instead of the microphone.
- Work log capture: record and append a timestamped entry to `work_log.txt`.
- Transcription engine toggle: choose Whisper or GPT-4o Realtime from the tray menu.
- Realtime live dictation: GPT-4o Realtime streams server-side transcript deltas while you are still holding the hotkey.
- Engine preference persistence: selected transcription engine is saved and restored on next launch.
- Busy tray feedback: tray icon shows a spinner while transcription is in progress.
- Tray menu controls:
  - Select dictation/worklog input devices (including Stereo Mix).
  - Toggle beeps, status tooltip, tap-to-toggle mode, and mute monitor.
  - Punctuation options and paste suffix selection.
  - Presets: "Default (no frills)" and "Bells and whistles".

Hotkeys
-------
- Dictation: `F13` by default
- System audio dictation: hold `Shift + F13` to capture the currently playing system audio input (defaults to a Stereo Mix-style device when available).
- Work log: `F14` by default
- Work log: hold `F14` (more than ~0.25s) to record, or double-tap `F14` to open `work_log.txt`.
- Recommended Windows setup: map your mouse side button to `F13` in your mouse software. It is usually much less collision-prone than `F8`.
- Override hotkeys with `DICTATION_HOTKEY` / `WORKLOG_HOTKEY`, or set `dictation_hotkey` / `worklog_hotkey` in `settings.json`.
- (Windows console only) Spacebar: toggle Stereo Mix for the work log input.

Tray Menu
---------
- Default (no frills): reset QoL options to the original behavior.
- Bells and whistles: enable beeps, status tooltip, and mute monitor.
- Options:
  - Toggle mode: tap the hotkey once to start, tap again to stop.
  - Beeps: playful two-tone sound on start/stop (Windows only).
  - Status tooltip: shows Ready/Listening/Transcribing + device + mute hint.
  - Mute monitor: warns if you start recording but the mic stays silent.
- Transcription engine:
  - Whisper: transcribes after key release.
  - GPT-4o Realtime: strict server-side websocket transcription with server VAD; streams deltas while recording, then finalizes on release.
- Punctuation:
  - Suffix: None / Space / Newline (affects pasted dictation only).
  - Ensure terminal punctuation (adds "." if missing; enabled by default).
  - Capitalize first letter.
  - Normalize whitespace.
- Dictation input device: choose an input device (per-mode).
- Work log input device: choose an input device (per-mode) + toggle Stereo Mix.
- Refresh audio devices.
- Open work log.
- Exit.

Setup
-----
Install dependencies from `requirements.txt`:

```powershell
python -m pip install -r requirements.txt
```

Ubuntu install commands (including system packages needed by audio/tray/clipboard libs):

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip portaudio19-dev libportaudio2 libasound2-dev python3-gi gir1.2-ayatanaappindicator3-0.1 python3-xlib xclip
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create a `.env` file in the same folder:

```ini
OPENAI_API_KEY=your_key_here
```

Run:

```powershell
python .\push_to_talk_realtime.py
```

Development
-----------
Basic checks (see `TESTING.md` for the full, plain-language plan):

```powershell
ruff check .
ruff format --check .
pytest -vv
```

Coverage gate used by CI:

```powershell
pytest -vv --cov=push_to_talk_realtime --cov=platform_input --cov=text_processing --cov-report=term-missing
```

GitHub Actions now runs:
- A Linux quality job on Python 3.12 with Ruff plus a 70% coverage gate.
- Compatibility test jobs on Ubuntu, Windows, and macOS using Python 3.11.

The test suite is headless-safe in CI by forcing dummy `pynput` and `pystray`
backends, so the regression checks do not depend on a live tray session or
audio hardware.

Configuration
-------------
Environment variables:

- `OPENAI_API_KEY` (required): OpenAI API key with speech-to-text access.
- `OPENAI_WHISPER_MODEL` (optional): defaults to `whisper-1`.
- `OPENAI_WHISPER_PROMPT` (optional): punctuation/style hint for Whisper.
- `TRANSCRIPTION_ENGINE` (optional): `whisper` (default) or `gpt4o_realtime`.
- `OPENAI_REALTIME_TRANSCRIBE_MODEL` (optional): defaults to `gpt-4o-transcribe`.
- `OPENAI_REALTIME_SESSION_MODEL` (optional): preferred realtime transcription model override (for example `gpt-4o-transcribe`).
- `OPENAI_REALTIME_TRANSCRIBE_LANGUAGE` (optional): ISO-639-1 language hint.
- `OPENAI_REALTIME_TRANSCRIBE_PROMPT` (optional): transcription prompt for realtime mode.
- `OPENAI_REALTIME_WS_URL` (optional): websocket URL for realtime transcription, default `wss://api.openai.com/v1/realtime?intent=transcription`.
- `OPENAI_REALTIME_WS_USE_BETA_HEADER` (optional): `0` (default) uses GA websocket headers; set to `1` only if you intentionally need legacy beta header behavior.
- `REALTIME_LIVE_TYPING` (optional): `1` (default) enables live delta typing for dictation, `0` disables it.
- `REALTIME_SERVER_VAD_THRESHOLD` (optional): server VAD threshold, default `0.5`.
- `REALTIME_SERVER_VAD_PREFIX_MS` (optional): server VAD prefix padding in ms, default `300`.
- `REALTIME_SERVER_VAD_SILENCE_MS` (optional): server VAD silence duration in ms, default `700`.
- `PUSH_TO_TALK_SETTINGS_PATH` (optional): override path for persisted tray settings (`settings.json` by default).
- `DICTATION_HOTKEY` (optional): single-key trigger for dictation, default `F13`.
- `WORKLOG_HOTKEY` (optional): single-key trigger for work log capture, default `F14`.
- `DICTATION_DEVICE` (optional): device index or name fragment for dictation.
- `WORKLOG_DEVICE` (optional): device index or name fragment for work log.
- `SYSTEM_AUDIO_DEVICE` (optional): device index or name fragment used by `Shift + DICTATION_HOTKEY`; if unset, the app searches for `STEREO_MIX_SEARCH`.
- `WORK_LOG_PATH` (optional): custom path for `work_log.txt`.
- `STEREO_MIX_SEARCH` (optional): name fragment for Stereo Mix device search.
- `MUTE_RMS_THRESHOLD` (optional): RMS threshold for mute monitor, default `0.01`.
- `MUTE_WARNING_AFTER_S` (optional): seconds before mute warning, default `1.5`.

Device selection
----------------
Use the tray menu to switch input devices on the fly. For scripted setup, set
`DICTATION_DEVICE` or `WORKLOG_DEVICE` to a device index or a partial name
match (case-insensitive). `Shift + DICTATION_HOTKEY` uses `SYSTEM_AUDIO_DEVICE`
when configured, otherwise it searches for the `STEREO_MIX_SEARCH` input. The
menu also includes a "Refresh audio devices" option. If a selected device
disappears (for example, USB unplug/replug), the app retries and falls back to
another available input instead of crashing. Reselect the desired device after
it reconnects.

Notes and tips
--------------
- Paste uses the standard shortcut for your platform: `Ctrl+V` on Windows/Linux
  and `Cmd+V` on macOS.
- If you bind a mouse button through Logitech/G Hub, Razer Synapse, X-Mouse, or similar software, prefer `F13`-`F24`; those keys are usually unused by other apps.
- System audio capture depends on a loopback-capable input device. On many Windows systems that is exposed as `Stereo Mix`; if yours uses a different name, set `SYSTEM_AUDIO_DEVICE`.
- On Linux, paste injection does not require the root-only `keyboard` package.
  If simulated paste fails, the transcript still remains on the clipboard.
- The global spacebar shortcut for toggling Stereo Mix is disabled on Linux and
  macOS to avoid intercepting normal typing in other apps. Use the tray menu
  instead.
- If a target app blocks simulated paste, trigger paste manually from the
  clipboard or try running the console with elevated permissions on Windows.
- The work log is a plain text file (one entry per line).
- Punctuation options affect both dictation and work log text content.
- Paste suffix options affect dictation paste only.
- Tray icon color: green when idle, red while listening, and orange + spinner while transcribing.
- While transcribing, new recordings are ignored until the current transcript finishes.
- Realtime live typing applies only to dictation mode and may need app focus to stay in the target field.
- GPT-4o Realtime is hard-switched to server-side mode (no local chunking and no Whisper fallback inside realtime mode).
- If realtime dependencies are missing, GPT-4o Realtime selection logs an install hint and stays on Whisper.
- VAD auto-stop and retry queues are not implemented yet.
