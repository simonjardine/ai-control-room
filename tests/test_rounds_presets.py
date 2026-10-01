"""Round planning, blind context and preset storage. No GUI, no model calls."""
import unittest

import room_presets
from room import Conversation, DEFAULT_PROMPT
from room_rounds import instruction, plan, round_key

ORDER = ['atlas', 'jade', 'meridian', 'frontier', 'haven', 'horizon']


class RoundPlanTests(unittest.TestCase):
    def test_single_round_matches_the_old_behaviour(self):
        steps = plan(ORDER, ['jade', 'atlas'], 'public')
        self.assertEqual([s['fid'] for s in steps], ['atlas', 'jade'])
        self.assertTrue(all(s['job'] == 'answer' and not s['blind'] for s in steps))
        self.assertEqual(instruction(steps[0]), '')

    def test_debate_rounds_then_one_verdict_last(self):
        steps = plan(ORDER, ['atlas', 'jade', 'horizon'], 'public', rounds=3, blind_first=True, verdict='horizon')
        self.assertEqual([(s['fid'], s['round'], s['job']) for s in steps], [
            ('atlas', 1, 'answer'), ('jade', 1, 'answer'),
            ('atlas', 2, 'revise'), ('jade', 2, 'revise'),
            ('atlas', 3, 'revise'), ('jade', 3, 'revise'),
            ('horizon', 3, 'verdict')])
        self.assertEqual([s['blind'] for s in steps], [True, True] + [False] * 5)
        self.assertEqual(len({s['round_id'] for s in steps}), 1)
        self.assertIn('independently', instruction(steps[0]))
        self.assertIn('round 2 of 3', instruction(steps[2]))
        self.assertIn('final verdict', instruction(steps[-1]))

    def test_uninvited_verdict_seat_is_skipped_and_private_is_one_reply(self):
        steps = plan(ORDER, ['atlas'], 'public', rounds=2, verdict='horizon')
        self.assertEqual([s['job'] for s in steps], ['answer', 'revise'])
        private = plan(ORDER, ['jade'], 'jade', rounds=3, blind_first=True, verdict='horizon')
        self.assertEqual([(s['fid'], s['job'], s['blind']) for s in private], [('jade', 'answer', False)])
        self.assertEqual(instruction(private[0]), '')

    def test_blind_round_hides_only_other_models_in_that_round(self):
        chat = Conversation()
        chat.add('public', 'human', 'QUESTION')
        steps = plan(ORDER, ['atlas', 'jade'], 'public', rounds=2, blind_first=True)
        key = round_key(steps[0])
        chat.add('public', 'atlas', 'ATLAS_ROUND_ONE')['round_key'] = key
        tool = chat.add('public', 'tool', 'ATLAS_TOOL_RESULT')
        tool.update(kind='tool', owner='atlas', round_key=key)
        blind = str(chat.messages('jade', 'public', 'p', 't', blind_key=key))
        self.assertIn('QUESTION', blind)
        self.assertNotIn('ATLAS_ROUND_ONE', blind)
        self.assertNotIn('ATLAS_TOOL_RESULT', blind)
        self.assertIn('ATLAS_TOOL_RESULT', str(chat.messages('atlas', 'public', 'p', 't', blind_key=key)))
        self.assertIn('ATLAS_ROUND_ONE', str(chat.messages('jade', 'public', 'p', 't')))

    def test_retry_context_stops_before_the_replaced_reply(self):
        chat = Conversation()
        chat.add('public', 'human', 'FIRST')
        target = chat.add('public', 'atlas', 'OLD_REPLY')
        chat.add('public', 'human', 'LATER')
        context = str(chat.messages('atlas', 'public', 'p', 't', upto=target['id']))
        self.assertIn('FIRST', context)
        self.assertNotIn('OLD_REPLY', context)
        self.assertNotIn('LATER', context)

    def test_saved_entries_without_ids_get_them_and_insert_before_works(self):
        entries = [dict(channel='public', speaker='human', text='a')]
        chat = Conversation(entries)
        self.assertTrue(entries[0]['id'])
        chat.add('public', 'tool', 'b', before=entries[0]['id'])
        self.assertEqual([e['text'] for e in chat.entries], ['b', 'a'])


class PresetTests(unittest.TestCase):
    def test_my_prompts_comes_from_existing_personal_prompts(self):
        presets = room_presets.load({'prompts': {'atlas': 'MY OWN ATLAS PROMPT'}})
        mine = presets[room_presets.MY_PROMPTS]
        self.assertEqual(mine['prompts']['atlas'], 'MY OWN ATLAS PROMPT')
        self.assertEqual(mine['prompts']['jade'], DEFAULT_PROMPT)
        self.assertEqual((mine['rounds'], mine['blind_first'], mine['verdict']), (1, False, None))
        self.assertEqual(list(presets)[:6], [room_presets.MY_PROMPTS, *room_presets.BUILTIN])

    def test_saved_edits_override_builtins_and_custom_presets_load(self):
        settings = {'presets': {
            'Best answer': {'rounds': 2, 'prompts': {'atlas': 'EDITED'}},
            'Mine': {'rounds': 99, 'blind_first': 'yes', 'verdict': 'nobody'}}}
        presets = room_presets.load(settings)
        self.assertEqual(presets['Best answer']['rounds'], 2)
        self.assertEqual(presets['Best answer']['prompts']['atlas'], 'EDITED')
        self.assertEqual(presets['Mine']['rounds'], room_presets.MAX_ROUNDS)
        self.assertFalse(presets['Mine']['blind_first'])
        self.assertIsNone(presets['Mine']['verdict'])
        self.assertEqual(room_presets.active_name({'active_preset': 'Gone'}, presets), room_presets.MY_PROMPTS)

    def test_builtin_roles_extend_the_safe_default_prompt(self):
        best = room_presets.builtin_default('Best answer')
        self.assertTrue(all(p.startswith(DEFAULT_PROMPT) for p in best['prompts'].values()))
        self.assertIn('Final verdict', best['prompts']['horizon'])
        self.assertEqual(room_presets.describe(best), '3 rounds · blind start · verdict')


if __name__ == '__main__':
    unittest.main()
