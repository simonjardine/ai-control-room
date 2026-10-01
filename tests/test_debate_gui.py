"""Presets, debates and retry through real Tk callbacks; synthetic model replies only."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import room_gui
import room_presets
from config import DEFAULT_CONFIG, _deep_merge, save_config

SEATS = {'atlas': 'gpt-test', 'jade': 'model-b', 'horizon': 'model-judge'}


class DebateGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        cfg = _deep_merge(DEFAULT_CONFIG, {'room': {'participants': {
            f: {'model': m} for f, m in SEATS.items()}}})
        save_config(cfg, Path(self.temp.name) / 'config.json')
        self.app = room_gui.RoomApp(data_root=self.temp.name)
        self.app.attributes('-alpha', 0)
        self.app.geometry('1180x800+10000+10000')
        self.app.update()
        self.errors = []
        self.app.report_callback_exception = lambda *args: self.errors.append(args)
        self.calls = []

    def tearDown(self):
        if not self.app.closed:
            self.app.close()
        self.temp.cleanup()
        self.assertFalse(self.errors, self.errors)

    def fake_model(self, provider, cfg, model, messages, **kwargs):
        self.calls.append((model, messages))
        return True, json.dumps({'text': f'{model} reply {len(self.calls)}'})

    def run_until_idle(self, seconds=6):
        until = time.monotonic() + seconds
        while not self.app.idle() and time.monotonic() < until:
            self.app.update()
            time.sleep(.005)
        self.assertTrue(self.app.idle(), 'round did not finish')

    def send(self, text):
        self.app.input.insert('1.0', text)
        self.app.send()

    def test_best_answer_runs_blind_rounds_then_a_verdict(self):
        self.assertTrue(self.app.apply_preset('Best answer'))
        self.assertIn('Final verdict', self.app.prompts['horizon'])
        with patch('room.chat_completion', side_effect=self.fake_model), \
             patch('room_gui.messagebox.askyesno', return_value=True) as confirm:
            self.send('Which is better?')
            self.run_until_idle()
        confirm.assert_called_once()
        self.assertIn('7 model calls', confirm.call_args.args[1])
        replies = [e for e in self.app.conversation.visible('public') if e['speaker'] in SEATS]
        self.assertEqual([(e['speaker'], e['step']['round'], e['step']['job']) for e in replies], [
            ('atlas', 1, 'answer'), ('jade', 1, 'answer'),
            ('atlas', 2, 'revise'), ('jade', 2, 'revise'),
            ('atlas', 3, 'revise'), ('jade', 3, 'revise'), ('horizon', 3, 'verdict')])
        # Blind first round: jade cannot see atlas's round-one reply; round two can.
        jade_blind = str(self.calls[1][1])
        self.assertNotIn('gpt-test reply 1', jade_blind)
        self.assertIn('Answer independently', jade_blind)
        self.assertIn('gpt-test reply 1', str(self.calls[3][1]))
        self.assertIn('final verdict', str(self.calls[-1][1]))
        # The cost check is not repeated for the same preset in the same chat.
        with patch('room.chat_completion', side_effect=self.fake_model), \
             patch('room_gui.messagebox.askyesno') as confirm:
            self.send('Follow-up')
            self.run_until_idle()
        confirm.assert_not_called()

    def test_declining_the_cost_check_keeps_the_draft_and_sends_nothing(self):
        self.app.apply_preset('Pro vs con')
        with patch('room.chat_completion') as model, \
             patch('room_gui.messagebox.askyesno', return_value=False):
            self.send('Proposition')
        model.assert_not_called()
        self.assertEqual(self.app.input.get('1.0', 'end').strip(), 'Proposition')
        self.assertFalse(self.app.conversation.entries)

    def test_retry_replaces_one_failed_reply_in_place(self):
        responses = [(False, 'HTTP 503: busy'), (True, '{"text":"jade fine"}'), (True, '{"text":"atlas recovered"}')]
        with patch('room.chat_completion', side_effect=responses) as model:
            self.app.select['horizon'].set(False)
            self.send('Hello')
            self.run_until_idle()
            entries = self.app.conversation.entries
            failed = next(e for e in entries if e['speaker'] == 'atlas')
            self.assertTrue(failed['error'])
            self.assertIn('↻ Retry', self.app.transcript.get('1.0', 'end'))
            count = len(entries)
            self.app.retry(failed['id'])
            self.run_until_idle()
        self.assertEqual(model.call_count, 3)
        self.assertEqual(len(self.app.conversation.entries), count)
        replaced = self.app.conversation.entries[self.app.conversation.index(failed['id'])]
        self.assertEqual((replaced['text'], replaced['error'], replaced['retries']), ('atlas recovered', False, 1))
        # The retry saw the conversation as it was: the human message, not jade's later reply.
        retry_context = str(model.call_args_list[2].args[3])
        self.assertIn('Hello', retry_context)
        self.assertNotIn('jade fine', retry_context)

    def test_preset_editor_saves_rounds_and_verdict(self):
        from room_preset_dialog import NO_VERDICT, preset_dialog
        self.app.apply_preset('Free discussion')
        window = preset_dialog(self.app)
        self.app.update()
        widgets = list(window.winfo_children())
        while widgets:
            widget = widgets.pop()
            widgets.extend(widget.winfo_children())
            if widget.winfo_class() == 'TCombobox':
                self.assertEqual(widget.get(), NO_VERDICT)
                widget.set(f'{self.app.order.index("horizon")+1}. {self.app.labels["horizon"]}')
            if widget.winfo_class() == 'TButton' and widget.cget('text') == 'Save preset':
                save = widget
        save.invoke()
        preset = self.app.presets['Free discussion']
        self.assertEqual((preset['rounds'], preset['verdict']), (3, 'horizon'))
        self.assertIn('verdict by', self.app.preset_info.cget('text'))

    def test_preset_choice_and_edits_survive_a_restart(self):
        self.app.apply_preset('Stress-test my idea')
        preset = dict(self.app.presets['Stress-test my idea'])
        preset['prompts'] = dict(preset['prompts'], atlas='CUSTOM CRITIC')
        self.assertTrue(self.app.save_preset('My stress test', preset))
        self.app.close()
        self.app = room_gui.RoomApp(data_root=self.temp.name)
        self.app.withdraw()
        self.assertEqual(self.app.preset_name, 'My stress test')
        self.assertEqual(self.app.prompts['atlas'], 'CUSTOM CRITIC')
        self.assertIn('Stress-test my idea', self.app.presets)
        self.assertEqual(self.app.preset_button.cget('text'), 'My stress test ▾')
        self.app.apply_preset(room_presets.MY_PROMPTS)
        self.assertNotIn('YOUR ROLE', self.app.prompts['atlas'])


if __name__ == '__main__':
    unittest.main()
