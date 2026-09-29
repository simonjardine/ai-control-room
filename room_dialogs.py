"""Host controls for speaking order, saved chats, memory and recaps."""
import tkinter as tk
from tkinter import ttk, scrolledtext
from room import REVIEWER_PROMPT
from room_store import memory_key


def window(app,title,width=820,height=560):
    w=tk.Toplevel(app);w.title(title)
    w.geometry(f'{width}x{height}+{max(0,app.winfo_rootx()+35)}+{max(0,app.winfo_rooty()+35)}')
    w.minsize(620,400)
    return w


def order_dialog(app):
    w=window(app,'Speaking order & final reviewer',780,560)
    ttk.Label(w,text='Choose positions 1–6. Selecting a position swaps those two speakers.\nOnly checked participants reply. Changes apply to the next new round.',wraplength=730).pack(padx=16,pady=12)
    footer=ttk.Frame(w);footer.pack(side='bottom',fill='x',padx=16,pady=12)
    draft=list(app.order);vars={};prompt=None;reviewer=None
    note=tk.StringVar(value='Existing individual prompts stay unchanged unless you choose the reviewer preset.')
    ttk.Label(w,textvariable=note,wraplength=730).pack(padx=16,pady=10)
    table=ttk.Frame(w);table.pack(fill='x',padx=24,pady=12)
    def redraw():
        for f,v in vars.items():v.set(str(draft.index(f)+1))
    def move(f):
        old=draft.index(f);new=int(vars[f].get())-1
        draft[old],draft[new]=draft[new],draft[old];redraw()
    for f in app.fids:
        row=ttk.Frame(table);row.pack(fill='x',pady=5)
        v=tk.StringVar(value=str(draft.index(f)+1));vars[f]=v
        box=ttk.Combobox(row,textvariable=v,values=[str(n) for n in range(1,7)],width=4,state='readonly')
        box.pack(side='left',padx=(0,12));box.bind('<<ComboboxSelected>>',lambda e,f=f:move(f))
        ttk.Label(row,text=app.labels[f]+'  ·  '+app.specs[f]['model']).pack(side='left')
    def preset():
        nonlocal reviewer,prompt
        reviewer=next((f for f,s in app.specs.items() if 'gpt' in s['model'].lower()),None)
        if reviewer is None:note.set('No GPT model is currently assigned. Reorder any participant manually.');return
        draft.remove(reviewer);draft.append(reviewer);redraw();prompt=REVIEWER_PROMPT
        note.set('On Save, GPT moves last and its prompt becomes the reviewer prompt shown below. Other prompts are kept.')
        preview.configure(state='normal');preview.delete('1.0','end');preview.insert('1.0',prompt);preview.configure(state='disabled')
    ttk.Button(w,text='GPT last + reviewer preset',command=preset).pack(anchor='w',padx=24,pady=6)
    preview=scrolledtext.ScrolledText(w,height=5,wrap='word',font=('Segoe UI',10));preview.pack(fill='both',expand=True,padx=24,pady=6)
    preview.insert('1.0','Preset preview: review earlier replies, correct demonstrated errors, flag unsupported claims and offer a concise conclusion. No invented objections.');preview.configure(state='disabled')
    def save():
        prompts=dict(app.prompts)
        if reviewer and prompt:prompts[reviewer]=prompt
        try:app.save_preferences(prompts=prompts,order=draft)
        except OSError:note.set('Could not save. Your changes remain open.');return
        app.prompts=prompts;app.order=list(draft);app.refresh_identity()
        app.status.set('Speaking order saved. Applies to the next new round.');w.destroy()
    ttk.Button(footer,text='Cancel',command=w.destroy).pack(side='left')
    ttk.Button(footer,text='Save order & selected preset',command=save).pack(side='right')


