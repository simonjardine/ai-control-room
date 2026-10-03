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

    def test_enter_sends_and_shift_enter_adds_a_newline(self):
        with patch.object(self.app, 'send') as send:
            self.app.input.focus_force()
            self.app.input.insert('1.0', 'line one')
            self.app.input.event_generate('<Shift-Return>')
            self.app.update()
            send.assert_not_called()
            self.assertEqual(self.app.input.get('1.0', 'end-1c'), 'line one\n')
            self.app.input.event_generate('<Return>')
            self.app.update()
            send.assert_called_once()
        self.assertEqual(self.app.input.get('1.0', 'end-1c'), 'line one\n')

    def test_conversation_button_opens_and_closes_the_panel(self):
        button = self.app.conversation_button
        self.assertFalse(self.app.transcript_visible)
        button.invoke()
        self.assertTrue(self.app.transcript_visible)
        self.assertEqual(button.cget('text'), 'Hide conversation')
        button.invoke()
        self.assertFalse(self.app.transcript_visible)
        self.assertEqual(button.cget('text'), 'Conversation')

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

    def test_openai_dropdown_opens_at_newest_model_without_changing_selection(self):
        settings = Settings(self.app)
        settings.withdraw()
        seat = settings.seats['atlas']
        models = ['gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna']
        models += [f'older-model-{i}' for i in range(30)]
        seat['combo']['values'] = models
        seat['model'].set('older-model-25')
        combo = seat['combo']
        combo.tk.call('ttk::combobox::Post', str(combo))
        self.app.update_idletasks()
        popdown = combo.tk.call('ttk::combobox::PopdownWindow', str(combo))
        self.assertEqual(combo.tk.call(popdown + '.f.l', 'yview')[0], 0.0)
        self.assertEqual(seat['model'].get(), 'older-model-25')
        combo.tk.call('ttk::combobox::Unpost', str(combo))
        settings.close()
        self.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
