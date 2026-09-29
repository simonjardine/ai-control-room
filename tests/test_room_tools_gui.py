"""Real Tk callbacks and file writes; synthetic provider responses, no paid requests."""
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import room_gui
from config import DEFAULT_CONFIG, _deep_merge, save_config


def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)


class RoomToolsGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        cfg=_deep_merge(DEFAULT_CONFIG, {'room': {'participants': {'atlas': {'model': 'test-model'}}}})
        save_config(cfg,Path(self.temp.name)/'config.json')
        self.app=room_gui.RoomApp(data_root=self.temp.name)
        self.app.attributes('-alpha',0)
        self.app.geometry('1180x800+10000+10000')
        self.app.update()
        self.errors=[]
        self.app.report_callback_exception=lambda *args:self.errors.append(args)

    def tearDown(self):
        if not self.app.closed:self.app.close()
        self.temp.cleanup()
        self.assertFalse(self.errors,self.errors)

    def pump(self,predicate,seconds=4):
        until=time.monotonic()+seconds
        while not predicate() and time.monotonic()<until:
            self.app.update();time.sleep(.005)
        self.assertTrue(predicate(),'Tk operation did not complete')

    def send_private(self,text):
        self.app.switch('atlas');self.app.input.insert('1.0',text);self.app.send()

    def test_preview_approval_real_file_and_private_evidence(self):
        responses=[(True,json.dumps(dict(tool='write_file',arguments=dict(path='test.txt',content='PRIVATE_TOOL_EVIDENCE')))),
                   (True,json.dumps(dict(tool='read_file',arguments=dict(path='test.txt')))),
                   (True,'{"text":"Created and verified the file."}')]
        with patch('room.chat_completion',side_effect=responses):
            self.send_private('Create test.txt with PRIVATE_TOOL_EVIDENCE then read it.')
            self.pump(lambda:bool(self.app.approval_requests))
            request=self.app.approval_requests[0]
            self.assertTrue(self.app.busy)
            self.assertFalse((Path(self.temp.name)/'workspace/test.txt').exists())
            self.assertIn('PRIVATE_TOOL_EVIDENCE',request['proposal']['content'])
            self.app.update()
            button=next(x for x in children(request['window']) if isinstance(x,ttk.Button)
                        and x.cget('text')=='Approve this file change')
            self.assertGreater(button.winfo_height(),10)
            button.invoke()
            self.pump(lambda:not self.app.busy)
        self.assertEqual((Path(self.temp.name)/'workspace/test.txt').read_text(),'PRIVATE_TOOL_EVIDENCE')
        entries=self.app.conversation.visible('atlas')
        self.assertEqual([e.get('kind') for e in entries],[None,'tool','tool',None])
        self.assertNotIn('PRIVATE_TOOL_EVIDENCE',str(self.app.conversation.messages('jade','public','p','t')))
        self.assertTrue(all(e.get('audience_key') for e in entries))
        self.assertGreater(self.app.input.winfo_height(),30)

    def test_pause_dismisses_preview_without_write(self):
        with patch('room.chat_completion',return_value=(True,'{"tool":"write_file","arguments":{"path":"no.txt","content":"no"}}')) as model:
            self.send_private('Create no.txt')
            self.pump(lambda:bool(self.app.approval_requests))
            self.app.pause()
            self.pump(lambda:not self.app.busy)
        self.assertEqual(model.call_count,1)
        self.assertFalse((Path(self.temp.name)/'workspace/no.txt').exists())
        self.assertTrue(self.app.paused)

    def test_close_cancels_pending_approval(self):
        with patch('room.chat_completion',return_value=(True,'{"tool":"write_file","arguments":{"path":"no.txt","content":"no"}}')):
            self.send_private('Create no.txt')
            self.pump(lambda:bool(self.app.approval_requests))
            request=self.app.approval_requests[0]
            self.app.close()
            time.sleep(.15)
        self.assertTrue(request['event'].is_set())
        self.assertFalse((Path(self.temp.name)/'workspace/no.txt').exists())

    def test_tool_controls_save_and_urls_are_clickable(self):
        self.app.open_tools();self.app.update()
        window=self.app.tools_window
        save=next(x for x in children(window) if isinstance(x,ttk.Button) and x.cget('text')=='Save tool settings')
        self.assertGreater(save.winfo_height(),10)
        save.invoke()
        settings=json.loads(self.app.settings_path.read_text())
        self.assertTrue(settings['tools']['read']);self.assertTrue(settings['tools']['write'])
        self.app.append('public','tool','Verified source https://docs.python.org/3/',kind='tool')
        tags=[t for t in self.app.transcript.tag_names() if t.startswith('source_')]
        self.assertEqual(len(tags),1)
        self.assertEqual(self.app.transcript.get(*self.app.transcript.tag_ranges(tags[0])),'https://docs.python.org/3/')


if __name__=='__main__':unittest.main()
