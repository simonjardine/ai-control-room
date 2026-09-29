"""Independent room model/API settings and Hermes router controls."""
import copy
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from urllib.parse import urlsplit
from config import save_config
from providers import list_models, resolve_hermes_local_endpoint, provider_timeout, _request, _headers
from room import DEFAULT_PROMPT


def local_models(cfg, action=None, model=None):
    local = cfg.get('providers',{}).get('local',{})
    base = local.get('base_url','http://127.0.0.1:18434/v1').rstrip('/')
    if urlsplit(base).hostname not in ('localhost','127.0.0.1','::1'):
        return False,'Local controls require a loopback endpoint.',[]
    key = local.get('api_key','')
    hermes_base, hermes_key = resolve_hermes_local_endpoint()
    if not key and base==hermes_base:key=hermes_key
    root=base.removesuffix('/v1')
    try:
        if action:
            if action not in ('load','unload') or not model:return False,'Choose a registered model.',[]
            resp=_request('POST',root+'/models/'+action,headers=_headers(key,'local'),json={'model':model},timeout=provider_timeout('local'))
            if resp.status_code>=400:return False,f'{action} failed: HTTP {resp.status_code}',[]
            if resp.json().get('success') is not True:return False,f'{action} not confirmed by server.',[]
        resp=_request('GET',root+'/models',headers=_headers(key,'local'),timeout=provider_timeout('local'))
        if resp.status_code>=400:return False,f'Hermes returned HTTP {resp.status_code}',[]
        rows=[dict(id=r['id'],status=r.get('status',{}).get('value','unknown')) for r in resp.json().get('data',[]) if r.get('id')]
        return True,('Request completed. ' if action else '')+f'{len(rows)} registered local models.',rows
    except Exception as exc:
        return False,f'Hermes connection failed ({type(exc).__name__}). Refresh to check state; ensure Hermes is running.',[]