def history_dialog(app):
    w=window(app,'Saved chats · Resume',940,600)
    ttk.Label(w,text='Resume a saved chat, including its public and separate private channels. No API call is made when opening a chat.').pack(padx=12,pady=12)
    footer=ttk.Frame(w);footer.pack(side='bottom',fill='x',padx=12,pady=10)
    note=tk.StringVar();ttk.Label(footer,textvariable=note).pack(side='left')
    tree=ttk.Treeview(w,columns=('date','title','count'),show='headings',height=15)
    for col,title,width in [('date','Updated',185),('title','Public topic / message',530),('count','Messages',85)]:
        tree.heading(col,text=title);tree.column(col,width=width)
    tree.pack(fill='both',expand=True,padx=12,pady=8)
    rows=app.store.history();lookup={}
    for r in rows:
        iid=tree.insert('','end',values=(r['updated'][:19],r['title'],r['count'] if r['count'] is not None else 'Legacy'))
        lookup[iid]=r
    def resume():
        selected=tree.selection()
        if not selected:note.set('Select a chat first.');return
        if app.busy or app.runtime_busy:note.set('Wait for the current request to finish.');return
        r=lookup[selected[0]]
        try:data=app.store.load(r['id']) if r['id'] else app.store.import_replay(r['path'])
        except (ValueError,OSError):note.set('Could not read that chat.');return
        if app.activate_chat(data):w.destroy()
    ttk.Button(footer,text='Resume selected chat',command=resume).pack(side='right')
    tree.bind('<Double-1>',lambda e:resume())


def memory_dialog(app):
    w=window(app,'Memory & chat recap',900,640)
    ttk.Label(w,text='Only notes you save here are remembered. Public notes go to the room; private notes go only to the named model.\nPrivate memory is tied to its current provider/model. Clearing a box and saving forgets that note. Nothing is inferred automatically.',wraplength=860).pack(padx=14,pady=12)
    footer=ttk.Frame(w);footer.pack(side='bottom',fill='x',padx=14,pady=10)
    note=tk.StringVar(value='Saved locally. These are host-edited notes, not verified facts.')
    ttk.Label(footer,textvariable=note).pack(side='left')
    tabs=ttk.Notebook(w);tabs.pack(fill='both',expand=True,padx=14,pady=8)
    editors={};sid=app.session['id'];binding=dict(app.specs)
    memories=app.store.memories()
    for channel in ['public']+app.fids:
        frame=ttk.Frame(tabs);tabs.add(frame,text='Public room' if channel=='public' else app.labels[channel])
        ttk.Label(frame,text='Remember across chats (included only when “Use saved memory” is checked)').pack(anchor='w',padx=8,pady=8)
        mem=scrolledtext.ScrolledText(frame,height=7,wrap='word',font=('Segoe UI',10));mem.pack(fill='both',expand=True,padx=8)
        key=memory_key(channel,binding);mem.insert('1.0',memories.get(key,''))
        ttk.Label(frame,text='Recap for this chat only (included when this chat is resumed; never copied into New chat)').pack(anchor='w',padx=8,pady=8)
        recap=scrolledtext.ScrolledText(frame,height=7,wrap='word',font=('Segoe UI',10));recap.pack(fill='both',expand=True,padx=8,pady=(0,8))
        recap.insert('1.0',app.session.get('recaps',{}).get(key,''));editors[channel]=(key,mem,recap)
    def save():
        if app.session['id']!=sid or app.specs!=binding:
            note.set('Chat/model changed. Reopen this editor before saving.');return
        memories=app.store.memories();recaps=dict(app.session.get('recaps',{}))
        for key,mem,recap in editors.values():
            value=mem.get('1.0','end').strip();summary=recap.get('1.0','end').strip()
            if len(value)>12000 or len(summary)>12000:note.set('Keep each note under 12,000 characters.');return
            if value:memories[key]=value
            else:memories.pop(key,None)
            if summary:recaps[key]=summary
            else:recaps.pop(key,None)
        try:app.store.save_memories(memories)
        except OSError:note.set('Could not save memory.');return
        app.session['recaps']=recaps
        if not app.persist_chat():note.set('Memory saved, but chat recap could not be saved.');return
        app.status.set('Memory and chat recap saved. Applied to future requests in the matching channel.');w.destroy()
    ttk.Button(footer,text='Save notes / forget cleared notes',command=save).pack(side='right')
