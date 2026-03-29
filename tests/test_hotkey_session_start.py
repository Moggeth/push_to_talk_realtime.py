from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import push_to_talk_realtime as push_to_talk  # noqa: E402


class ThreadRecorder:
    instances: ClassVar[list[ThreadRecorder]] = []

    def __init__(self, target=None, args=(), daemon=None, kwargs=None) -> None:
        self.target = target
        self.args = args
        self.daemon = daemon
        self.kwargs = kwargs or {}
        self.started = False
        type(self).instances.append(self)

    def start(self) -> None:
        self.started = True


class HotkeySessionStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_openai_key = push_to_talk.OPENAI_API_KEY
        with push_to_talk.state.lock:
            push_to_talk.state.is_listening = False
            push_to_talk.state.is_transcribing = False
            push_to_talk.state.session_start_pending = False
            push_to_talk.state.should_stop = False
            push_to_talk.state.toggle_mode_enabled = False
            push_to_talk.state.active_hotkey = ""
            push_to_talk.state.dictation_device_index = None
            push_to_talk.state.dictation_device_label = push_to_talk.DEFAULT_DEVICE_LABEL
            push_to_talk.state.worklog_device_index = None
            push_to_talk.state.worklog_device_label = push_to_talk.DEFAULT_DEVICE_LABEL
            push_to_talk.state.worklog_press_time = 0.0
            push_to_talk.state.last_worklog_tap_time = 0.0
            push_to_talk.state.worklog_double_tap_active = False
            push_to_talk.state.worklog_is_pressed = False
            push_to_talk.state.shift_keys_down.clear()

    def tearDown(self) -> None:
        push_to_talk.OPENAI_API_KEY = self.original_openai_key
        ThreadRecorder.instances.clear()
        with push_to_talk.state.lock:
            push_to_talk.state.is_listening = False
            push_to_talk.state.is_transcribing = False
            push_to_talk.state.session_start_pending = False
            push_to_talk.state.should_stop = False

    def test_repeated_dictation_keydown_only_queues_one_session(self) -> None:
        key = SimpleNamespace(name=push_to_talk.HOTKEY_DICTATION.lower())

        with patch.object(push_to_talk.threading, "Thread", ThreadRecorder):
            push_to_talk.on_press(key)
            push_to_talk.on_press(key)

        self.assertEqual(1, len(ThreadRecorder.instances))
        self.assertTrue(ThreadRecorder.instances[0].started)
        with push_to_talk.state.lock:
            self.assertTrue(push_to_talk.state.session_start_pending)

    def test_dictation_can_start_while_previous_audio_is_still_transcribing(self) -> None:
        key = SimpleNamespace(name=push_to_talk.HOTKEY_DICTATION.lower())
        with push_to_talk.state.lock:
            push_to_talk.state.is_transcribing = True
            push_to_talk.state.transcribing_session_count = 1

        with patch.object(push_to_talk.threading, "Thread", ThreadRecorder):
            push_to_talk.on_press(key)

        self.assertEqual(1, len(ThreadRecorder.instances))
        self.assertTrue(ThreadRecorder.instances[0].started)
        with push_to_talk.state.lock:
            self.assertTrue(push_to_talk.state.session_start_pending)

    def test_start_listening_clears_pending_flag_when_api_key_missing(self) -> None:
        push_to_talk.OPENAI_API_KEY = ""
        with push_to_talk.state.lock:
            push_to_talk.state.session_start_pending = True

        push_to_talk.start_listening(
            push_to_talk.MODE_DICTATION,
            push_to_talk.HOTKEY_DICTATION,
            None,
            push_to_talk.DEFAULT_DEVICE_LABEL,
        )

        with push_to_talk.state.lock:
            self.assertFalse(push_to_talk.state.session_start_pending)


if __name__ == "__main__":
    unittest.main()