class Settings(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.title('Control Room · Settings & APIs')
        self.geometry(f'940x720+{max(0,app.winfo_rootx()+40)}+{max(0,app.winfo_rooty()+40)}')
        self.cfg=copy.deepcopy(app.cfg);self.jobs=queue.Queue();self.dead=False
        self.provider_vars={};self.seats={};self.catalog={};self.local_busy=False
        self.note=tk.StringVar(value='Configure one or more models. Leave unused model fields blank. Save applies while the room is idle.')
        ttk.Label(self,textvariable=self.note,wraplength=890).pack(fill='x',padx=16,pady=12)
        ttk.Button(self,text='Save settings & prompts',command=self.save).pack(side='bottom',pady=12)
        nb=ttk.Notebook(self);nb.pack(fill='both',expand=True,padx=14,pady=5)
        models=ttk.Frame(nb);apis=ttk.Frame(nb);local=ttk.Frame(nb)
        nb.add(models,text='Models & individual prompts');nb.add(apis,text='APIs');nb.add(local,text='Local LLM · Load / unload')
        ttk.Button(models,text='Refresh model lists (all providers)',command=self.refresh_all).pack(anchor='w',padx=10,pady=8)
        seats=ttk.Notebook(models);seats.pack(fill='both',expand=True,padx=10,pady=8)
        for fid,spec in app.specs.items():
            frame=ttk.Frame(seats);seats.add(frame,text=app.labels[fid])
            provider=tk.StringVar(value=spec['provider']);model=tk.StringVar(value=spec['model'])
            row=ttk.Frame(frame);row.pack(fill='x',padx=10,pady=10)
            ttk.Label(row,text='Provider').pack(side='left')
            pc=ttk.Combobox(row,textvariable=provider,values=list(self.cfg.get('providers',{})),state='readonly',width=15);pc.pack(side='left',padx=8)
            ttk.Button(row,text='Refresh models',command=lambda p=provider:self.refresh(p.get())).pack(side='left')
            mc=ttk.Combobox(frame,textvariable=model,values=[spec['model']],width=80);mc.pack(fill='x',padx=10)
            ttk.Label(frame,text='Individual system prompt (this participant only)').pack(anchor='w',padx=10,pady=(15,5))
            prompt=scrolledtext.ScrolledText(frame,wrap='word',font=('Segoe UI',11));prompt.pack(fill='both',expand=True,padx=10,pady=(0,10));prompt.insert('1.0',app.prompts[fid])
            self.seats[fid]=dict(provider=provider,model=model,combo=mc,prompt=prompt)
            pc.bind('<<ComboboxSelected>>',lambda e,f=fid:self.provider_changed(f))
        for p,spec in self.cfg.get('providers',{}).items():
            box=ttk.LabelFrame(apis,text=p,padding=6);box.pack(fill='x',padx=10,pady=4)
            base=tk.StringVar(value=spec.get('base_url',''));key=tk.StringVar(value=spec.get('api_key',''));status=tk.StringVar()
            ttk.Label(box,text='URL').grid(row=0,column=0);ttk.Entry(box,textvariable=base,width=65).grid(row=0,column=1,sticky='ew')
            ttk.Label(box,text='API key').grid(row=1,column=0);ttk.Entry(box,textvariable=key,show='*',width=65).grid(row=1,column=1,sticky='ew')
            ttk.Button(box,text='Test / refresh',command=lambda p=p:self.refresh(p)).grid(row=0,column=2,padx=6)
            ttk.Label(box,textvariable=status,wraplength=190).grid(row=1,column=2);box.columnconfigure(1,weight=1)
            self.provider_vars[p]=dict(base=base,key=key,status=status)
        ttk.Button(apis,text='Refresh all APIs',command=self.refresh_all).pack(anchor='w',padx=10,pady=6)
        ttk.Label(local,text='Hermes local LLMs · http://127.0.0.1:18434/v1\nLoad uses RAM/VRAM. Unload frees model memory; it does not delete the GGUF.\nChoose a local model here to manage memory; assign it to a seat in the Models tab.',wraplength=860).pack(anchor='w',padx=10,pady=12)
        self.tree=ttk.Treeview(local,columns=('model','state'),show='headings',height=12)
        self.tree.heading('model',text='Local LLM');self.tree.heading('state',text='Runtime state');self.tree.column('model',width=660);self.tree.column('state',width=120)
        self.tree.pack(fill='both',expand=True,padx=10,pady=8)
        row=ttk.Frame(local);row.pack(fill='x',padx=10,pady=10)
        ttk.Button(row,text='Refresh local models',command=self.refresh_local).pack(side='left')
        ttk.Button(row,text='Load selected LLM',command=lambda:self.manage('load')).pack(side='left',padx=10)
        ttk.Button(row,text='Unload selected LLM',command=lambda:self.manage('unload')).pack(side='left')
        self.local_status=tk.StringVar(value='Refresh to see loaded / unloaded models.')
        ttk.Label(local,textvariable=self.local_status,wraplength=860).pack(fill='x',padx=10,pady=10)
        self.protocol('WM_DELETE_WINDOW',self.close);self.timer=self.after(50,self.poll)

    def draft(self):
        cfg=copy.deepcopy(self.cfg)
        for p,v in self.provider_vars.items():
            cfg['providers'][p]['base_url']=v['base'].get().strip()
            cfg['providers'][p]['api_key']=v['key'].get()
        return cfg

    def job(self,fn,done):
        def work():
            try:r=fn()
            except Exception:r=(False,'Request failed.',[])
            self.jobs.put((done,r))
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        if self.dead:return
        try:
            while True:
                done,r=self.jobs.get_nowait();done(r)
        except queue.Empty:pass
        self.timer=self.after(50,self.poll)

    def provider_changed(self,f):
        s=self.seats[f];s['model'].set('');s['combo']['values']=self.catalog.get(s['provider'].get(),[])
        self.refresh(s['provider'].get())

    def refresh(self,p):
        cfg=self.draft();self.provider_vars[p]['status'].set('Refreshing…')
        def done(result):
            ok,msg,models=result
            self.provider_vars[p]['status'].set(('OK: ' if ok else 'Failed: ')+msg[:90])
            self.catalog[p]=models
            for s in self.seats.values():
                if s['provider'].get()==p:s['combo']['values']=models
            self.note.set(f'{p}: {len(models)} model choices refreshed.' if ok else f'{p}: refresh failed; current selections preserved.')
        self.job(lambda:list_models(p,cfg),done)

    def refresh_all(self):
        for p in self.provider_vars:self.refresh(p)
        self.refresh_local()

    def display_local(self,result):
        ok,msg,rows=result;self.local_status.set(msg)
        if not ok:return
        selected=self.tree.selection();old=self.tree.item(selected[0],'values')[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            iid=self.tree.insert('','end',values=(r['id'],r['status']))
            if r['id']==old:self.tree.selection_set(iid)
        self.catalog['local']=[r['id'] for r in rows]
        for s in self.seats.values():
            if s['provider'].get()=='local':s['combo']['values']=self.catalog['local']

    def refresh_local(self):
        if self.local_busy:return
        cfg=self.draft();self.local_status.set('Refreshing Hermes…')
        self.job(lambda:local_models(cfg),self.display_local)

    def manage(self,action):
        if self.app.busy or self.app.pending:
            self.local_status.set('Finish the current chat round before loading or unloading.');return
        if self.local_busy:return
        selection=self.tree.selection()
        if not selection:self.local_status.set('Select a model in the list first.');return
        model=self.tree.item(selection[0],'values')[0];cfg=self.draft();self.local_busy=True
        self.app.runtime_busy=True;self.local_status.set(action.title()+' request in progress…')
        def done(result):
            self.local_busy=False;self.app.runtime_busy=False;self.display_local(result)
            if action=='unload' and result[0]:
                for f,s in self.app.specs.items():
                    if s['provider']=='local' and s['model']==model:self.app.select[f].set(False)
                self.app.status.set('Local LLM unloaded. Its public-reply checkbox is off; sending to it may load it again.')
        self.job(lambda:local_models(cfg,action,model),done)

    def save(self):
        if self.app.busy or self.app.pending or self.local_busy:
            self.note.set('Finish the reply round or runtime operation before saving.');return
        assignments={f:dict(provider=s['provider'].get(),model=s['model'].get().strip()) for f,s in self.seats.items()}
        cfg=self.draft();cfg.setdefault('room',{})['participants']=assignments
        prompts={f:s['prompt'].get('1.0','end').strip() or DEFAULT_PROMPT for f,s in self.seats.items()}
        try:
            save_config(cfg,self.app.data_root/'config.json')
            self.app.save_preferences(prompts=prompts)
        except OSError:self.note.set('Could not save settings.');return
        for f,spec in assignments.items():
            if not spec['model']:self.app.select[f].set(False)
            elif not self.app.specs[f]['model']:self.app.select[f].set(True)
        self.cfg=cfg;self.app.cfg=cfg;self.app.specs=assignments;self.app.prompts=prompts
        self.app.refresh_identity();self.note.set('Saved. Models and prompts are active now.');self.app.status.set('Model assignments and individual prompts updated.')

    def close(self):
        if self.local_busy:self.note.set('Wait for the local model operation to finish before closing.');return
        self.dead=True;self.after_cancel(self.timer);self.destroy()
