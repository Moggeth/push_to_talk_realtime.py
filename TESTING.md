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
- Final summary looks like `7 passed in X.XXs`.

CI runs the same three commands on every push and pull request.

## Unit tests included (what they validate)

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

- `test_prepare_clipboard_text_suffix_space_only_at_end`
  - Input: `"First sentence. \nSecond sentence."`
  - Expected: `"First sentence.\nSecond sentence. "`
  - Logic: only one trailing space, at the very end.

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

## Manual tests (audio + tray behavior)

1) Startup
- Run: `python .\push_to_talk_realtime.py`
- Expected: a tray icon appears and the console prints the ready message.

2) Dictation hotkey (F8)
- Hold F8, speak one sentence, release.
- Expected: beep on start/stop (if enabled), a transcript appears, and the text
  pastes into the active app.

3) Multi-sentence spacing (your request)
- Hold F8, speak 3-5 sentences with clear full stops, release.
- Expected: the pasted text has normal spacing between sentences, and only a
  single trailing space at the very end (no extra spaces at line breaks).

4) Punctuation toggles
- Toggle "Ensure terminal punctuation", "Capitalize first letter", and
  "Normalize whitespace" from the tray menu.
- Expected: dictation + work log reflect the settings on the next run.

5) Work log hotkey (F9)
- Hold F9, speak a short sentence, release.
- Expected: a new timestamped line appears in `work_log.txt`.

6) Mute monitor (optional)
- Enable "Mute monitor", then start recording while silent.
- Expected: after ~1.5s, a "Muted?" hint appears in the console/tray tooltip.

7) Device hot-swap fallback
- While the app is running, unplug and replug the USB mic, then press F8.
- Expected: no crash; the console logs a retry/fallback message and recording
  continues or exits cleanly if no input device is available.

8) Work log double-tap
- Double-tap F9 while idle.
- Expected: `work_log.txt` opens and no new recording starts.
