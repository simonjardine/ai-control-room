import json
import unittest
from unittest.mock import patch

import providers
import requests


def response(data, status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    result.headers.update(headers or {})
    return result


FINAL = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"text":"Ready"}'}}]}


class ProviderErrorTests(unittest.TestCase):
    def setUp(self):
        credentials = patch.object(providers, 'resolve_credentials',
                                   return_value=('test-private-key', 'https://example.invalid/v1', 'config'))
        credentials.start()
        self.addCleanup(credentials.stop)
        self.now = 100.0
        clock = patch.object(providers.time, 'monotonic', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        sleep = patch.object(providers.time, 'sleep', side_effect=self.advance)
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def advance(self, seconds):
        self.now += seconds

    def chat(self, provider='openrouter'):
        return providers.chat_completion(provider, {}, 'test-model',
                                         [{'role': 'user', 'content': 'Hello'}])

    def test_http_200_error_is_reported_instead_of_no_choices(self):
        error = response({'error': {'code': 503, 'message': 'Model unavailable'}})
        with patch.object(providers, '_request', return_value=error) as send:
            self.assertEqual(self.chat(), (False, 'Provider error (503): Model unavailable'))
            self.assertEqual(send.call_count, 2)

    def test_temporary_failure_retries_same_model_with_remaining_deadline(self):
        timeouts = []
        models = []

        def send(*args, **kwargs):
            timeouts.append(kwargs['timeout'])
            models.append(kwargs['json']['model'])
            self.advance(4)
            return response({'error': {'code': 503, 'message': 'Busy'}}) if len(timeouts) == 1 else response(FINAL)

        with patch.object(providers, '_request', side_effect=send):
            self.assertEqual(self.chat(), (True, '{"text":"Ready"}'))
        self.assertEqual(timeouts, [60, 55.5])
        self.assertEqual(models, ['test-model', 'test-model'])

    def test_missing_choices_retries_once_and_can_recover(self):
        with patch.object(providers, '_request', side_effect=[response({}), response(FINAL)]) as send:
            self.assertTrue(self.chat()[0])
            self.assertEqual(send.call_count, 2)

    def test_permanent_errors_do_not_retry_even_with_partial_choices(self):
        for code in (400, 401, 402, 403, 404):
            with self.subTest(code=code):
                data = dict(FINAL, error={'code': code, 'message': 'Request rejected'})
                with patch.object(providers, '_request', return_value=response(data)) as send:
                    ok, error = self.chat()
                    self.assertFalse(ok)
                    self.assertIn(str(code), error)
                    self.assertEqual(send.call_count, 1)

    def test_retry_after_cannot_extend_deadline(self):
        result = response({'error': {'code': 429, 'message': 'Rate limited'}},
                          headers={'Retry-After': '120'})
        with patch.object(providers, '_request', return_value=result) as send:
            self.assertFalse(self.chat()[0])
            self.assertEqual(send.call_count, 1)
        self.sleep.assert_not_called()

    def test_retry_after_http_date_is_respected(self):
        result = response({}, headers={'Retry-After': 'Wed, 01 Jan 2025 00:01:00 GMT'})
        with patch.object(providers.time, 'time', return_value=1735689600):
            self.assertEqual(providers._chat_retry_delay(result), 60)

    def test_exhausted_deadline_does_not_start_another_request(self):
        def send(*args, **kwargs):
            self.advance(60)
            return response({'error': {'code': 503, 'message': 'Busy'}})

        with patch.object(providers, '_request', side_effect=send) as request:
            self.assertFalse(self.chat()[0])
            self.assertEqual(request.call_count, 1)
        self.sleep.assert_not_called()

    def test_timeout_uses_selected_provider_limit_without_retry(self):
        for provider, limit in [('local', 90), ('openrouter', 60), ('kimi', 60), ('openai', 60), ('xai', 60)]:
            with self.subTest(provider=provider):
                with patch.object(providers, '_request', side_effect=requests.Timeout()) as send:
                    self.assertEqual(self.chat(provider), (False, f'Provider request exceeded {limit}s deadline'))
                    self.assertEqual(send.call_args.kwargs['timeout'], limit)
                    self.assertEqual(send.call_count, 1)

    def test_error_messages_redact_keys_and_omit_raw_metadata(self):
        result = response({'error': {'code': 401, 'message': 'Rejected test-private-key Bearer another-secret',
                                    'metadata': {'raw': 'private request headers'}}})
        with patch.object(providers, '_request', return_value=result):
            ok, error = self.chat()
        self.assertFalse(ok)
        for secret in ('test-private-key', 'another-secret', 'private request headers'):
            self.assertNotIn(secret, error)
        self.assertIn('[REDACTED]', error)

    def test_malformed_response_shapes_are_errors(self):
        values = [[], 'bad', {'choices': 'bad'}, {'choices': [None]},
                  {'choices': [{'message': 'bad'}]}, {'choices': [{'message': {'content': {}}}]}]
        for value in values:
            with self.subTest(value=value):
                with patch.object(providers, '_request', return_value=response(value)) as send:
                    self.assertFalse(self.chat()[0])
                    self.assertEqual(send.call_count, 1)

    def test_incomplete_output_is_never_accepted_as_a_reply(self):
        data = {'choices': [{'finish_reason': 'error', 'message': {'content': '{"text":"partial"}'}}]}
        with patch.object(providers, '_request', return_value=response(data)):
            self.assertEqual(self.chat(), (False, 'Provider stopped before completing the response'))


if __name__ == '__main__':
    unittest.main()
