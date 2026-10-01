"""Seven-seat dark control room, human-led bounded discussion and private chats."""
import json
import math
import queue
import re
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from pathlib import Path
from datetime import datetime
from config import APP_DIR, RESOURCE_DIR, load_config
from providers import provider_timeout
from room import Conversation, DEFAULT_PROMPT, current_prompt, reply, room_specs, speaking_order
import room_presets
from room_rounds import instruction, plan, round_key
from room_store import RoomStore, memory_key, write_json, read_json
from room_scene import CouncilScene
from room_tools import RoomTools, default_tools, normalize_tools

ROOT = RESOURCE_DIR
BG, PANEL, BORDER, TEXT, MUTED, BLUE = '#070d18', '#101d30', '#233c57', '#e1edfa', '#8ba4bc', '#57cfff'
IDENTITIES = [('openai','GPT','#73e0be'), ('deepseek','DeepSeek','#6599ff'),
              ('qwen','Qwen','#b5a0ff'), ('grok','Grok','#d4e2f4'),
              ('gemma','Gemma','#84baff'), ('kimi','Kimi','#8f96ff'),
              ('gemini','Gemini','#8ab4f8'), ('claude','Claude','#e0926f')]


class RoomApp(tk.Tk):
    def __init__(self, data_root=None):
        super().__init__()
        self.title('AI Control Room | Seven seats')
        self.geometry('1480x940'); self.minsize(1180,800); self.configure(bg=BG)
        self.data_root=Path(data_root) if data_root else APP_DIR
        self.cfg = load_config(self.data_root/'config.json'); self.specs = room_specs(self.cfg)
        self.runtime_busy = False
        self.fids = list(self.specs)
        self.channel = 'public'; self.pending = []; self.busy = False; self.paused = False
        self.active = None; self.work_step = None; self.closed = False; self.results = queue.Queue()
        self.cost_ok = set()
        self.store=RoomStore(self.data_root)
        self.session=self.store.last() or self.store.fresh()
        self.conversation=Conversation(self.session['entries'])
        self.settings_path = self.data_root/'room_settings.json'
        try: self.settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
        except (OSError, ValueError): self.settings = {}
        self.tool_settings=normalize_tools(self.settings.get('tools'),self.data_root)
        Path(default_tools(self.data_root)['workspace']).mkdir(parents=True,exist_ok=True)
        self.cancel_turn=threading.Event();self.approval_requests=[]
        self.tool_status=tk.StringVar()
        self.presets=room_presets.load(self.settings)
        self.preset_name=room_presets.active_name(self.settings,self.presets)
        self.prompts={f:current_prompt(p) for f,p in self.presets[self.preset_name]['prompts'].items()}
        self.order=speaking_order(self.settings.get('order'),self.fids)
        self.topic = tk.StringVar(value=self.session.get('topic','Open conversation'))
        self.host = tk.StringVar(value=self.settings.get('host','You'))
        self.use_memory=tk.BooleanVar(value=self.session.get('use_memory',True))
        self.status = tk.StringVar(value='Ready. Send a message or invite one round of replies.')
        self.select = {f:tk.BooleanVar(value=bool(self.specs[f]['model'])) for f in self.fids}
        self.labels = {}; self.colors = {}; self.icons = {}
        for f,spec in self.specs.items():
            model = spec['model'].lower()
            identity = next((v for v in IDENTITIES if v[0] in model or (v[0]=='openai' and 'gpt' in model)), None)
            self.labels[f] = self.model_label(spec,identity,f)
            self.colors[f] = identity[2] if identity else BLUE
            if identity:
                try:
                    im=tk.PhotoImage(file=str(ROOT/'assets'/'logos'/f'{identity[0]}.png'))
                    self.icons[f]=im.subsample(max(1,math.ceil(im.width()/36)))
                except tk.TclError: pass
        folder=self.data_root/'replays';folder.mkdir(exist_ok=True)
        self.replay=folder/f'room_{datetime.now():%Y%m%d_%H%M%S_%f}.jsonl'
        self.session.setdefault('replay_files',[]).append(self.replay.name)
        self.build()
        self.switch('public')
        if not any(spec['model'] for spec in self.specs.values()):
            self.status.set('Welcome. Open Settings & APIs to configure your first model. Empty seats stay off.')
        if self.conversation.entries:self.status.set('Resumed saved chat. Recent messages and its saved recap are available to each matching channel.')
        self.poll_id=self.after(50,self.poll)
        self.protocol('WM_DELETE_WINDOW',self.close)

    def button(self,parent,text,command,accent=False):
        return tk.Button(parent,text=text,command=command,bg='#174c68' if accent else PANEL,
                         fg=TEXT,activebackground='#235875',activeforeground='white',
                         relief='flat',bd=0,padx=12,pady=7,cursor='hand2',font=('Segoe UI',10))

    def save_preferences(self, prompts=None, order=None, tools=None, preset=None):
        """Persist room settings. Prompt edits are saved into the active preset."""
        settings=read_json(self.settings_path,{})
        if not isinstance(settings,dict):settings={}
        saved=settings.get('presets') if isinstance(settings.get('presets'),dict) else {}
        if prompts is not None or preset is not None:
            value=dict(preset or self.presets[self.preset_name])
            if prompts is not None:value['prompts']=dict(prompts)
            saved[self.preset_name]=room_presets.normalize(value)
            if self.preset_name==room_presets.MY_PROMPTS:settings['prompts']=dict(value['prompts'])
        settings.update(presets=saved,active_preset=self.preset_name,
                        order=self.order if order is None else list(order),host=self.host.get())
        settings['tools']=self.tool_settings if tools is None else tools
        write_json(self.settings_path,settings)
        self.settings=settings
        if self.preset_name in saved:self.presets[self.preset_name]=saved[self.preset_name]

    def idle(self):
        return not (self.busy or self.pending or self.runtime_busy)

    def apply_preset(self,name):
        """Switch every seat prompt and the round settings to one preset."""
        if name not in self.presets:return False
        if not self.idle():
            self.status.set('Finish or pause the current round before changing preset.');return False
        previous=self.preset_name;self.preset_name=name
        try:self.save_preferences()
        except OSError:
            self.preset_name=previous;self.status.set('Could not save the preset choice.');return False
        self.prompts={f:current_prompt(p) for f,p in self.presets[name]['prompts'].items()}
        self.refresh_preset_bar()
        self.status.set(f'Preset “{name}” active: {self.preset_summary()}. Seat prompts updated.')
        return True

    def save_preset(self,name,preset):
        """Save a named preset (new, or an edit of an existing one) and switch to it."""
        if not self.idle():
            self.status.set('Finish or pause the current round before saving a preset.');return False
        previous=self.preset_name
        self.presets[name]=room_presets.normalize(preset);self.preset_name=name
        try:self.save_preferences(preset=self.presets[name])
        except OSError:
            self.preset_name=previous;self.status.set('Could not save the preset.');return False
        self.prompts={f:current_prompt(p) for f,p in self.presets[name]['prompts'].items()}
        self.refresh_preset_bar();return True

    def preset_summary(self):
        preset=self.presets[self.preset_name]
        text=room_presets.describe(preset)
        if preset['verdict']:text+=f' by {self.order.index(preset["verdict"])+1}. {self.labels[preset["verdict"]]}'
        return text

    def refresh_preset_bar(self):
        if not hasattr(self,'preset_menu'):return
        menu=self.preset_menu;menu.delete(0,'end')
        for name in self.presets:
            menu.add_radiobutton(label=name,value=name,variable=self.preset_var,
                                 command=lambda n=name:self.apply_preset(n))
        menu.add_separator()
        menu.add_command(label='Edit this preset…',command=self.open_preset_editor)
        self.preset_var.set(self.preset_name)
        self.preset_button.configure(text=f'{self.preset_name} ▾')
        self.preset_info.configure(text=self.preset_summary())

    def open_preset_editor(self):
        from room_preset_dialog import preset_dialog
        preset_dialog(self)

    def persist_chat(self):
        self.session.update(entries=self.conversation.entries,topic=self.topic.get(),
                            use_memory=bool(self.use_memory.get()),channel=self.channel)
        self.session.setdefault('participants',dict(self.specs))
        try:self.store.save(self.session);return True
        except OSError:
            self.status.set('Could not save this chat. It remains open in memory.');return False

    def new_chat(self):
        if self.busy or self.runtime_busy:
            self.status.set('Pause and wait for the in-flight request to finish before starting a new chat.');return
        if self.input.get('1.0','end').strip():
            self.status.set('Send or clear your unsent draft before starting a new chat.');return
        if not self.persist_chat():return
        self.activate_chat(self.store.fresh(use_memory=bool(self.use_memory.get())),save_current=False)
        self.status.set('New chat. Previous chat saved. '+('Saved memory is ON.' if self.use_memory.get() else 'Saved memory is OFF.'))

    def activate_chat(self,data,save_current=True):
        if self.busy or self.runtime_busy:return False
        if self.input.get('1.0','end').strip():self.status.set('Send or clear the current draft before switching chats.');return False
        if save_current and not self.persist_chat():return False
        self.pending.clear();self.paused=False;self.active=None
        self.session=data;self.conversation=Conversation(data['entries'])
        # Legacy transcripts lacked audience identities. Import them into the selected seat assignment.
        bindings=data.get('participants',self.specs)
        for entry in self.conversation.entries:
            if entry['channel']!='public' and 'audience_key' not in entry and entry['channel'] in bindings:
                entry['audience_key']=memory_key(entry['channel'],bindings)
        self.topic.set(data.get('topic','Open conversation'));self.use_memory.set(data.get('use_memory',True))
        self.replay=self.data_root/'replays'/f'room_{datetime.now():%Y%m%d_%H%M%S_%f}.jsonl'
        self.session.setdefault('replay_files',[]).append(self.replay.name)
        self.switch('public');self.persist_chat();self.status.set('Chat resumed. No model call has been made.');return True

    def open_order(self):
        from room_dialogs import order_dialog
        order_dialog(self)

    def open_history(self):
        from room_dialogs import history_dialog
        if self.persist_chat():history_dialog(self)

    def open_memory(self):
        from room_dialogs import memory_dialog
        memory_dialog(self)

    def open_tools(self):
        from room_tool_dialogs import tools_dialog
        tools_dialog(self)

    def refresh_tool_status(self):
        s=self.tool_settings
        self.tool_status.set(f'{Path(s["workspace"]).name[:40]}  ·  Read {"ON" if s["read"] else "OFF"}'
            f'  ·  Write {"asks" if s["write"] else "OFF"}  ·  {"Web via OpenAI" if s["web"] else "Web OFF"}')

    def model_label(self,spec,identity,fid=None):
        name=identity[1] if identity else spec['model'].split('/')[-1][:22]
        if not name:name=f'Seat {self.fids.index(fid)+1}' if fid in self.fids else 'Choose model'
        if 'gemma-4' in spec['model'].lower():name='Gemma 4'
        return name+(' (LLM)' if spec['provider']=='local' else '')

    def refresh_identity(self):
        self.icons.clear()
        for f,spec in self.specs.items():
            model=spec['model'].lower()
            identity=next((v for v in IDENTITIES if v[0] in model or (v[0]=='openai' and 'gpt' in model)),None)
            self.labels[f]=self.model_label(spec,identity,f);self.colors[f]=identity[2] if identity else BLUE
            if identity:
                try:
                    im=tk.PhotoImage(file=str(ROOT/'assets'/'logos'/f'{identity[0]}.png'))
                    self.icons[f]=im.subsample(max(1,math.ceil(im.width()/36)))
                except tk.TclError:pass
            self.reply_checks[f].configure(text=f'{self.order.index(f)+1}. {self.labels[f]}',fg=self.colors[f],
                                           state='normal' if spec['model'] else 'disabled')
        self.switch(self.channel)

    def open_settings(self):
        from room_settings import Settings
        if getattr(self,'settings_window',None) and self.settings_window.winfo_exists():
            self.settings_window.lift();return
        self.settings_window=Settings(self)

    def public_all(self):
        self.switch('public')
        for f,value in self.select.items():value.set(bool(self.specs[f]['model']))
        self.draw()
        self.status.set('Public room: your next message will invite every configured model to reply.')

    def build(self):
        self.transcript_visible = False
        top=tk.Frame(self,bg=BG);top.pack(fill='x',padx=22,pady=(16,8))
        heading=tk.Frame(top,bg=BG);heading.pack(side='left')
        tk.Label(heading,text='THE CONTROL ROOM',font=('Segoe UI',18,'bold'),fg=TEXT,bg=BG).pack(anchor='w')
        tk.Label(heading,text='SIX MODELS  /  ONE TABLE  /  YOUR CONVERSATION',font=('Segoe UI',8),fg=BLUE,bg=BG).pack(anchor='w')
        self.transcript_button=self.button(top,'Show transcript',self.toggle_transcript)
        self.transcript_button.pack(side='right',padx=(8,0))
        self.button(top,'Settings & APIs',self.open_settings).pack(side='right',padx=(8,0))
        controls=tk.Menubutton(top,text='Room controls ▾',bg=PANEL,fg=TEXT,
            activebackground='#235875',activeforeground='white',relief='flat',
            padx=12,pady=7,font=('Segoe UI',10),cursor='hand2')
        menu=tk.Menu(controls,tearoff=False,bg=PANEL,fg=TEXT,activebackground='#235875',
                     activeforeground='white',font=('Segoe UI',10))
        menu.add_command(label='Preset & seat prompts…',command=self.open_preset_editor)
        menu.add_command(label='Speaking order / reviewer…',command=self.open_order)
        menu.add_command(label='Memory / recap…',command=self.open_memory)
        menu.add_command(label='Files & internet…',command=self.open_tools)
        menu.add_separator()
        menu.add_checkbutton(label='Use saved memory',variable=self.use_memory,command=self.persist_chat)
        controls.configure(menu=menu);controls.pack(side='right',padx=(8,0))
        self.button(top,'Chat history',self.open_history).pack(side='right',padx=(8,0))
        self.button(top,'New chat',self.new_chat).pack(side='right',padx=(8,0))

        topicbar=tk.Frame(self,bg=BG);topicbar.pack(fill='x',padx=24,pady=(0,8))
        tk.Label(topicbar,text='TOPIC',bg=BG,fg=MUTED,font=('Segoe UI',8,'bold')).pack(side='left',padx=(0,12))
        entry=tk.Entry(topicbar,textvariable=self.topic,bg=BG,fg=TEXT,insertbackground=TEXT,
            relief='flat',font=('Segoe UI',11));entry.pack(side='left',fill='x',expand=True)
        self.topic.trace_add('write',lambda *_:self.draw())
        self.preset_var=tk.StringVar(value=self.preset_name)
        self.preset_info=tk.Label(topicbar,bg=BG,fg=MUTED,font=('Segoe UI',8))
        self.preset_info.pack(side='right')
        self.preset_button=tk.Menubutton(topicbar,bg=PANEL,fg=TEXT,activebackground='#235875',
            activeforeground='white',relief='flat',padx=12,pady=4,font=('Segoe UI',10,'bold'),cursor='hand2')
        self.preset_menu=tk.Menu(self.preset_button,tearoff=False,bg=PANEL,fg=TEXT,activebackground='#235875',
                                 activeforeground='white',selectcolor=BLUE,font=('Segoe UI',10))
        self.preset_button.configure(menu=self.preset_menu);self.preset_button.pack(side='right',padx=8)
        tk.Label(topicbar,text='PRESET',bg=BG,fg=MUTED,font=('Segoe UI',8,'bold')).pack(side='right')
        self.refresh_preset_bar()

        toolbar=tk.Frame(self,bg=BG);toolbar.pack(fill='x',padx=22,pady=(0,6))
        self.button(toolbar,'Files & internet',self.open_tools).pack(side='left')
        tk.Label(toolbar,textvariable=self.tool_status,bg=BG,fg=MUTED,font=('Segoe UI',9)).pack(side='left',padx=12)
        self.conversation_button=self.button(toolbar,'Conversation',self.toggle_transcript)
        self.conversation_button.pack(side='right')
        self.refresh_tool_status()

        # Pack the composer before the scene so it remains visible at minimum size.
        tk.Label(self,textvariable=self.status,bg=BG,fg=MUTED,font=('Segoe UI',9),
                 anchor='w').pack(side='bottom',fill='x',padx=24,pady=(0,8))
        bottom=tk.Frame(self,bg=PANEL,padx=14,pady=10)
        bottom.pack(side='bottom',fill='x',padx=18,pady=(6,8))
        recipients=tk.Frame(bottom,bg=PANEL);recipients.pack(fill='x')
        tk.Label(recipients,text='INVITE',bg=PANEL,fg=MUTED,font=('Segoe UI',8)).pack(side='left',padx=(0,6))
        self.reply_checks={}
        for f in self.fids:
            check=tk.Checkbutton(recipients,text=f'{self.order.index(f)+1}. {self.labels[f]}',
                variable=self.select[f],command=self.draw,bg=PANEL,fg=self.colors[f],font=('Segoe UI',9),
                selectcolor=BG,activebackground=PANEL,activeforeground=TEXT)
            check.configure(state='normal' if self.specs[f]['model'] else 'disabled')
            check.pack(side='left');self.reply_checks[f]=check
        row=tk.Frame(bottom,bg=PANEL);row.pack(fill='x',pady=(6,0))
        self.destination=tk.Label(row,text='You → room',bg=PANEL,fg=BLUE,width=19,
            anchor='w',font=('Segoe UI',10));self.destination.pack(side='left')
        self.input=tk.Text(row,height=2,bg=BG,fg=TEXT,insertbackground=TEXT,
            font=('Segoe UI',11),relief='flat',padx=10,pady=8,wrap='word')
        self.input.pack(side='left',fill='x',expand=True)
        # Enter sends; Shift+Enter keeps Tk's default newline. Ctrl+Enter still sends.
        self.input.bind('<Return>',self._send_shortcut)
        self.input.bind('<Shift-Return>',lambda event:None)
        self.input.bind('<Control-Return>',self._send_shortcut)
        self.button(row,'Send ↗',self.send,True).pack(side='right',padx=(10,0))
        self.recorder=None;self.transcribing=False
        self.mic_button=self.button(row,'● Mic',self.toggle_mic)
        self.mic_button.pack(side='right',padx=(10,0))
        self.bind('<F2>',lambda event:self.toggle_mic())
        self.button(row,'Pause',self.pause).pack(side='right',padx=(8,0))
        self.button(row,'One round / resume',self.invite).pack(side='right',padx=(8,0))

        self.middle=tk.Frame(self,bg=BG)
        self.middle.pack(fill='both',expand=True,padx=18)
        self.transcript_panel=tk.Frame(self.middle,bg=PANEL,width=370)
        self.transcript_panel.pack_propagate(False)
        side=self.transcript_panel
        self.channel_label=tk.Label(side,text='PUBLIC ROOM',bg=PANEL,fg=BLUE,
            font=('Segoe UI',12,'bold'),pady=10);self.channel_label.pack(anchor='w',padx=12)
        self.button(side,'Chat to everyone · public room',self.public_all,True).pack(anchor='w',padx=10)
        self.transcript=scrolledtext.ScrolledText(side,wrap='word',bg=PANEL,fg=TEXT,insertbackground=TEXT,
            font=('Segoe UI',10),relief='flat',padx=12,pady=12,state='disabled')
        self.transcript.pack(fill='both',expand=True,pady=8)
        self.transcript.tag_configure('name',foreground=BLUE,font=('Segoe UI',10,'bold'))
        self.transcript.tag_configure('error',foreground='#ff9b9b')
        self.transcript.tag_configure('tool',foreground='#9db5cc',font=('Consolas',9))
        self.transcript.tag_configure('retry',foreground=MUTED,font=('Segoe UI',8))
        self.share_button=self.button(side,'Share latest private reply with room',self.share)
        self.share_button.pack(fill='x',padx=10,pady=(0,10));self.share_button.configure(state='disabled')
        self.canvas=CouncilScene(self.middle,self)
        self.canvas.pack(side='left',fill='both',expand=True)
        for i in range(1,len(self.fids)+1):
            self.bind(f'<Alt-Key-{i}>',lambda event,n=i-1:self.open_seat(self.order[n]))
        self.bind('<Alt-Key-0>',lambda event:self.public_all())
        self.draw()

    def _send_shortcut(self,event):
        self.send()
        return 'break'

    def toggle_mic(self):
        from room_voice import MAX_SECONDS, NO_KEY_MESSAGE, Recorder, VoiceError, has_key
        if self.transcribing:return
        if self.recorder is not None:
            self.finish_recording();return
        # Check before recording so nobody speaks into a recording that cannot be transcribed.
        if not has_key(self.cfg):self.status.set(NO_KEY_MESSAGE);return
        recorder=Recorder()
        try:recorder.start()
        except VoiceError as exc:self.status.set(str(exc));return
        self.recorder=recorder
        self.mic_button.configure(text='■ Stop',bg='#7a2630')
        def tick():
            if self.recorder is not recorder or self.closed:return
            seconds=int(recorder.elapsed)
            if seconds>=MAX_SECONDS:self.finish_recording();return
            self.status.set(f'Recording {seconds//60}:{seconds%60:02d} · click Stop or press F2 when you finish speaking.')
            self.after(250,tick)
        tick()

    def finish_recording(self):
        from room_voice import MIN_SECONDS, VoiceError, transcribe, wav_seconds
        recorder,self.recorder=self.recorder,None
        if recorder is None:return
        try:audio=recorder.stop()
        except Exception as exc:
            self.mic_button.configure(text='● Mic',bg=PANEL)
            self.status.set(f'Recording failed ({type(exc).__name__}).');return
        if wav_seconds(audio)<MIN_SECONDS:
            self.mic_button.configure(text='● Mic',bg=PANEL)
            self.status.set('Recording was too short. Click Mic, speak, then click Stop.');return
        self.transcribing=True
        self.mic_button.configure(text='Transcribing…',bg=PANEL,state='disabled')
        self.status.set('Transcribing your recording with OpenAI…')
        cfg=self.cfg
        def worker():
            try:ok,text=True,transcribe(audio,cfg)
            except VoiceError as exc:ok,text=False,str(exc)
            except Exception as exc:ok,text=False,f'Transcription failed ({type(exc).__name__}).'
            self.results.put(('voice',ok,text))
        threading.Thread(target=worker,daemon=True).start()

    def voice_result(self,ok,text):
        self.transcribing=False
        self.mic_button.configure(text='● Mic',state='normal')
        if not ok:self.status.set(text);return
        before=self.input.get('1.0','insert')
        self.input.insert('insert',(' ' if before and not before[-1].isspace() else '')+text)
        self.input.focus_set()
        self.status.set('Transcribed into your message. Edit it if needed, then press Enter to send.')

    def toggle_transcript(self,show=None):
        visible=not self.transcript_visible if show is None else bool(show)
        if visible==self.transcript_visible:return
        self.transcript_visible=visible
        if visible:
            self.canvas.pack_forget()
            self.transcript_panel.pack(side='right',fill='y',padx=(12,0))
            self.canvas.pack(side='left',fill='both',expand=True)
        else:self.transcript_panel.pack_forget()
        self.transcript_button.configure(text='Hide transcript' if visible else 'Show transcript')
        self.conversation_button.configure(text='Hide conversation' if visible else 'Conversation')
        self.after_idle(self.draw)

    def open_seat(self,fid):
        self.switch(fid)
        self.toggle_transcript(True)
        self.input.focus_set()

    def draw(self):
        if hasattr(self,'canvas'):self.canvas.render()

    def switch(self,channel):
        self.channel=channel
        self.channel_label.configure(text='PUBLIC ROOM' if channel=='public' else 'PRIVATE · '+self.labels[channel])
        self.destination.configure(text='You → room' if channel=='public' else 'You → '+self.labels[channel])
        self.share_button.configure(state='disabled' if channel=='public' else 'normal')
        self.refresh_transcript();self.draw()

    def refresh_transcript(self):
        t=self.transcript;t.configure(state='normal');t.delete('1.0','end')
        for tag in t.tag_names():
            if tag.startswith(('source_','retry_')):t.tag_delete(tag)
        link_number=0
        for e in self.conversation.visible(self.channel):
            name=e.get('display_name') or (self.host.get() if e['speaker']=='human' else self.labels.get(e['speaker'],e['speaker']))
            step=e.get('step') or {}
            if e.get('kind')!='tool' and step.get('job')=='verdict':name+='  ·  verdict'
            elif e.get('kind')!='tool' and step.get('rounds',1)>1:name+=f'  ·  round {step["round"]} of {step["rounds"]}'
            t.insert('end',name+'\n','name')
            style='error' if e.get('error') else 'tool' if e.get('kind')=='tool' else ()
            start=t.index('end-1c');t.insert('end',e['text']+'\n',style)
            if e['speaker'] in self.fids and e.get('kind')!='tool':
                tag='retry_'+e['id']
                t.insert('end','↻ Retry',('retry',tag));t.insert('end','\n')
                t.tag_bind(tag,'<Button-1>',lambda event,i=e['id']:self.retry(i))
                t.tag_bind(tag,'<Enter>',lambda event:t.configure(cursor='hand2'))
                t.tag_bind(tag,'<Leave>',lambda event:t.configure(cursor='xterm'))
            t.insert('end','\n')
            for match in re.finditer(r'https://[^\s<>\"]+',e['text']):
                url=match.group().rstrip(').,;]}')
                try:
                    from room_web import public_url
                    public_url(url)
                except ValueError:continue
                tag=f'source_{link_number}';link_number+=1
                t.tag_add(tag,f'{start}+{match.start()}c',f'{start}+{match.start()+len(url)}c')
                t.tag_configure(tag,foreground=BLUE,underline=True)
                t.tag_bind(tag,'<Button-1>',lambda event,u=url:webbrowser.open(u))
                t.tag_bind(tag,'<Enter>',lambda event:t.configure(cursor='hand2'))
                t.tag_bind(tag,'<Leave>',lambda event:t.configure(cursor='xterm'))
        t.configure(state='disabled');t.see('end')

    def append(self,channel,speaker,text,error=False,before=None,**metadata):
        e=self.conversation.add(channel,speaker,text,error,before=before)
        e['display_name']=self.host.get() if speaker=='human' else self.labels.get(speaker,speaker)
        e.update(metadata)
        if channel!='public':e['audience_key']=memory_key(channel,self.specs)
        self.log_replay(e);self.persist_chat();self.refresh_transcript();self.draw()
        return e

    def log_replay(self,record):
        try:
            with self.replay.open('a',encoding='utf-8') as fp:fp.write(json.dumps(record,ensure_ascii=False)+'\n')
        except OSError:self.status.set('Conversation works, but local replay could not be saved.')

    @staticmethod
    def step_metadata(step):
        """What an entry remembers about the step that produced it (for blind rounds and retry)."""
        meta=dict(step={k:step[k] for k in ('job','round','rounds','blind','round_id') if k in step})
        if step.get('round_id'):meta['round_key']=round_key(step)
        return meta

    def recipients(self):
        return ([self.channel] if self.specs[self.channel]['model'] else []) if self.channel!='public' else [
            f for f in self.order if self.select[f].get() and self.specs[f]['model']]

    def send(self):
        if self.runtime_busy:self.status.set('Wait for the local model operation to finish.');return
        if self.busy or (self.pending and not self.paused):
            self.status.set('A reply round is in progress. Pause holds queued replies; resume to finish before sending.');return
        text=self.input.get('1.0','end').strip()
        if not text:return
        steps=self.plan_round()
        if not steps:return  # The draft is kept; plan_round explained why.
        # A human intervention while paused replaces the unstarted reply queue.
        if self.paused:self.pending.clear()
        self.input.delete('1.0','end');self.append(self.channel,'human',text)
        self.begin(steps)

    def invite(self):
        if self.runtime_busy:self.status.set('Wait for the local model operation to finish.');return
        if self.busy:
            self.paused=False;self.status.set('Resumed. Waiting for the current reply.');return
        if self.pending:self.paused=False;self.advance()
        else:
            steps=self.plan_round()
            if steps:self.begin(steps)

    def plan_round(self):
        """Reply steps for the active preset, or None after telling the host why not."""
        invited=self.recipients()
        if not invited:
            self.status.set('Choose a model in Settings & APIs and invite it before sending. Your draft is kept.');return None
        preset=self.presets[self.preset_name]
        steps=plan(self.order,invited,self.channel,preset['rounds'],preset['blind_first'],preset['verdict'])
        # Multi-round presets multiply cost; confirm once per chat and preset.
        key=(self.session['id'],self.preset_name)
        if len(steps)>len(invited) and key not in self.cost_ok:
            if not messagebox.askyesno('Start discussion',
                    f'“{self.preset_name}” runs {self.preset_summary()}.\n\n'
                    f'This message will make {len(steps)} model calls. Pause stops it at any time.\n\n'
                    'Start? (You will not be asked again for this preset in this chat.)',parent=self):
                self.status.set('Not started. Your draft is kept; switch preset or invite fewer models.');return None
            self.cost_ok.add(key)
        return steps

    def begin(self,steps):
        self.pending=list(steps)
        self.round_prompts=dict(self.prompts)
        self.round_tools=dict(self.tool_settings)
        self.paused=False;self.advance()

    def retry(self,entry_id):
        """Run one model again with the conversation as it was, replacing that reply in place."""
        index=self.conversation.index(entry_id)
        if index is None:return
        entry=self.conversation.entries[index];fid=entry['speaker']
        if not self.idle():
            self.status.set('Retry is available when the room is idle. Pause or let the round finish first.');return
        if fid not in self.fids or not self.specs[fid]['model']:
            self.status.set('That seat has no model now. Choose one in Settings & APIs to retry.');return
        old=entry.get('step') or {}
        step=dict(fid=fid,channel=entry['channel'],job=old.get('job','answer'),round=old.get('round',1),
                  rounds=old.get('rounds',1),blind=old.get('blind',False),round_id=old.get('round_id'),
                  retry_of=entry_id)
        self.begin([step])

    def pause(self):
        self.paused=True;self.cancel_turn.set()
        self.status.set('Paused. An in-flight response can finish; further tools and model calls are stopped.')

    def advance(self):
        if self.busy or self.paused or self.closed:return
        if not self.pending:
            self.status.set('Round finished. Your turn, or invite another round.');self.active=None;self.draw();return
        step=self.pending.pop(0);fid,channel=step['fid'],step['channel']
        self.busy=True;self.active=fid;self.work_channel=channel;self.work_step=step
        self.cancel_turn=threading.Event();cancel=self.cancel_turn
        key=memory_key(channel,self.specs)
        memory=self.store.memories().get(key,'') if self.use_memory.get() else ''
        recap=self.session.get('recaps',{}).get(key,'')
        messages=self.conversation.messages(fid,channel,self.round_prompts[fid]+instruction(step),self.topic.get(),
            dict(self.labels,human=self.host.get()),memory=memory,recap=recap,audience_key=key,
            upto=step.get('retry_of'),blind_key=round_key(step) if step.get('blind') else None)
        spec=dict(self.specs[fid]);cfg=self.cfg
        tool_settings=dict(self.round_tools)
        def emit(event):self.results.put(('tool_event',step,event))
        def approve(proposal):
            request=dict(proposal=proposal,event=threading.Event(),approved=False,
                         name=self.labels[fid],channel=channel)
            emit(dict(phase='approval',request=request))
            until=time.monotonic()+300
            while not request['event'].wait(.1):
                if cancel.is_set() or time.monotonic()>until:
                    request['event'].set();return False
            return request['approved'] and not cancel.is_set()
        stage=('retry' if step.get('retry_of') else 'final verdict' if step['job']=='verdict'
               else f'round {step["round"]} of {step["rounds"]}' if step['rounds']>1 else
               'public room' if channel=='public' else 'private')
        self.status.set(f'{self.labels[fid]} is responding · {stage} · up to {provider_timeout(spec["provider"])}s')
        self.draw()
        def worker():
            try:
                tools=RoomTools(tool_settings,self.data_root,approve=approve,emit=emit,cancelled=cancel.is_set,cfg=cfg)
                ok,text=reply(cfg,spec,messages,tools=tools,cancelled=cancel.is_set)
            except Exception as exc:ok,text=False,'Chat worker failed: '+type(exc).__name__
            self.results.put(('reply',step,ok,text))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        if self.closed:return
        try:
            while True:
                item=self.results.get_nowait()
                if item[0]=='tool_event':
                    self.handle_tool_event(*item[1:]);continue
                if item[0]=='voice':
                    self.voice_result(*item[1:]);continue
                _,step,ok,text=item
                self.busy=False;self.active=None;self.work_step=None
                self.finish_reply(step,ok,text)
                self.advance()
        except queue.Empty:pass
        self.approval_requests=[r for r in self.approval_requests if not r['event'].is_set()]
        self.poll_id=self.after(50,self.poll)

    def finish_reply(self,step,ok,text):
        fid,channel=step['fid'],step['channel']
        text=text if ok else 'Response stopped / error: '+text
        index=self.conversation.index(step['retry_of']) if step.get('retry_of') else None
        if index is None:
            self.append(channel,fid,text,not ok,**self.step_metadata(step));return
        entry=self.conversation.entries[index]
        self.log_replay(dict(replaced=entry['id'],previous_text=entry['text'],previous_error=entry.get('error')))
        entry.update(text=text,error=not ok,time=datetime.now().isoformat(),
                     display_name=self.labels.get(fid,fid),retries=entry.get('retries',0)+1)
        self.log_replay(entry);self.persist_chat();self.refresh_transcript();self.draw()
        self.status.set(f'{self.labels[fid]} retried. The new reply replaced the old one in place.'
                        if ok else f'{self.labels[fid]} retry failed; the error is shown in place.')

    def handle_tool_event(self,step,event):
        fid,channel=step['fid'],step['channel']
        if event['phase']=='approval':
            from room_tool_dialogs import write_preview
            self.approval_requests.append(event['request'])
            self.toggle_transcript(True)
            self.status.set(f'{self.labels[fid]} is waiting for your file preview approval.')
            write_preview(self,event['request'])
        elif event['phase']=='start':
            self.status.set(f'{self.labels[fid]} · {event["tool"]} · {event["target"][:100]}')
        else:
            status='OK' if event['success'] else 'FAILED / DECLINED'
            output=event.get('output') or event.get('error','')
            detail=f'{event["tool"]} · {status}\n{event["target"]}\n{output}'
            if event.get('url'):detail+='\nSource: '+event['url']
            if event.get('truncated') or event.get('limited'):detail+='\n[Output limited; request a narrower excerpt.]'
            if event.get('backup_id'):detail+='\nBackup: tool_backups/'+event['backup_id']+'.bak'
            # A retry's tool activity is shown just before the reply it replaces.
            self.append(channel,'tool',detail,False,before=step.get('retry_of'),kind='tool',tool=event['tool'],
                        tool_success=event['success'],display_name=self.labels[fid]+' · Tool',owner=fid,
                        **self.step_metadata(step))

    def share(self):
        if self.channel=='public':return
        entries=[e for e in self.conversation.visible(self.channel) if e['speaker']==self.channel and not e['error']]
        if not entries:self.status.set('No private model reply to share yet.');return
        e=entries[-1]
        self.append('public','human',f"Shared from my private chat with {self.labels[self.channel]}:\n{e['text']}")
        self.switch('public');self.status.set('Reply shared publicly. Invite replies when ready.')

    def close(self):
        if not self.persist_chat():return
        self.closed=True;self.cancel_turn.set();self.pending.clear()
        if self.recorder is not None:
            try:self.recorder.stop()
            except Exception:pass
            self.recorder=None
        for request in self.approval_requests:
            if request.get('finish'):request['finish']()
            else:request['event'].set()
        # Cancel queued Tk callbacks before their widget command names are destroyed.
        for job in self.tk.splitlist(self.tk.call('after','info')):
            self.tk.call('after','cancel',job)
        self.destroy()


def run_app():
    RoomApp().mainloop()
