"""Streaming replies with synthetic server-sent events; no network or paid calls."""
import json
from pathlib import Path
import random
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import providers
import room_gui
from config import DEFAULT_CONFIG, _deep_merge, save_config
from room import reply, visible_text

CFG = _deep_merge(DEFAULT_CONFIG, {'providers': {'openrouter': {'api_key': 'synthetic-key'},
                                                 'anthropic': {'api_key': 'synthetic-key'}}})
MESSAGES = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'hi'}]


def sse(*pieces, finish='stop'):
    lines = [b'data: ' + json.dumps({'choices': [{'delta': {'content': p}}]}).encode() for p in pieces]
    lines.append(b'data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': finish}]}).encode())
    lines.append(b'data: [DONE]')
    return lines


class FakeStreamResponse:
    def __init__(self, lines, status=200, content_type='text/event-stream', body=None, gate=None):
        self.lines, self.status_code, self.gate = lines, status, gate
        self.headers = {'Content-Type': content_type}
        self._body = body
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.closed = True

    def iter_lines(self):
        for number, line in enumerate(self.lines):
            if self.gate and number == 2:
                self.gate.wait(2)
            if self.closed:
                raise providers.requests.ConnectionError('closed')
            yield line

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body

    @property
    def text(self):
        return json.dumps(self._body)


class VisibleTextTests(unittest.TestCase):
    def test_text_grows_correctly_however_the_reply_is_split(self):
        final = 'Line one\nSaid "hi" — café ✓ 😀 \\ done'
        raw = json.dumps({'text': final})
        rng = random.Random(7)
        for _ in range(50):
            cuts = sorted(rng.sample(range(1, len(raw)), 8))
            pieces = [raw[a:b] for a, b in zip([0, *cuts], [*cuts, len(raw)])]
            shown, received = [], ''
            for piece in pieces:
                received += piece
                shown.append(visible_text(received))
            self.assertEqual(shown[-1], final)
            for earlier in shown:
                self.assertTrue(final.startswith(earlier), (earlier, final))

    def test_tool_calls_and_prose_show_nothing_until_text(self):
        self.assertEqual(visible_text('{"tool":"read_file","arguments":{"text":"x"}}'), '')
        self.assertEqual(visible_text('Sure, here: {"te'), '')
        self.assertEqual(visible_text('```json\n{"text": "Hel'), 'Hel')


class StreamChatTests(unittest.TestCase):
    def stream(self, response, cancelled=None):
        pieces = []
        with patch.object(providers.requests, 'post', return_value=response) as post:
            result = providers.chat_completion('openrouter', CFG, 'm', MESSAGES,
                                               on_delta=pieces.append, cancelled=cancelled)
        return result, pieces, post

    def test_server_sent_events_are_joined_and_forwarded(self):
        result, pieces, post = self.stream(FakeStreamResponse(sse('{"text":', '"Hel', 'lo"}')))
        self.assertEqual(result, (True, '{"text":"Hello"}'))
        self.assertEqual(pieces, ['{"text":', '"Hel', 'lo"}'])
        self.assertTrue(post.call_args.kwargs['json']['stream'])
        self.assertEqual(post.call_args.kwargs['timeout'], (15, providers.STREAM_IDLE))

    def test_truncation_and_mid_stream_errors_are_reported(self):
        self.assertEqual(self.stream(FakeStreamResponse(sse('{"text":"cut', finish='length')))[0],
                         (False, 'Response truncated at token limit'))
        error = [b'data: {"error":{"message":"upstream failed for synthetic-key"}}']
        ok, text = self.stream(FakeStreamResponse(error))[0]
        self.assertFalse(ok)
        self.assertNotIn('synthetic-key', text)

    def test_refused_stream_falls_back_to_a_normal_request(self):
        refused = FakeStreamResponse([], status=400, content_type='application/json',
                                     body={'error': {'message': 'stream not supported'}})
        normal = SimpleNamespace(status_code=200, headers={},
                                 json=lambda: {'choices': [{'message': {'content': '{"text":"ok"}'}}]})
        with patch.object(providers.requests, 'post', return_value=refused), \
             patch.object(providers, '_request', return_value=normal) as fallback:
            result = providers.chat_completion('openrouter', CFG, 'm', MESSAGES, on_delta=lambda p: None)
        self.assertEqual(result, (True, '{"text":"ok"}'))
        self.assertNotIn('stream', fallback.call_args.kwargs['json'])

    def test_pause_closes_the_stream_part_way(self):
        gate = threading.Event()
        paused = threading.Event()
        response = FakeStreamResponse(sse('{"text":"a', 'b', 'c', 'd"}'), gate=gate)
        threading.Timer(.3, paused.set).start()
        result, pieces, _ = self.stream(response, cancelled=paused.is_set)
        self.assertEqual(result, (False, providers.STOPPED))
        self.assertTrue(response.closed)
        self.assertEqual(pieces, ['{"text":"a', 'b'])


