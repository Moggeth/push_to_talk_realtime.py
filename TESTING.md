# Testing (Plain Language)

This project has quick automated checks plus a manual checklist for the audio
and tray behavior. The automated checks are meant to be fast and verbose.

## Automated checks (run these every time)

1) Lint (Ruff)

```
ruff check .
```

Expected result:
- If clean: a short "All checks passed" message.
- If not: a list of rule codes + file/line so you can fix them.

2) Format check (Ruff)

```
ruff format --check .
```

Expected result:
- If formatted: "All files are formatted".
- If not: file names + a diff of what Ruff would change.

3) Unit tests (Pytest, verbose)

```
pytest -vv
```

Expected result:
- Each test name shows up as `PASSED`.
- Final summary looks like `77 passed in X.XXs` (or higher as coverage grows).

4) Coverage gate (matches the Linux CI quality job)

```
pytest -vv --cov=push_to_talk_realtime --cov=platform_input --cov=text_processing --cov-report=term-missing
```

Expected result:
- A coverage table is printed.
- Total coverage stays at or above `70%`.

CI now runs:
- A Linux quality job on Python 3.12 for Ruff + the coverage gate.
- Compatibility pytest jobs on Ubuntu, Windows, and macOS using Python 3.11.
- Dummy `pynput`/`pystray` backends so headless CI can still import and test the app logic.

## Unit tests included (what they validate)

- App orchestration and state helpers:
  - Device descriptor resolution, fallback device picking, and device list refresh.
  - Clipboard paste flow, work-log append behavior, and tray status updates.
  - Keyboard and mouse hotkey press/release transitions, double-tap work-log handling, and toggle mode stop behavior.
  - Persisted dictation hotkey kind/tokens, default-on transcript history, startup toggle helpers, hotkey capture helper parsing, tray restart/quit actions, menu builders, tray startup/shutdown, and `main()` bootstrap wiring.

- `test_apply_punctuation_options_normalize_capitalize_terminal`
  - Input: `"  hello   world  "`
  - Expected: `"Hello world."`
  - Logic: whitespace normalized, first letter capitalized, terminal period added.

- `test_apply_punctuation_options_trims_spaces_before_newline`
  - Input: `"First sentence. \nSecond sentence."`
  - Expected: `"First sentence.\nSecond sentence."`
  - Logic: no trailing space before the line break.

- `test_apply_punctuation_options_keeps_terminal_punct`
  - Input: `"Already!"`
  - Expected: `"Already!"`
  - Logic: do not add an extra period.

- `test_apply_punctuation_options_empty_input`
  - Input: `""`
  - Expected: `""`
  - Logic: empty input short-circuits cleanly.

- `test_prepare_clipboard_text_suffix_space_only_at_end`
  - Input: `"First sentence. \nSecond sentence."`
  - Expected: `"First sentence.\nSecond sentence. "`
  - Logic: only one trailing space, at the very end.

- `test_prepare_clipboard_text_terminal_punctuation_with_space_suffix`
  - Input: `"first sentence second sentence"`
  - Expected: `"first sentence second sentence. "`
  - Logic: enforce terminal punctuation and keep exactly one trailing space.

- `test_prepare_clipboard_text_suffix_newline`
  - Input: `"Hello"`
  - Expected: `"Hello\n"`
  - Logic: newline suffix appends once.

- `test_prepare_clipboard_text_suffix_none`
  - Input: `"Hello"`
  - Expected: `"Hello"`
  - Logic: no suffix added.

- `test_prepare_clipboard_text_empty_input`
  - Input: `"   "`
  - Expected: `""`
  - Logic: empty/whitespace input yields empty output.

- `test_get_paste_modifier_uses_ctrl_on_linux`
  - Input: `"Linux"`
  - Expected: `Key.ctrl`
  - Logic: Linux paste uses `Ctrl+V`, without the root-only `keyboard` package.

- `test_supports_foreground_console_detection_only_on_windows`
  - Input: `"Windows"`, `"Linux"`, `"Darwin"`
  - Expected: `True`, `False`, `False`
  - Logic: the global spacebar shortcut only runs where console foreground
    detection is actually supported.

- `test_get_paste_modifier_uses_ctrl_on_windows`
  - Input: `"Windows"`
  - Expected: `Key.ctrl`
  - Logic: Windows paste keeps using `Ctrl+V`.

- `test_get_paste_modifier_uses_cmd_on_macos`
  - Input: `"Darwin"`
  - Expected: `Key.cmd`
  - Logic: macOS paste uses `Cmd+V`.

- `test_send_paste_shortcut_presses_modifier_then_v`
  - Input: fake controller
  - Expected: modifier pressed, `v` pressed/released, modifier released
  - Logic: the app sends one paste chord in the right order.

## Manual tests (audio + tray behavior)

1) Startup
- Run: `python .\push_to_talk_realtime.py`
- Expected: a tray icon appears and the console prints the ready message.

2) Dictation hotkey (Caps Lock by default)
- Hold Caps Lock, speak one sentence, release.
- Expected: beep on start/stop (if enabled), a transcript appears, and the text
  pastes into the active app.
- While transcription is processing after key release, the tray icon shows a spinning
  indicator until transcription completes.

