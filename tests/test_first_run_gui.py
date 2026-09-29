"""First-run setup uses temporary local files and makes no provider calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import room_gui
from room_settings import Settings


class FirstRunGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.request_patch = patch('providers._request')
        self.request = self.request_patch.start()
        self.app = room_gui.RoomApp(data_root=self.temp.name)
        self.app.attributes('-alpha', 0)
        self.app.geometry('1180x800+10000+10000')
        self.app.update()
        self.errors = []
        self.app.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        if not self.app.closed:
            self.app.close()
        self.request_patch.stop()
        self.temp.cleanup()
        self.assertFalse(self.errors, self.errors)

    def test_fresh_start_has_named_empty_seats_and_keeps_unsent_draft(self):
        self.assertTrue(all(self.app.labels.values()))
        self.assertEqual(self.app.recipients(), [])
        self.app.input.insert('1.0', 'Keep this draft')
        self.app.send()
        self.assertEqual(self.app.input.get('1.0', 'end').strip(), 'Keep this draft')
        self.assertFalse(self.app.busy)
        self.assertFalse(self.app.conversation.entries)
        self.request.assert_not_called()

    def test_one_model_can_be_saved_and_is_the_only_invited_seat(self):
        settings = Settings(self.app)
        settings.withdraw()
        settings.provider_vars['openai']['key'].set('synthetic-test-key')
        settings.seats['atlas']['model'].set('gpt-test-model')
        settings.save()
        self.app.public_all()
        self.assertEqual(self.app.recipients(), ['atlas'])
        saved = json.loads((Path(self.temp.name) / 'config.json').read_text())
        self.assertEqual(saved['providers']['openai']['api_key'], 'synthetic-test-key')
        self.assertEqual(saved['room']['participants']['atlas']['model'], 'gpt-test-model')
        self.assertIn('atlas', self.app.icons)
        self.app.canvas.render()
        self.assertTrue(self.app.canvas._icon_cache)
        self.assertEqual(saved['room']['participants']['jade']['model'], '')
        settings.close()
        self.request.assert_not_called()

    def test_api_settings_can_be_saved_before_any_model_is_chosen(self):
        settings = Settings(self.app)
        settings.withdraw()
        settings.provider_vars['openrouter']['key'].set('synthetic-test-key')
        settings.save()
        self.assertTrue((Path(self.temp.name) / 'config.json').exists())
        self.assertEqual(self.app.recipients(), [])
        settings.close()
        self.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
