"""Model discovery retains API results while making current models visible."""
import unittest
from unittest.mock import Mock, patch

import providers


class ModelCatalogTests(unittest.TestCase):
    def test_openai_refresh_orders_versions_and_keeps_every_model(self):
        ids = ['gpt-image-2', 'gpt-6-sol', 'gpt-6.1-sol-2026-09-29',
               'o3', 'gpt-5', 'gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-luna',
               'gpt-6.9-sol', 'gpt-6.10-sol', 'gpt-4o', 'space-bunny-alpha']
        response = Mock(status_code=200)
        response.json.return_value = {'data': [{'id': mid} for mid in ids]}
        cfg = {'providers': {'openai': {'api_key': 'synthetic-key'}}}
        with patch.object(providers, '_request', return_value=response) as request:
            ok, _, models = providers.list_models('openai', cfg)
        self.assertTrue(ok)
        self.assertEqual(models[:4], ['gpt-6.10-sol', 'gpt-6.9-sol',
                                     'gpt-6.1-sol', 'gpt-6.1-sol-2026-09-29'])
        self.assertEqual(set(models), set(ids))
        self.assertEqual(len(models), len(ids))
        request.assert_called_once()
        self.assertEqual(request.call_args.args[:2],
                         ('GET', 'https://api.openai.com/v1/models'))

    def test_other_provider_order_is_preserved(self):
        ids = ['z-model', 'a-model']
        self.assertEqual(providers._prefer_sort('local', ids), ids)
        self.assertEqual(providers._prefer_sort('xai', ids), ids)


if __name__ == '__main__':
    unittest.main()
