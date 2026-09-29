import tempfile
from pathlib import Path
import unittest

from room import Conversation, speaking_order
from room_store import RoomStore, memory_key


class ConversationTests(unittest.TestCase):
    def test_private_context_never_enters_another_channel(self):
        chat = Conversation()
        chat.add('public', 'human', 'PUBLIC_TOPIC')
        chat.add('atlas', 'human', 'ATLAS_PRIVATE')
        chat.add('jade', 'human', 'JADE_PRIVATE')
        public = str(chat.messages('atlas', 'public', 'prompt', 'topic'))
        private = str(chat.messages('atlas', 'atlas', 'prompt', 'topic'))
        self.assertNotIn('ATLAS_PRIVATE', public)
        self.assertNotIn('JADE_PRIVATE', private)
        self.assertIn('ATLAS_PRIVATE', private)
        with self.assertRaises(ValueError):
            chat.messages('jade', 'atlas', 'prompt', 'topic')

    def test_changing_seat_model_excludes_its_previous_private_history(self):
        specs = {'atlas': {'provider': 'openai', 'model': 'first'}}
        old_key = memory_key('atlas', specs)
        chat = Conversation()
        entry = chat.add('atlas', 'human', 'OLD_PRIVATE_MODEL')
        entry['audience_key'] = old_key
        specs['atlas']['model'] = 'second'
        self.assertNotIn('OLD_PRIVATE_MODEL', str(chat.messages(
            'atlas', 'atlas', 'prompt', 'topic', audience_key=memory_key('atlas', specs))))

    def test_session_restart_new_chat_and_private_safe_history_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            store = RoomStore(folder)
            first = store.fresh()
            first['entries'] = [{'channel': 'atlas', 'speaker': 'human', 'text': 'PRIVATE_SENTINEL'}]
            store.save(first)
            restarted = RoomStore(folder)
            self.assertEqual(restarted.last()['entries'], first['entries'])
            self.assertNotIn('PRIVATE_SENTINEL', str(restarted.history()))
            second = store.fresh(use_memory=False)
            store.save(second)
            self.assertFalse(store.last()['entries'])
            self.assertFalse(store.last()['use_memory'])
            self.assertEqual(store.load(first['id'])['entries'], first['entries'])

    def test_speaking_order_has_each_seat_once(self):
        self.assertEqual(speaking_order(['jade', 'jade', 'unknown'], ['atlas', 'jade']), ['jade', 'atlas'])


if __name__ == '__main__':
    unittest.main()
