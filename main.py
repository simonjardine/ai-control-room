#!/usr/bin/env python3
"""Control room desktop entry point, including visible startup diagnostics for pythonw."""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path


def main() -> int:
    try:
        from room_gui import run_app
        run_app()
    except Exception as exc:
        # Desktop shortcuts use pythonw, which has no visible stderr console.
        try:
            from config import APP_DIR
        except Exception:
            APP_DIR=Path(__file__).resolve().parent
        log_path=APP_DIR/'crash.log'
        try:
            with log_path.open('a',encoding='utf-8') as log:
                log.write(f'\n[{datetime.now().isoformat()}] Control room startup/runtime failure\n')
                traceback.print_exc(file=log)
        except OSError:
            pass
        if sys.stderr is not None:
            traceback.print_exc()
        try:
            import tkinter as tk
            from tkinter import messagebox
            root=tk._default_root or tk.Tk()
            root.withdraw()
            messagebox.showerror('Control Room could not start',
                f'{type(exc).__name__}: the app could not stay open.\n\n'
                f'Details were recorded in:\n{log_path}',parent=root)
            root.destroy()
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
