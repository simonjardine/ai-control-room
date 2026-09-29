import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import room
import room_tools
import room_web
from room_tools import RoomTools, ToolError, default_tools


class ToolFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=self.root/'workspace'
        self.workspace.mkdir()
        self.events=[]
        self.approvals=[]
        self.tools=RoomTools(default_tools(self.root),self.root,approve=self.approve,emit=self.events.append)

    def approve(self,proposal):
        self.approvals.append(proposal)
        return True


class WorkspaceToolsTests(ToolFixture):
    def test_create_read_edit_and_backup(self):
        created=self.tools.run('write_file',dict(path='notes/hello.txt',content='hello\nworld\n'))
        self.assertTrue(created['success'],created)
        self.assertEqual((self.workspace/'notes/hello.txt').read_text(),'hello\nworld\n')
        self.assertIn('1: hello',self.tools.run('read_file',dict(path='notes/hello.txt'))['output'])
        edited=self.tools.run('edit_file',dict(path='notes/hello.txt',old_text='world',new_text='room'))
        self.assertTrue(edited['success'],edited)
        self.assertEqual((self.root/'tool_backups'/(edited['backup_id']+'.bak')).read_bytes(),b'hello\nworld\n')
        self.assertIn('-world',self.approvals[-1]['diff'])
        self.assertIn('hello\nroom',self.approvals[-1]['content'])

    def test_denial_writes_nothing(self):
        self.tools.approve=lambda p:False
        result=self.tools.run('write_file',dict(path='missing/hello.txt',content='new'))
        self.assertFalse(result['success'])
        self.assertFalse((self.workspace/'missing').exists())

    def test_file_changed_during_approval_is_not_overwritten(self):
        file=self.workspace/'notes.txt';file.write_text('first')
        def changed(proposal):
            file.write_text('changed by human')
            return True
        self.tools.approve=changed
        self.assertFalse(self.tools.run('write_file',dict(path='notes.txt',content='model'))['success'])
        self.assertEqual(file.read_text(),'changed by human')

    def test_cancel_after_approval_prevents_write(self):
        cancel=threading.Event()
        self.tools.cancelled=cancel.is_set
        self.tools.approve=lambda p:cancel.set() or True
        self.assertFalse(self.tools.run('write_file',dict(path='hello.txt',content='new'))['success'])
        self.assertFalse((self.workspace/'hello.txt').exists())

    def test_disabled_tool_never_executes(self):
        self.tools.settings.update(read=False,write=False,web=False)
        for name,args in [('write_file',dict(path='file.txt',content='bad')),('web_search',dict(query='test')),
                          ('read_file',dict(path='file.txt')),('shell',dict(command='echo bad'))]:
            self.assertFalse(self.tools.run(name,args)['success'])
        self.assertFalse(self.approvals)

    def test_paths_credentials_and_internal_chat_storage_are_excluded(self):
        for path in ['../outside.txt','C:\\Windows\\file','\\\\server\\share\\a','file.txt:stream',
                     '.env','.env.local','.ssh/id_rsa','x/../a','aux.txt','trailing.']:
            with self.subTest(path=path):
                self.assertFalse(self.tools.run('write_file',dict(path=path,content='no'))['success'])
        (self.workspace/'tokens.txt').write_text('api_key = "private_dummy_value"')
        result=self.tools.run('read_file',dict(path='tokens.txt'))
        self.assertFalse(result['success']);self.assertNotIn('private_dummy_value',json.dumps(result))
        whole=RoomTools(dict(workspace=str(self.root),read=True,write=True,web=False),self.root)
        (self.root/'chats').mkdir();(self.root/'chats'/'private.json').write_text('private')
        self.assertFalse(whole.run('read_file',dict(path='chats/private.json'))['success'])

    def test_symlink_and_hardlink_escape(self):
        outside=self.root/'outside.txt';outside.write_text('outside')
        linked=self.workspace/'hard.txt'
        os.link(outside,linked)
        self.assertFalse(self.tools.run('read_file',dict(path='hard.txt'))['success'])
        self.assertFalse(self.tools.run('write_file',dict(path='hard.txt',content='new'))['success'])
        self.assertEqual(outside.read_text(),'outside')
        try:
            (self.workspace/'link.txt').symlink_to(outside)
        except OSError:
            return  # Windows developer mode may disable symlink creation; hardlink check still ran.
        self.assertFalse(self.tools.run('read_file',dict(path='link.txt'))['success'])

    def test_size_binary_and_unique_edit_guards(self):
        (self.workspace/'big.txt').write_bytes(b'a'*(room_tools.MAX_FILE_BYTES+1))
        (self.workspace/'binary.txt').write_bytes(b'abc\0def')
        (self.workspace/'repeat.txt').write_text('same same')
        for path in ('big.txt','binary.txt'):
            self.assertFalse(self.tools.run('read_file',dict(path=path))['success'])
        self.assertFalse(self.tools.run('edit_file',dict(path='repeat.txt',old_text='same',new_text='one'))['success'])
        self.assertFalse(self.approvals)

    def test_listing_search_and_line_ranges(self):
        (self.workspace/'notes.txt').write_text('first\nSecond word\nthird')
        (self.workspace/'.env').write_text('hidden')
        self.assertEqual(self.tools.run('list_directory',{})['output'],'notes.txt')
        self.assertIn('notes.txt:2: Second word',self.tools.run('search_files',dict(query='second'))['output'])
        result=self.tools.run('read_file',dict(path='notes.txt',start_line=2,max_lines=1))
        self.assertEqual(result['output'],'2: Second word');self.assertTrue(result['truncated'])


