"""Reasoning-capable endpoints must be allowed to produce a final chat reply."""
import json
import unittest
from unittest.mock import patch

import providers
import requests


def response(status, message, finish_reason='stop'):
    result = requests.Response()
    result.status_code = status
    data = ({'choices': [{'message': message, 'finish_reason': finish_reason}]}
            if status == 200 else {'error': {'message': message}})
    result._content = json.dumps(data).encode()
    return result


class ReasoningCompatibilityTests(unittest.TestCase):
    def request(self, provider, http_response):
        with patch.object(providers, 'resolve_credentials',
                          return_value=('test-key', 'https://example.invalid/v1', 'config')):
            with patch.object(providers, '_request', side_effect=http_response) as send:
                result = providers.chat_completion(
                    provider, {}, 'stealth/space-bunny-alpha',
                    [{'role': 'user', 'content': 'Hello'}], max_tokens=4096)
                return result, send

    def test_mandatory_reasoning_endpoint_accepts_default_mode(self):
        def mandatory_endpoint(method, url, **kwargs):
            body = kwargs['json']
            reasoning = body.get('reasoning', {})
            disabled = (reasoning.get('enabled') is False
                        or reasoning.get('effort') == 'none'
                        or body.get('thinking', {}).get('type') == 'disabled'
                        or body.get('chat_template_kwargs', {}).get('enable_thinking') is False)
            if disabled:
                return response(400, 'Reasoning is mandatory for this endpoint and cannot be disabled.')
            return response(200, {'content': '{"text":"Hello Simon"}',
                                  'reasoning': 'Provider-only reasoning'})

        for provider in ('openrouter', 'kimi', 'local'):
            with self.subTest(provider=provider):
                result, send = self.request(provider, mandatory_endpoint)
                self.assertEqual(result, (True, '{"text":"Hello Simon"}'))
                self.assertEqual(send.call_count, 1)
                self.assertAlmostEqual(send.call_args.kwargs['timeout'],
                                       90 if provider == 'local' else 60, delta=0.1)

    def test_reasoning_alone_is_not_substituted_for_final_answer(self):
        result, _ = self.request('openrouter', lambda *a, **k: response(
            200, {'content': None, 'reasoning': 'Not a final response'}))
        self.assertEqual(result, (False, 'Empty final response'))

    def test_exhausted_reasoning_budget_reports_truncation(self):
        result, _ = self.request('openrouter', lambda *a, **k: response(
            200, {'content': '', 'reasoning': 'Incomplete'}, 'length'))
        self.assertEqual(result, (False, 'Response truncated at token limit'))


if __name__ == '__main__':
    unittest.main()
