"""Host controls and concrete write previews; all widgets stay on the Tk thread."""
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk

from room_tools import ToolError, validate_workspace


def tools_dialog(app):
    existing = getattr(app, 'tools_window', None)
    if existing and existing.winfo_exists():
        existing.lift()
        return
    win = app.tools_window = tk.Toplevel(app)
    win.title('Files & internet')
    win.geometry(f'800x580+{max(0,app.winfo_rootx()+45)}+{max(0,app.winfo_rooty()+45)}')
    win.minsize(700,540)
    body = ttk.Frame(win, padding=20)
    body.pack(fill='both', expand=True)
    ttk.Label(body, text='Give the room a workspace', font=('Segoe UI', 15, 'bold')).pack(anchor='w')
    ttk.Label(body, text='These controls apply to all six models. Changes apply to the next new round.',
              wraplength=740).pack(anchor='w', pady=(8,18))
    workspace = tk.StringVar(value=app.tool_settings['workspace'])
    row = ttk.Frame(body)
    row.pack(fill='x')
    ttk.Entry(row, textvariable=workspace).pack(side='left', fill='x', expand=True)

    def browse():
        chosen = filedialog.askdirectory(parent=win, title='Choose the room workspace',
                                         initialdir=workspace.get(), mustexist=True)
        if chosen:
            workspace.set(chosen)

    ttk.Button(row, text='Choose folder…', command=browse).pack(side='right', padx=(10,0))
    values = {key: tk.BooleanVar(value=app.tool_settings[key]) for key in ('read','write','web')}
    for key, label in [('read','Read files: list, read and search text in this folder'),
                       ('write','Write files: preview and approve each change'),
                       ('web','Internet: search via OpenAI and read public webpages')]:
        ttk.Checkbutton(body, text=label, variable=values[key]).pack(anchor='w', pady=(15,0))
    ttk.Label(body, text='File excerpts are sent to the responding model’s provider. Public tool results appear '
              'in the room; private results stay in that conversation. The workspace itself is shared by all seats.\n\n'
              'Search uses your configured OpenAI API key (GPT-4.1 mini + web search); normal API charges apply. '
              'Direct webpage reading needs no search key.\n\n'
              'Existing files are backed up before approved changes. Credentials and app chat/settings files '
              'are excluded. These tools do not run programs.', wraplength=730).pack(anchor='w', pady=20)
    note = tk.StringVar()
    ttk.Label(body, textvariable=note, wraplength=730).pack(anchor='w')
    footer = ttk.Frame(body)
    footer.pack(side='bottom', fill='x', pady=(12,0))
    ttk.Button(footer, text='Cancel', command=win.destroy).pack(side='left')

    def save():
        if app.busy or app.runtime_busy or app.pending:
            note.set('Wait for the current round to finish before changing tool access.')
            return
        try:
            root = validate_workspace(workspace.get())
            settings = dict(workspace=str(root), **{k:v.get() for k,v in values.items()})
            app.save_preferences(tools=settings)
        except (ToolError, OSError, ValueError) as exc:
            note.set(str(exc) if isinstance(exc, ToolError) else 'Could not use or save that folder.')
            return
        app.tool_settings = settings
        app.refresh_tool_status()
        app.status.set('File and internet controls saved. They apply to the next new round.')
        win.destroy()

    ttk.Button(footer, text='Save tool settings', command=save).pack(side='right')


def write_preview(app, request):
    proposal = request['proposal']
    if request['event'].is_set() or app.closed or app.cancel_turn.is_set():
        request['event'].set()
        return
    win = tk.Toplevel(app)
    win.title(f'Approve file change · {request["name"]}')
    win.geometry(f'900x660+{max(0,app.winfo_rootx()+55)}+{max(0,app.winfo_rooty()+55)}')
    win.minsize(650,460)
    request['window'] = win
    audience = 'PUBLIC ROOM' if request['channel'] == 'public' else 'PRIVATE CHAT'
    ttk.Label(win, text=f'{request["name"]} · {audience}', font=('Segoe UI', 12, 'bold')).pack(anchor='w',padx=18,pady=(14,6))
    verb = 'Replace' if proposal['exists'] else 'Create'
    ttk.Label(win, text=f'{verb}: {Path(proposal["workspace"])/proposal["path"]}\n'
              f'{proposal["bytes"]:,} bytes · Existing file will be backed up before saving.',
              wraplength=850).pack(anchor='w',padx=18,pady=6)
    footer=ttk.Frame(win)
    footer.pack(side='bottom',fill='x',padx=18,pady=14)
    tabs=ttk.Notebook(win)
    tabs.pack(fill='both',expand=True,padx=18,pady=8)
    for title, text in [('Changes',proposal['diff'] or '(New empty file)'), ('Full new contents',proposal['content'])]:
        editor=scrolledtext.ScrolledText(tabs,wrap='none',font=('Consolas',10))
        editor.insert('1.0',text)
        editor.configure(state='disabled')
        tabs.add(editor,text=title)

    check_id=None
    def finish(approved=False):
        nonlocal check_id
        if check_id:
            win.after_cancel(check_id);check_id=None
        if not request['event'].is_set():
            request['approved'] = bool(approved and not app.cancel_turn.is_set())
            request['event'].set()
        if win.winfo_exists():
            win.destroy()

    request['finish'] = finish
    ttk.Button(footer,text='Decline',command=finish).pack(side='left')
    ttk.Button(footer,text='Approve this file change',command=lambda:finish(True)).pack(side='right')
    win.protocol('WM_DELETE_WINDOW',finish)
    win.bind('<Escape>',lambda event:finish())
    # Non-modal: Pause and the rest of the room remain responsive during review.
    def check():
        nonlocal check_id
        check_id=None
        if request['event'].is_set() or app.cancel_turn.is_set():
            finish()
        elif win.winfo_exists():
            check_id=win.after(100,check)
    check_id=win.after(100,check)
    win.lift()