class ProtocolTests(ToolFixture):
    def test_actual_reply_loop_consumes_file_evidence(self):
        (self.workspace/'evidence.txt').write_text('EVIDENCE_VALUE_7281')
        responses=[(True,json.dumps(dict(tool='read_file',arguments=dict(path='evidence.txt')))),
                   (True,json.dumps(dict(text='Verified EVIDENCE_VALUE_7281')))]
        with patch('room.chat_completion',side_effect=responses) as model:
            ok,text=room.reply({},dict(provider='local',model='fixture'),[dict(role='system',content='persona'),
                dict(role='user',content='Read evidence.txt')],tools=self.tools)
        self.assertTrue(ok);self.assertIn('7281',text)
        self.assertIn('EVIDENCE_VALUE_7281',str(model.call_args.args[3]))
        self.assertEqual(len([e for e in self.events if e['phase']=='result']),1)

    def test_denied_write_is_returned_to_model(self):
        self.tools.approve=lambda p:False
        responses=[(True,json.dumps(dict(tool='write_file',arguments=dict(path='test.txt',content='test')))),
                   (True,'{"text":"The write was declined."}')]
        with patch('room.chat_completion',side_effect=responses) as model:
            ok,text=room.reply({},dict(provider='local',model='fixture'),[dict(role='system',content='persona')],tools=self.tools)
        self.assertTrue(ok)
        self.assertIn('declined',str(model.call_args.args[3]))
        self.assertFalse((self.workspace/'test.txt').exists())

    def test_budget_prevents_endless_loop(self):
        with patch('room.chat_completion',return_value=(True,'{"tool":"list_directory","arguments":{}}')) as model:
            ok,text=room.reply({},dict(provider='local',model='fixture'),[dict(role='system',content='persona')],tools=self.tools)
        self.assertFalse(ok)
        self.assertEqual(model.call_count,room_tools.MAX_TOOL_STEPS+1)
        self.assertEqual(len([e for e in self.events if e['phase']=='result']),room_tools.MAX_TOOL_STEPS)

    def test_pause_between_provider_response_and_tool(self):
        cancelled=threading.Event();self.tools.cancelled=cancelled.is_set
        def model(*args,**kwargs):
            cancelled.set()
            return True,'{"tool":"write_file","arguments":{"path":"no.txt","content":"no"}}'
        with patch('room.chat_completion',side_effect=model) as call:
            ok,_=room.reply({},dict(provider='local',model='fixture'),[dict(role='system',content='persona')],
                            tools=self.tools,cancelled=cancelled.is_set)
        self.assertFalse(ok);self.assertEqual(call.call_count,1)
        self.assertFalse(self.approvals)

    def test_malformed_multi_action_and_disabled_tools_fail(self):
        for raw in ['{"text":"done","tool":"write_file","arguments":{}}','[]','not json',
                    '{"tool":"read_file","arguments":{"path":"x"}}']:
            with patch('room.chat_completion',return_value=(True,raw)):
                ok,_=room.reply({},dict(provider='local',model='fixture'),[dict(role='system',content='persona')])
            self.assertFalse(ok)


