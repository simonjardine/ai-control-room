"""Dictation with a fake microphone and synthetic transcription responses; no audio device or paid calls."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import requests

import room_gui
import room_voice
from config import DEFAULT_CONFIG, _deep_merge


def http(status, body):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    return result


class VoiceTests(unittest.TestCase):
    def test_wav_round_trip_reports_duration(self):
        audio = room_voice.to_wav(b'\x00\x00' * 8000, 16000)
        self.assertEqual(audio[:4], b'RIFF')
        self.assertAlmostEqual(room_voice.wav_seconds(audio), 0.5)

    def test_transcription_without_openai_key_sends_nothing(self):
        with patch.object(room_voice, '_request') as send:
            with self.assertRaisesRegex(room_voice.VoiceError, 'OpenAI API key'):
                room_voice.transcribe(b'audio', DEFAULT_CONFIG)
        send.assert_not_called()

    def test_transcription_uploads_wav_and_returns_text(self):
        cfg = _deep_merge(DEFAULT_CONFIG, {'providers': {'openai': {'api_key': 'synthetic-key'}}})
        with patch.object(room_voice, '_request', return_value=http(200, {'text': ' Hello room '})) as send:
            self.assertEqual(room_voice.transcribe(b'audio', cfg), 'Hello room')
        args, kwargs = send.call_args
        self.assertEqual(args, ('POST', 'https://api.openai.com/v1/audio/transcriptions'))
        self.assertEqual(kwargs['data']['model'], room_voice.TRANSCRIBE_MODEL)
        self.assertEqual(kwargs['files']['file'][2], 'audio/wav')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer synthetic-key')

    def test_transcription_errors_never_echo_the_key(self):
        cfg = _deep_merge(DEFAULT_CONFIG, {'providers': {'openai': {'api_key': 'synthetic-key'}}})
        reply = http(401, {'error': {'message': 'Incorrect API key provided: synthetic-key'}})
        with patch.object(room_voice, '_request', return_value=reply):
            with self.assertRaises(room_voice.VoiceError) as caught:
                room_voice.transcribe(b'audio', cfg)
        self.assertIn('HTTP 401', str(caught.exception))
        self.assertNotIn('synthetic-key', str(caught.exception))


class FakeRecorder:
    def __init__(self):
        self.elapsed = 0.0

    def start(self):
        pass

    def stop(self):
        return room_voice.to_wav(b'\x00\x00' * 16000, 16000)


class VoiceGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = room_gui.RoomApp(data_root=self.temp.name)
        self.app.attributes('-alpha', 0)
        self.app.geometry('1180x800+10000+10000')
        self.app.update()

    def tearDown(self):
        if not self.app.closed:
            self.app.close()
        self.temp.cleanup()

    def test_mic_without_openai_key_explains_and_does_not_record(self):
        with patch.object(room_voice, 'Recorder') as recorder:
            self.app.mic_button.invoke()
        recorder.assert_not_called()
        self.assertEqual(self.app.mic_button.cget('text'), '● Mic')
        self.assertIsNone(self.app.recorder)
        self.assertIn('OpenAI API key', self.app.status.get())

    def test_mic_records_then_transcribes_into_the_draft(self):
        with patch.object(room_voice, 'has_key', return_value=True), \
             patch.object(room_voice, 'Recorder', FakeRecorder), \
             patch.object(room_voice, 'transcribe', return_value='spoken words') as transcribe:
            self.app.input.insert('1.0', 'Typed')
            self.app.mic_button.invoke()
            self.assertEqual(self.app.mic_button.cget('text'), '■ Stop')
            self.app.mic_button.invoke()
            until = time.monotonic() + 4
            while self.app.transcribing and time.monotonic() < until:
                self.app.update()
                time.sleep(.005)
        transcribe.assert_called_once()
        self.assertEqual(self.app.input.get('1.0', 'end-1c'), 'Typed spoken words')
        self.assertEqual(self.app.mic_button.cget('text'), '● Mic')
        self.assertFalse(self.app.conversation.entries)


if __name__ == '__main__':
    unittest.main()
