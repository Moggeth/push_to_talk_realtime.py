Push-to-talk Whisper
====================

Hold a hotkey to record from your microphone, send audio to Whisper, and paste
the transcript into the active window. A second hotkey logs dictations as
timestamped work entries. The app runs from a system tray icon with submenus
for input device selection, punctuation rules, and QoL toggles.

Features
--------
- Push-to-talk dictation: record and paste on release.
- Work log capture: record and append a timestamped entry to `work_log.txt`.
- Tray menu controls:
  - Select dictation/worklog input devices (including Stereo Mix).
  - Toggle beeps, status tooltip, tap-to-toggle mode, and mute monitor.
  - Punctuation options and paste suffix selection.
  - Presets: "Default (no frills)" and "Bells and whistles".

Hotkeys
-------
- Dictation: `F8`
- Work log: `F9`
- Work log: double-tap `F9` to open `work_log.txt`.
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
- Punctuation:
  - Suffix: None / Space / Newline (affects pasted dictation only).
  - Ensure terminal punctuation (adds "." if missing).
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

- `OPENAI_API_KEY` (required): OpenAI API key with Whisper access.
- `OPENAI_WHISPER_MODEL` (optional): defaults to `whisper-1`.
- `DICTATION_DEVICE` (optional): device index or name fragment for dictation.
- `WORKLOG_DEVICE` (optional): device index or name fragment for work log.
- `WORK_LOG_PATH` (optional): custom path for `work_log.txt`.
- `STEREO_MIX_SEARCH` (optional): name fragment for Stereo Mix device search.
- `MUTE_RMS_THRESHOLD` (optional): RMS threshold for mute monitor, default `0.01`.
- `MUTE_WARNING_AFTER_S` (optional): seconds before mute warning, default `1.5`.

Device selection
----------------
Use the tray menu to switch input devices on the fly. For scripted setup, set
`DICTATION_DEVICE` or `WORKLOG_DEVICE` to a device index or a partial name
match (case-insensitive). The menu also includes a "Refresh audio devices"
option. If a selected device disappears (for example, USB unplug/replug), the
app retries and falls back to another available input instead of crashing.
Reselect the desired device after it reconnects.

Notes and tips
--------------
- Paste uses the standard shortcut for your platform: `Ctrl+V` on Windows/Linux
  and `Cmd+V` on macOS.
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
- Tray icon color: green when idle/transcribing, red while listening.
- VAD auto-stop and retry queues are not implemented yet.