class WebTests(unittest.TestCase):
    def test_private_urls_and_dns_are_blocked(self):
        for url in ['http://example.org','https://127.0.0.1/','https://[::1]/','https://192.168.1.1/',
                    'https://localhost/','https://example.org:8443/','https://user:pass@example.org/',
                    'file:///C:/file','https://example.org\n/']:
            with self.subTest(url=url),self.assertRaises(ToolError):room_web.public_url(url)
        private=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.0.0.2',443))]
        with patch('room_web.socket.getaddrinfo',return_value=private),self.assertRaises(ToolError):
            room_web.public_addresses('rebinding.example')

    def test_redirect_to_private_is_blocked(self):
        class Response:
            status=302
            def getheader(self,name):return 'https://127.0.0.1/private'
        with patch('room_web.public_addresses',return_value=['93.184.216.34']),patch('room_web.PinnedHTTPS') as cls:
            cls.return_value.getresponse.return_value=Response()
            with self.assertRaises(ToolError):room_web._fetch('https://example.org/')
            self.assertEqual(cls.call_count,1)

    def test_public_page_has_source_and_strips_script(self):
        payload=dict(body='<title>Official</title><script>STEAL</script><p>Useful evidence</p>',
                     content_type='text/html',url='https://example.org/final',truncated=False)
        with patch('room_web.fetch_text',return_value=payload):
            result=room_web.fetch_web_page('https://example.org')
        self.assertIn('Useful evidence',result['output']);self.assertNotIn('STEAL',result['output'])
        self.assertEqual(result['url'],'https://example.org/final')

    def test_search_requires_real_tool_evidence_and_preserves_deadline(self):
        cfg=dict(providers=dict(openai=dict(api_key='fixture-key',base_url='https://api.openai.com/v1')))
        data=dict(output=[dict(type='web_search_call',status='completed',action=dict(sources=[
            dict(url='https://docs.python.org/3/',title='Python docs')])),
            dict(type='message',content=[dict(type='output_text',text='Supported summary',annotations=[])])])
        with patch('room_web._request') as request:
            request.return_value.status_code=200;request.return_value.json.return_value=data
            result=room_web.web_search('Python docs',cfg)
            self.assertEqual(result['sources'][0]['url'],'https://docs.python.org/3/')
            self.assertEqual(request.call_args.kwargs['timeout'],60)
            self.assertFalse(request.call_args.kwargs['allow_redirects'])
            self.assertFalse(request.call_args.kwargs['json']['store'])
            self.assertEqual(request.call_args.kwargs['json']['input'],'Python docs')
            request.return_value.json.return_value=dict(output=[data['output'][1]])
            with self.assertRaises(ToolError):room_web.web_search('Python docs',cfg)

    def test_missing_key_and_provider_errors_are_explicit(self):
        with self.assertRaises(ToolError):room_web.web_search('Python docs',{})
        cfg=dict(providers=dict(openai=dict(api_key='fixture-key')))
        with patch('room_web._request',side_effect=TimeoutError('sensitive error')):
            with self.assertRaises(ToolError) as raised:room_web.web_search('Python docs',cfg)
        self.assertNotIn('sensitive',str(raised.exception))


if __name__=='__main__':
    unittest.main()
