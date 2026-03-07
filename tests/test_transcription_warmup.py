from __future__ import annotations

import sys
import unittest
from pathlib import Path
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


class FakeModels:
    def __init__(self) -> None:
        self.calls = 0

    def list(self) -> None:
        self.calls += 1


class FakeClient:
    def __init__(self) -> None:
        self.models = FakeModels()

    def with_options(self, **kwargs):
        return self


class TranscriptionWarmupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_api_key = push_to_talk.OPENAI_API_KEY
        push_to_talk.OPENAI_API_KEY = "test-key"
        push_to_talk.transcription_warmup_started = push_to_talk.threading.Event()
        push_to_talk.transcription_warmup_finished = push_to_talk.threading.Event()
        ThreadRecorder.instances.clear()

    def tearDown(self) -> None:
        push_to_talk.OPENAI_API_KEY = self.original_api_key
        push_to_talk.transcription_warmup_started = push_to_talk.threading.Event()
        push_to_talk.transcription_warmup_finished = push_to_talk.threading.Event()
        ThreadRecorder.instances.clear()

    def test_start_transcription_warmup_only_launches_once(self) -> None:
        with patch.object(push_to_talk.threading, "Thread", ThreadRecorder):
            push_to_talk.start_transcription_warmup()
            push_to_talk.start_transcription_warmup()

        self.assertEqual(1, len(ThreadRecorder.instances))
        self.assertTrue(ThreadRecorder.instances[0].started)

    def test_prewarm_transcription_stack_marks_finished(self) -> None:
        fake_client = FakeClient()
        with (
            patch.object(push_to_talk, "get_openai_client", return_value=fake_client),
            patch.object(push_to_talk, "log"),
        ):
            push_to_talk._prewarm_transcription_stack()

        self.assertEqual(1, fake_client.models.calls)
        self.assertTrue(push_to_talk.transcription_warmup_finished.is_set())


if __name__ == "__main__":
    unittest.main()