class FakeClaudeStream:
    def __init__(self, pieces, stop_reason='end_turn'):
        self.text_stream = iter(pieces)
        self.final = SimpleNamespace(stop_reason=stop_reason,
                                     content=[SimpleNamespace(type='text', text=''.join(pieces))])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def get_final_message(self):
        return self.final


class ClaudeStreamTests(unittest.TestCase):
    def test_claude_text_stream_is_forwarded(self):
        client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: FakeClaudeStream(['{"text":"Hi', '"}'])))
        pieces = []
        with patch.object(providers, '_anthropic_client', return_value=client):
            result = providers.chat_completion('anthropic', CFG, 'claude-opus-5-5', MESSAGES, on_delta=pieces.append)
        self.assertEqual(result, (True, '{"text":"Hi"}'))
        self.assertEqual(pieces, ['{"text":"Hi', '"}'])


class ReplyStreamingTests(unittest.TestCase):
    def test_tool_calls_reset_the_live_text(self):
        replies = iter(['{"tool":"list_directory","arguments":{}}', '{"text":"Done"}'])

        def model(provider, cfg, model, messages, on_delta=None, **kwargs):
            raw = next(replies)
            for ch in raw:
                on_delta(ch)
            return True, raw

        tools = SimpleNamespace(names=['list_directory'], instructions=lambda: '',
                                run=lambda name, args: {'success': True, 'output': ''})
        shown = []
        with patch('room.chat_completion', side_effect=model):
            result = reply({}, {'provider': 'local', 'model': 'm'}, MESSAGES, tools=tools, on_text=shown.append)
        self.assertEqual(result, (True, 'Done'))
        tool_call = shown[:41]  # The reset plus one update per character of the 40-character tool call.
        self.assertEqual(set(tool_call), {''})
        self.assertEqual(shown[41], '')  # The second model call starts with a reset.
        self.assertEqual(shown[-1], 'Done')


class LiveGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        cfg = _deep_merge(DEFAULT_CONFIG, {'room': {'participants': {'atlas': {'model': 'm'}}}})
        save_config(cfg, Path(self.temp.name) / 'config.json')
        self.app = room_gui.RoomApp(data_root=self.temp.name)
        self.app.attributes('-alpha', 0)
        self.app.geometry('1180x800+10000+10000')
        self.app.update()

    def tearDown(self):
        if not self.app.closed:
            self.app.close()
        self.temp.cleanup()

    def pump_until(self, predicate, seconds=5):
        until = time.monotonic() + seconds
        while not predicate() and time.monotonic() < until:
            self.app.update()
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_reply_streams_into_transcript_and_pause_keeps_the_partial_text(self):
        def model(provider, cfg, model, messages, on_delta=None, cancelled=None, **kwargs):
            for piece in ['{"text":"Streaming ', 'words ', 'arrive']:
                on_delta(piece)
                time.sleep(.2)
            while not cancelled():
                time.sleep(.01)
            return False, providers.STOPPED

        with patch('room.chat_completion', side_effect=model):
            self.app.input.insert('1.0', 'Go')
            self.app.send()
            self.pump_until(lambda: 'Streaming words' in self.app.transcript.get('1.0', 'end'))
            self.assertIn('writing…', self.app.transcript.get('1.0', 'end'))
            self.app.pause()
            self.pump_until(lambda: not self.app.busy)
        last = self.app.conversation.entries[-1]
        self.assertEqual(last['text'], 'Streaming words arrive\n[Stopped by host]')
        self.assertFalse(last['error'])
        self.assertNotIn('writing…', self.app.transcript.get('1.0', 'end'))


if __name__ == '__main__':
    unittest.main()
