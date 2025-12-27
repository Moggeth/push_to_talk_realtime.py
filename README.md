Push-to-talk Whisper (Windows)
==============================

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
- (Console only) Spacebar: toggle Stereo Mix for the work log input.

Tray Menu
---------
- Default (no frills): reset QoL options to the original behavior.
- Bells and whistles: enable beeps, status tooltip, and mute monitor.
- Options:
  - Toggle mode: tap the hotkey once to start, tap again to stop.
  - Beeps: sound on start/stop (Windows only).
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
Install dependencies (example):

```powershell
pip install openai sounddevice numpy pynput pyperclip keyboard pystray pillow python-dotenv
```

Create a `.env` file in the same folder:

```ini
OPENAI_API_KEY=your_key_here
```

Run:

```powershell
python .\push_to_talk_realtime.py
```

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
option.

Notes and tips
--------------
- If an app blocks paste, run the console as Administrator.
- The work log is a plain text file (one entry per line).
- Punctuation options affect both dictation and work log text content.
- Paste suffix options affect dictation paste only.
- Tray icon color: green when idle/transcribing, red while listening.
- VAD auto-stop and retry queues are not implemented yet.
