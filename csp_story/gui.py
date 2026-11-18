"""Tkinter front end for the csp_story CLI (standard library only).

Run with:  python -m csp_story gui    (or: pythonw -m csp_story gui  for no console window)
"""
from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import sys
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

DEFAULT_SCRIPT = "script.txt"


class _LogWriter:
    """File-like object that appends to the log widget (used to capture print output)."""

    def __init__(self, app: "App", tag: str | None = None):
        self.app, self.tag = app, tag

    def write(self, s: str) -> int:
        self.app.log(s, self.tag, newline=False)
        return len(s)

    def flush(self) -> None:
        pass


class App(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=10)
        self.root = root
        root.title("CSP Story Tool")
        root.minsize(560, 380)
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        self.comic = tk.StringVar()
        self.script = tk.StringVar(value=os.path.join(os.getcwd(), DEFAULT_SCRIPT))
        self.dry_run = tk.BooleanVar(value=False)

        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)

        ttk.Label(self, text="Comic (.cmc / .clip):").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(self, textvariable=self.comic).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="Browse…", command=self.pick_comic).grid(row=0, column=2)

        ttk.Label(self, text="Script file:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(self, textvariable=self.script).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="Browse…", command=self.pick_script).grid(row=1, column=2)

        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 6))
        ttk.Button(bar, text="Export", command=self.do_export).pack(side="left")
        ttk.Button(bar, text="Import", command=self.do_import).pack(side="left", padx=6)
        ttk.Checkbutton(bar, text="Dry run (preview import only)",
                        variable=self.dry_run).pack(side="left", padx=6)
        ttk.Button(bar, text="Clear log", command=self.clear_log).pack(side="right")

        self.text = ScrolledText(self, height=16, wrap="word", state="disabled",
                                 font=("Consolas", 9))
        self.text.grid(row=3, column=0, columnspan=3, sticky="nsew")
        self.text.tag_configure("cmd", foreground="#2060c0")
        self.text.tag_configure("err", foreground="#c02020")
        self.text.tag_configure("link", foreground="#2060c0", underline=True)
        self.text.tag_bind("link", "<Enter>", lambda e: self.text.config(cursor="hand2"))
        self.text.tag_bind("link", "<Leave>", lambda e: self.text.config(cursor=""))
        self._links = 0

    # --- file pickers -------------------------------------------------------

    def pick_comic(self) -> None:
        path = filedialog.askopenfilename(
            title="Select comic",
            filetypes=[("Clip Studio comic", "*.cmc *.clip"), ("All files", "*.*")],
            initialdir=os.path.dirname(self.comic.get()) or os.getcwd())
        if path:
            self.comic.set(os.path.normpath(path))

    def pick_script(self) -> None:
        cur = self.script.get()
        # A save dialog lets the user pick an existing file or name a new one.
        path = filedialog.asksaveasfilename(
            title="Select script file",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
            defaultextension=".txt", confirmoverwrite=False,
            initialdir=os.path.dirname(cur) or os.getcwd(),
            initialfile=os.path.basename(cur) or DEFAULT_SCRIPT)
        if path:
            self.script.set(os.path.normpath(path))

    # --- actions ------------------------------------------------------------

    def do_export(self) -> None:
        comic, script = self.comic.get().strip(), self.script.get().strip()
        if not comic:
            messagebox.showwarning("Export", "Select a comic (.cmc or .clip) first.")
            return
        if not script:
            messagebox.showwarning("Export", "Select a script file first.")
            return
        if os.path.exists(script) and not messagebox.askyesno(
                "Export", f"{script} already exists.\n\nOverwrite it? Any edits in it "
                          "that haven't been imported will be lost."):
            return
        if self.run(["export", comic, "-o", script]) == 0:
            self.log_link("Open " + os.path.basename(script), os.path.abspath(script))
            self.log("")

    def do_import(self) -> None:
        script = self.script.get().strip()
        if not script or not os.path.exists(script):
            messagebox.showwarning("Import", "Script file not found. Export first.")
            return
        args = ["import", script] + (["--dry-run"] if self.dry_run.get() else [])
        if not self.dry_run.get() and not messagebox.askyesno(
                "Import", "Write the edited text into the comic's .clip files?\n\n"
                          "Make sure the comic is closed in Clip Studio."):
            return
        self.run(args)

    def run(self, argv: list[str]) -> int:
        from .__main__ import main  # same code path as the command line

        self.log("> csp_story " + " ".join(shlex.quote(a) for a in argv), "cmd")
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            with contextlib.redirect_stdout(_LogWriter(self)), \
                    contextlib.redirect_stderr(_LogWriter(self, "err")):
                code = main(argv)
        except SystemExit as e:  # argparse or explicit exits
            code = e.code if isinstance(e.code, int) else 1
            if e.code and not isinstance(e.code, int):
                self.log(str(e.code), "err")
        except Exception:
            code = 1
            self.log(traceback.format_exc(), "err")
        finally:
            self.root.config(cursor="")
        self.log("done" if not code else f"failed (exit code {code})", None if not code else "err")
        if code:
            self.log("")
        return code or 0

    # --- log ----------------------------------------------------------------

    def log(self, s: str, tag: str | None = None, newline: bool = True) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", s + ("\n" if newline else ""), tag or ())
        self.text.see("end")
        self.text.configure(state="disabled")
        self.text.update_idletasks()

    def log_link(self, label: str, path: str) -> None:
        """Append a clickable line that opens `path` in the default app."""
        self._links += 1
        tag = f"link{self._links}"
        self.text.configure(state="normal")
        self.text.insert("end", label, ("link", tag))
        self.text.insert("end", "\n")
        self.text.configure(state="disabled")
        self.text.tag_bind(tag, "<Button-1>", lambda e: self.open_path(path))
        self.text.see("end")

    def open_path(self, path: str) -> None:
        if not os.path.exists(path):
            messagebox.showwarning("Open", f"{path} no longer exists.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as e:
            messagebox.showerror("Open", f"Couldn't open {path}:\n{e}")

    def clear_log(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


def run_gui() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
