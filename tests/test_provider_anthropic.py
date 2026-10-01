"""Claude via the official SDK, with a fake client: no network, no paid requests."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import anthropic
import httpx2

import providers
from config import DEFAULT_CONFIG, _deep_merge

CFG = _deep_merge(DEFAULT_CONFIG, {'providers': {'anthropic': {'api_key': 'synthetic-key'}}})
ROOM_MESSAGES = [{'role': 'system', 'content': 'Room rules. Return JSON.'},
                 {'role': 'user', 'content': '{"topic":"t"}'}]


def message(text, stop_reason='end_turn'):
    blocks = [SimpleNamespace(type='thinking', thinking=''), SimpleNamespace(type='text', text=text)]
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def status_error(cls, status, text):
    response = httpx2.Response(status, request=httpx2.Request('POST', 'https://api.anthropic.com/v1/messages'))
    return cls(text, response=response, body=None)


class FakeClient:
    def __init__(self, results, models=()):
        self.results = list(results)
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)
        self.models = SimpleNamespace(list=lambda: [SimpleNamespace(id=m) for m in models])

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class AnthropicProviderTests(unittest.TestCase):
    def chat(self, client, model='claude-opus-5-5'):
        with patch.object(providers, '_anthropic_client', return_value=client) as make:
            result = providers.chat_completion('anthropic', CFG, model, ROOM_MESSAGES)
        return result, make

    def test_system_prompt_is_separate_and_text_blocks_are_joined(self):
        client = FakeClient([message('{"text":"Hello"}')])
        result, make = self.chat(client)
        self.assertEqual(result, (True, '{"text":"Hello"}'))
        request = client.calls[0]
        self.assertEqual(request['system'], 'Room rules. Return JSON.')
        self.assertEqual(request['messages'], [{'role': 'user', 'content': '{"topic":"t"}'}])
        self.assertEqual(request['max_tokens'], providers.ANTHROPIC_MAX_TOKENS)
        self.assertEqual(request['output_config'], {'effort': 'low'})
        self.assertNotIn('temperature', request)
        self.assertEqual(make.call_args.args[:2], ('synthetic-key', 'https://api.anthropic.com'))

    def test_models_without_effort_support_omit_it(self):
        client = FakeClient([message('{"text":"Hi"}')])
        self.chat(client, model='claude-haiku-4-5')
        self.assertNotIn('output_config', client.calls[0])

    def test_truncation_and_refusal_are_reported(self):
        self.assertEqual(self.chat(FakeClient([message('{"te', 'max_tokens')]))[0],
                         (False, 'Response truncated at token limit'))
        self.assertFalse(self.chat(FakeClient([message('', 'refusal')]))[0][0])

    def test_overload_is_retried_once_but_bad_key_is_not(self):
        client = FakeClient([status_error(anthropic.OverloadedError, 529, 'Overloaded'),
                             message('{"text":"Recovered"}')])
        with patch.object(providers.time, 'sleep'):
            self.assertEqual(self.chat(client)[0], (True, '{"text":"Recovered"}'))
        self.assertEqual(len(client.calls), 2)
        client = FakeClient([status_error(anthropic.AuthenticationError, 401, 'invalid x-api-key synthetic-key')])
        ok, text = self.chat(client)[0]
        self.assertFalse(ok)
        self.assertIn('401', text)
        self.assertNotIn('synthetic-key', text)
        self.assertEqual(len(client.calls), 1)

    def test_missing_key_never_builds_a_client(self):
        with patch.object(providers, '_anthropic_client') as make:
            result = providers.chat_completion('anthropic', DEFAULT_CONFIG, 'claude-opus-5-5', ROOM_MESSAGES)
        self.assertEqual(result, (False, 'No provider API key configured'))
        make.assert_not_called()

    def test_model_list_uses_the_models_api(self):
        client = FakeClient([], models=['claude-opus-5-5', 'claude-sonnet-5-5'])
        with patch.object(providers, '_anthropic_client', return_value=client):
            self.assertEqual(providers.list_models('anthropic', CFG),
                             (True, 'OK: 2 model(s)', ['claude-opus-5-5', 'claude-sonnet-5-5']))


if __name__ == '__main__':
    unittest.main()
