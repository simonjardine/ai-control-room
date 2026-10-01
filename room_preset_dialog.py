"""Edit the active preset: seat prompts, rounds, blind first round and verdict seat."""
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

import room_presets
from room import DEFAULT_PROMPT
from room_dialogs import window

NO_VERDICT = 'No verdict'


def preset_dialog(app):
    if not app.idle():
        app.status.set('Finish or pause the current round before editing presets.');return
    name = app.preset_name
    preset = app.presets[name]
    w = window(app, f'Preset · {name}', 900, 680)
    note = tk.StringVar(value='Changes apply from the next message. Each seat keeps its own prompt.')
    footer = ttk.Frame(w);footer.pack(side='bottom', fill='x', padx=12, pady=10)
    ttk.Label(footer, textvariable=note).pack(side='left')

    top = ttk.Frame(w);top.pack(fill='x', padx=12, pady=(12, 4))
    ttk.Label(top, text='Your name').pack(side='left')
    ttk.Entry(top, textvariable=app.host, width=16).pack(side='left', padx=(6, 18))
    ttk.Label(top, text='Rounds').pack(side='left')
    rounds = tk.IntVar(value=preset['rounds'])
    ttk.Spinbox(top, from_=1, to=room_presets.MAX_ROUNDS, textvariable=rounds, width=4,
                state='readonly').pack(side='left', padx=(6, 18))
    blind = tk.BooleanVar(value=preset['blind_first'])
    ttk.Checkbutton(top, text='First round blind (models answer independently)',
                    variable=blind).pack(side='left', padx=(0, 18))

    second = ttk.Frame(w);second.pack(fill='x', padx=12, pady=4)
    ttk.Label(second, text='Final verdict by').pack(side='left')
    seats = {f'{app.order.index(f)+1}. {app.labels[f]}': f for f in app.order}
    verdict = tk.StringVar(value=next((k for k, f in seats.items() if f == preset['verdict']), NO_VERDICT))
    ttk.Combobox(second, textvariable=verdict, values=[NO_VERDICT, *seats], state='readonly',
                 width=28).pack(side='left', padx=6)
    ttk.Label(second, text='The verdict seat speaks once, last, after the other rounds. '
              'It only applies in the public room.').pack(side='left', padx=8)

    tabs = ttk.Notebook(w);tabs.pack(fill='both', expand=True, padx=12, pady=8)
    editors = {}
    for f in app.order:
        text = scrolledtext.ScrolledText(tabs, wrap='word', font=('Segoe UI', 11))
        text.insert('1.0', app.prompts[f])
        tabs.add(text, text=f'{app.order.index(f)+1}. {app.labels[f]}');editors[f] = text

    def collect():
        return dict(prompts={f: t.get('1.0', 'end').strip() or DEFAULT_PROMPT for f, t in editors.items()},
                    rounds=int(rounds.get()), blind_first=bool(blind.get()),
                    verdict=seats.get(verdict.get()))

    def save():
        if app.save_preset(name, collect()):
            app.status.set(f'Preset “{name}” saved: {app.preset_summary()}.');w.destroy()
        else:note.set(app.status.get())

    def save_as():
        new = simpledialog.askstring('Save as new preset', 'Name for the new preset:', parent=w)
        new = (new or '').strip()[:60]
        if not new:return
        if new in app.presets and not messagebox.askyesno('Replace preset', f'Replace the preset “{new}”?', parent=w):
            return
        if app.save_preset(new, collect()):
            app.status.set(f'Preset “{new}” saved and active.');w.destroy()
        else:note.set(app.status.get())

    def reset():
        default = room_presets.builtin_default(name)
        rounds.set(default['rounds']);blind.set(default['blind_first'])
        verdict.set(next((k for k, f in seats.items() if f == default['verdict']), NO_VERDICT))
        for f, t in editors.items():
            t.delete('1.0', 'end');t.insert('1.0', default['prompts'][f])
        note.set('Built-in defaults restored in this editor. Save to keep them.')

    ttk.Button(footer, text='Save preset', command=save).pack(side='right')
    ttk.Button(footer, text='Save as new…', command=save_as).pack(side='right', padx=8)
    if room_presets.is_builtin(name):
        ttk.Button(footer, text='Reset to built-in', command=reset).pack(side='right')
    w.bind('<Control-s>', lambda e: save())
    return w
