import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import config
import providers
from room import room_specs


class ConfigTests(unittest.TestCase):
    def test_fresh_config_has_no_credentials_or_model_assignments(self):
        with tempfile.TemporaryDirectory() as folder:
            value = config.load_config(Path(folder) / 'config.json')
        self.assertTrue(all(not p['api_key'] for p in value['providers'].values()))
        self.assertTrue(all(not p['model'] for p in room_specs(value).values()))
        self.assertEqual(value['providers']['local']['base_url'], 'http://127.0.0.1:18434/v1')

    def test_example_matches_fresh_defaults(self):
        example = json.loads(config.EXAMPLE_PATH.read_text(encoding='utf-8'))
        self.assertEqual(example, config.DEFAULT_CONFIG)

    def test_frozen_build_keeps_user_data_next_to_exe_and_assets_in_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'AI-Control-Room.exe'
            bundle = Path(folder) / '_MEI123'
            with patch.object(config.sys, 'executable', str(exe)), \
                 patch.object(config.sys, '_MEIPASS', str(bundle), create=True):
                data, resources = config.app_dirs(frozen=True)
            self.assertEqual(data, exe.resolve().parent)
            self.assertEqual(resources, bundle)
        self.assertEqual(config.app_dirs(frozen=False), (config.APP_DIR, config.RESOURCE_DIR))

    def test_save_and_partial_load_preserve_keys_without_modifying_defaults(self):
        before = copy.deepcopy(config.DEFAULT_CONFIG)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            config.save_config({'providers': {'openai': {'api_key': 'synthetic-test-key'}}}, path)
            value = config.load_config(path)
            self.assertEqual(value['providers']['openai']['api_key'], 'synthetic-test-key')
            self.assertEqual(value['providers']['local']['base_url'], 'http://127.0.0.1:18434/v1')
            self.assertFalse(list(Path(folder).glob('*.tmp')))
        self.assertEqual(config.DEFAULT_CONFIG, before)

    def test_legacy_assignments_import_without_ship_code(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            config.save_config({'roles': {'captain': {'provider': 'openai', 'model': 'saved-model'}}}, path)
            self.assertEqual(room_specs(config.load_config(path))['atlas']['model'], 'saved-model')

    def test_hermes_uses_current_users_local_app_data(self):
        with tempfile.TemporaryDirectory() as folder:
            server = Path(folder) / 'hermes/runtimes/llamacpp/server.json'
            server.parent.mkdir(parents=True)
            server.write_text(json.dumps({'api_key': 'synthetic-local-key',
                                          'base_url': 'http://127.0.0.1:18434'}))
            with patch.dict(os.environ, {'LOCALAPPDATA': folder}):
                self.assertEqual(providers.resolve_hermes_local_endpoint(),
                                 ('http://127.0.0.1:18434/v1', 'synthetic-local-key'))

    def test_local_model_refresh_uses_matching_hermes_key(self):
        result = Mock(status_code=200)
        result.json.return_value = {'data': [{'id': 'local-test-model'}]}
        with patch.object(providers, 'resolve_hermes_local_endpoint',
                          return_value=('http://127.0.0.1:18434/v1', 'synthetic-local-key')):
            with patch.object(providers, '_request', return_value=result) as request:
                ok, _, models = providers.list_models('local', config.DEFAULT_CONFIG)
        self.assertTrue(ok)
        self.assertEqual(models, ['local-test-model'])
        self.assertEqual(request.call_args.kwargs['headers']['Authorization'], 'Bearer synthetic-local-key')
        self.assertEqual(request.call_args.kwargs['timeout'], 90)

    def test_cloud_refresh_without_key_never_sends_a_request(self):
        with patch.object(providers, '_request') as request:
            ok, message, models = providers.list_models('openai', config.DEFAULT_CONFIG)
        self.assertFalse(ok)
        self.assertIn('API key', message)
        self.assertEqual(models, [])
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