3) Set Hotkey dialog
- Open the tray menu, click `Set Hotkey...`, press a key or combo, confirm the drafted label looks right, then click `Accept`.
- Expected: the tray menu immediately shows the new dictation hotkey.
- Expected: the hotkey only starts dictation when the drafted keys are the only keys being held, except for `Shift + hotkey` system-audio capture.

4) Realtime live typing (GPT-4o Realtime)
- In tray menu, set "Transcription engine" -> "GPT-4o Realtime".
- Hold Caps Lock and speak 1-2 sentences.
- Expected: text starts appearing before key release; releasing Caps Lock finalizes punctuation/suffix.
- Expected: realtime behavior is server-side; no local chunking fallback should appear in logs.
- Expected: no `invalid_model` websocket errors when using the default realtime websocket URL.

5) Shared input device menu
- Open the tray menu, choose `Input device`, then select a different microphone/input.
- Expected: both normal dictation and work-log capture switch to the same selected device.

6) System audio dictation
- Start playing a video or song.
- Hold `Shift + Caps Lock` while the audio is playing, then release.
- Expected: the app captures the system audio input instead of the microphone and transcribes that audio on release.
- If your machine does not expose a device literally named "Stereo Mix", set `SYSTEM_AUDIO_DEVICE` to the correct input name first.

7) Linux non-root paste path
- Run the app as a regular user on Linux, hold Caps Lock, speak a short phrase, release.
- Expected: no `ImportError: You must be root to use this library on linux.`
- If the target app does not accept simulated paste, expected fallback: the app
  logs that the transcript stayed on the clipboard instead of crashing.

8) Multi-sentence spacing (your request)
- Hold Caps Lock, speak 3-5 sentences with clear full stops, release.
- Expected: the pasted text has normal spacing between sentences, and only a
  single trailing space at the very end (no extra spaces at line breaks).

9) Punctuation toggles
- Toggle "Ensure terminal punctuation", "Capitalize first letter", and
  "Normalize whitespace" from the tray menu.
- Expected: dictation + work log reflect the settings on the next run.

11) Transcription engine toggle
- In tray menu, switch "Transcription engine" between Whisper and GPT-4o Realtime.
- Expected: selected radio item updates immediately and next dictation uses that engine.
- Expected: when GPT-4o Realtime is active, there is no automatic Whisper fallback.

12) Engine preference persistence
- Set engine to GPT-4o Realtime, exit app, relaunch app.
- Expected: tray still shows GPT-4o Realtime selected.

13) Dictation hotkey persistence
- Use `Set Hotkey...` to save a new dictation key or combo, exit the app, relaunch it.
- Expected: the tray still shows the saved dictation hotkey and it works without reconfiguration.

14) Transcript history (default on)
- Dictate once with the normal dictation hotkey, then open the history file from the tray.
- Expected: a new line is appended with a full date/time stamp and a `[Dictation]` tag.
- In `Options`, toggle `Save transcript history` off, dictate again, and confirm no new dictation history line is added.

15) Work log hotkey (F14 by default)
- Hold F14 for at least ~0.25s, speak a short sentence, release.
- Expected: a new timestamped line appears in `work_log.txt` with a `[Work log]` tag.

16) Mute monitor (optional)
- Enable "Mute monitor", then start recording while silent.
- Expected: after ~1.5s, a "Muted?" hint appears in the console/tray tooltip.

17) Device hot-swap fallback
- While the app is running, unplug and replug the USB mic, then press Caps Lock.
- Expected: no crash; the console logs a retry/fallback message and recording
  continues or exits cleanly if no input device is available.

18) Work log double-tap
- Double-tap F14 while idle.
- Expected: `work_log.txt` opens and no new recording starts.

19) Overlap while transcribing
- Dictate once with Caps Lock, release, then press Caps Lock again before the first transcript finishes.
- Expected: the second recording starts immediately.
- Expected: both transcripts still appear, and they paste in the order the recordings were made.

20) Run on startup toggle
- Open `Options` -> `Run on startup`.
- Expected on Linux: a user systemd service is written/enabled for the current checkout and starts immediately.
- Expected on Windows/macOS: the platform startup artifact is created for the current checkout.
- Toggle it off again.
- Expected: the startup artifact is disabled or removed cleanly.

21) Tray restart and quit actions
- If running under the included user systemd service on Linux, click `Restart` and `Quit` from the tray.
- Expected: `Restart` restarts the service cleanly and the tray returns.
- Expected: `Quit` stops the service.
- If running the script directly instead of under systemd, `Restart` should relaunch the script and `Quit` should only close the current process.

22) Starter script
- Run `python start_push_to_talk.py`.
- Expected: the tray app starts exactly like running the main module directly.

23) Mouse-side-button remap
- Map a spare mouse button to `F13`, relaunch the app, then hold that button and speak.
- Expected: dictation starts/stops cleanly and other apps no longer react as if `F8` was pressed.

24) Ubuntu tray interactivity
- On Ubuntu GNOME/Wayland, launch the app from `python start_push_to_talk.py`.
- Left-click or right-click the tray icon.
- Expected: the tray menu opens and actions such as `Set Hotkey...`, `Restart`, and `Quit` are clickable.
- Expected: there are no preset entries such as `Default (no frills)` or `Bells and whistles`; individual toggles remain under `Options`.
