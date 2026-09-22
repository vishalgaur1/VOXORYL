#!/usr/bin/env python3
"""
VOXORYL Setup Wizard — guided first-run installer (Windows / macOS / Linux).

Pages: Welcome → License → Install location → Mode → Config/ENV → Progress → Done

  python scripts/setup_voxoryl.py
  scripts/Setup VOXORYL.bat          # Windows
  scripts/Setup VOXORYL.command      # macOS
"""

from __future__ import annotations

import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]

# Light aesthetic aligned with widget.css
BG = "#f7f9fc"
SURFACE = "#ffffff"
INK = "#1a2332"
MUTED = "#8a94a6"
ROYAL = "#4a8cff"
ROYAL_DEEP = "#3b6ef5"
LINE = "#e2e8f0"
SUCCESS = "#16a34a"
FONT_UI = ("Segoe UI", 10) if platform.system() == "Windows" else ("Helvetica", 11)
FONT_TITLE = ("Segoe UI Semibold", 18) if platform.system() == "Windows" else ("Helvetica", 18, "bold")
FONT_HEAD = ("Segoe UI Semibold", 13) if platform.system() == "Windows" else ("Helvetica", 13, "bold")
FONT_MONO = ("Consolas", 9) if platform.system() == "Windows" else ("Menlo", 10)

LICENSE_SHORT = """VOXORYL is dual-licensed:

• Non-commercial use — free under the PolyForm Noncommercial License 1.0.0.
  Personal, educational, research, hobby, and charitable use are allowed.

• Commercial use — selling VOXORYL, offering it as SaaS, embedding it in paid
  products/services, or using it for internal business purposes beyond personal/
  noncommercial use requires a separate paid commercial license.

Full text: LICENSE in the VOXORYL folder
Contact: vishalgaur2002@gmail.com

By continuing you agree to these terms for your intended use."""


def _user_data_hint() -> str:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(base) / "VOXORYL")
    if system == "Darwin":
        return str(Path.home() / "Library" / "Application Support" / "VOXORYL")
    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return str(Path(xdg).expanduser() / "voxoryl")
    return str(Path.home() / ".local" / "share" / "voxoryl")


def _venv_python(root: Path) -> Path:
    if platform.system() == "Windows":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def _find_python() -> str:
    if sys.version_info >= (3, 11):
        return sys.executable
    for cand in ("py", "python3", "python"):
        found = shutil.which(cand)
        if found:
            return found
    return sys.executable


class SetupWizard(tk.Tk):
    PAGES = (
        "welcome",
        "license",
        "location",
        "mode",
        "custom",
        "config",
        "progress",
        "done",
    )

    def __init__(self) -> None:
        super().__init__()
        self.title("VOXORYL Setup")
        self.geometry("720x560")
        self.minsize(640, 480)
        self.configure(bg=BG)
        self.resizable(True, True)

        self.app_dir = tk.StringVar(value=str(ROOT))
        self.mode = tk.StringVar(value="automatic")
        self.license_ok = tk.BooleanVar(value=False)
        self.comp_venv = tk.BooleanVar(value=True)
        self.comp_ollama = tk.BooleanVar(value=True)
        self.comp_models = tk.BooleanVar(value=True)
        self.chat_model = tk.StringVar(value="")
        self.fast_model = tk.StringVar(value="")
        self.vision_model = tk.StringVar(value="")
        self.embed_model = tk.StringVar(value="")
        self._env_vars: dict[str, tk.StringVar] = {}
        self._page = "welcome"
        self._install_ok = False
        self._log_q: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None

        self._build_chrome()
        self._pages: dict[str, tk.Frame] = {}
        self._build_pages()
        self._show("welcome")
        self.after(200, self._drain_log)

        try:
            self.iconbitmap(default=str(ROOT / "voxoryl" / "static" / "favicon.ico"))
        except Exception:
            pass

    def _build_chrome(self) -> None:
        header = tk.Frame(self, bg=SURFACE, highlightthickness=1, highlightbackground=LINE)
        header.pack(fill="x")
        tk.Label(
            header,
            text="VOXORYL",
            font=FONT_TITLE,
            fg=ROYAL_DEEP,
            bg=SURFACE,
        ).pack(side="left", padx=20, pady=(14, 2))
        self.subtitle = tk.Label(
            header,
            text="Setup Wizard",
            font=FONT_UI,
            fg=MUTED,
            bg=SURFACE,
        )
        self.subtitle.pack(side="left", padx=(0, 20), pady=(18, 6))

        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=20, pady=12)

        footer = tk.Frame(self, bg=BG)
        footer.pack(fill="x", padx=20, pady=(0, 16))
        self.back_btn = ttk.Button(footer, text="Back", command=self._back)
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(footer, text="Next", command=self._next)
        self.next_btn.pack(side="right")
        self.cancel_btn = ttk.Button(footer, text="Cancel", command=self._cancel)
        self.cancel_btn.pack(side="right", padx=(0, 8))

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=6)
        style.configure("Accent.TButton", foreground="#fff", background=ROYAL)
        style.map("Accent.TButton", background=[("active", ROYAL_DEEP)])

    def _card(self, parent: tk.Widget) -> tk.Frame:
        f = tk.Frame(parent, bg=SURFACE, highlightthickness=1, highlightbackground=LINE)
        f.pack(fill="both", expand=True)
        return f

    def _build_pages(self) -> None:
        self._pages["welcome"] = self._page_welcome()
        self._pages["license"] = self._page_license()
        self._pages["location"] = self._page_location()
        self._pages["mode"] = self._page_mode()
        self._pages["custom"] = self._page_custom()
        self._pages["config"] = self._page_config()
        self._pages["progress"] = self._page_progress()
        self._pages["done"] = self._page_done()

    def _page_welcome(self) -> tk.Frame:
        wrap = tk.Frame(self.body, bg=BG)
        card = self._card(wrap)
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(
            inner,
            text="Welcome to VOXORYL",
            font=FONT_HEAD,
            fg=INK,
            bg=SURFACE,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            inner,
            text=(
                "VOXORYL (vox-OR-ill) is a local voice assistant for your PC.\n"
                "It talks with you, remembers what you care about, and can help with\n"
                "everyday desktop tasks — with optional cloud brains when you add a free API key.\n\n"
                "This wizard sets up folders, detects your hardware, picks a fitting local\n"
                "model, and writes a private config. You can fill API keys now or later\n"
                "inside the app Settings."
            ),
            font=FONT_UI,
            fg=INK,
            bg=SURFACE,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(12, 0))
        tk.Label(
            inner,
            text="Voice Operating eXecutive · On-device Reasoning that Yields Local action",
            font=FONT_UI,
            fg=MUTED,
            bg=SURFACE,
            anchor="w",
            wraplength=600,
            justify="left",
        ).pack(fill="x", pady=(18, 0))
        return wrap

    def _page_license(self) -> tk.Frame:
        wrap = tk.Frame(self.body, bg=BG)
        card = self._card(wrap)
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(
            inner,
            text="License notice",
            font=FONT_HEAD,
            fg=INK,
            bg=SURFACE,
            anchor="w",
        ).pack(fill="x")
        text = tk.Text(
            inner,
            height=14,
            wrap="word",
            font=FONT_UI,
            bg="#fafbfd",
            fg=INK,
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            padx=10,
            pady=10,
        )
        text.pack(fill="both", expand=True, pady=(12, 8))
        text.insert("1.0", LICENSE_SHORT)
        text.configure(state="disabled")
        tk.Checkbutton(
            inner,
            text="I understand and accept these terms for my intended use",
            variable=self.license_ok,
            font=FONT_UI,
            bg=SURFACE,
            fg=INK,
            activebackground=SURFACE,
            selectcolor=SURFACE,
            command=self._refresh_nav,
        ).pack(anchor="w")
        return wrap

    def _page_location(self) -> tk.Frame:
        wrap = tk.Frame(self.body, bg=BG)
        card = self._card(wrap)
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(
            inner,
            text="Install location",
            font=FONT_HEAD,
            fg=INK,
            bg=SURFACE,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            inner,
            text=(
                "Application files stay in the folder you unzipped or cloned.\n"
                "Your memory, knowledge, reels, and secrets go to the OS user-data folder\n"
                "(not inside the zip/git folder). Deleting the app folder does not wipe that data."
            ),
            font=FONT_UI,
            fg=INK,
            bg=SURFACE,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(10, 16))

        tk.Label(inner, text="Application folder", font=FONT_UI, fg=MUTED, bg=SURFACE).pack(anchor="w")
        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x", pady=(4, 12))
        tk.Entry(row, textvariable=self.app_dir, font=FONT_UI).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self._browse_app).pack(side="left", padx=(8, 0))

        tk.Label(inner, text="User data (automatic)", font=FONT_UI, fg=MUTED, bg=SURFACE).pack(anchor="w")
        tk.Label(
            inner,
            text=_user_data_hint(),
            font=FONT_UI,
            fg=INK,
            bg="#fafbfd",
            anchor="w",
            padx=8,
            pady=6,
            highlightthickness=1,
            highlightbackground=LINE,
        ).pack(fill="x", pady=(4, 0))
        return wrap

    def _page_mode(self) -> tk.Frame:
        wrap = tk.Frame(self.body, bg=BG)
        card = self._card(wrap)
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(
            inner,
            text="Installation type",
            font=FONT_HEAD,
            fg=INK,
            bg=SURFACE,
            anchor="w",
        ).pack(fill="x")

        for value, title, desc in (
            (
                "automatic",
                "Automatic (recommended)",
                "Creates a Python environment, user-data folders, detects hardware,\n"
                "picks/pulls fitting Ollama models, and writes config — you do nothing.",
            ),
            (
                "custom",
                "Custom",
                "Choose which components to run and optionally override model tags.",
            ),
        ):
            fr = tk.Frame(inner, bg=SURFACE)
            fr.pack(fill="x", pady=(14, 0))
            tk.Radiobutton(
                fr,
                text=title,
                variable=self.mode,
                value=value,
                font=FONT_HEAD,
                bg=SURFACE,
                fg=INK,
                activebackground=SURFACE,
                selectcolor=SURFACE,
                anchor="w",
                command=self._refresh_nav,
            ).pack(anchor="w")
            tk.Label(
                fr,
                text=desc,
                font=FONT_UI,
                fg=MUTED,
                bg=SURFACE,
                justify="left",
                anchor="w",
            ).pack(anchor="w", padx=(24, 0))
        return wrap

    def _page_custom(self) -> tk.Frame:
        wrap = tk.Frame(self.body, bg=BG)
        card = self._card(wrap)
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(
            inner,
            text="Custom options",
            font=FONT_HEAD,
            fg=INK,
            bg=SURFACE,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            inner,
            text="Leave model fields blank to use the hardware-aware plan.",
            font=FONT_UI,
            fg=MUTED,
            bg=SURFACE,
            anchor="w",
        ).pack(fill="x", pady=(4, 12))

        tk.Label(inner, text="Components", font=FONT_UI, fg=MUTED, bg=SURFACE).pack(anchor="w")
        for var, label in (
            (self.comp_venv, "Create virtualenv & install Python packages"),
            (self.comp_ollama, "Detect / install Ollama (local models)"),
            (self.comp_models, "Pull recommended Ollama models"),
        ):
            tk.Checkbutton(
                inner,
                text=label,
                variable=var,
                font=FONT_UI,
                bg=SURFACE,
                fg=INK,
                activebackground=SURFACE,
                selectcolor=SURFACE,
            ).pack(anchor="w", pady=2)

        tk.Label(inner, text="Model overrides (Ollama tags)", font=FONT_UI, fg=MUTED, bg=SURFACE).pack(
            anchor="w", pady=(14, 4)
        )
        grid = tk.Frame(inner, bg=SURFACE)
        grid.pack(fill="x")
        for i, (label, var) in enumerate(
            (
                ("Chat / main", self.chat_model),
                ("Fast", self.fast_model),
                ("Vision", self.vision_model),
                ("Embed", self.embed_model),
            )
        ):
            tk.Label(grid, text=label, font=FONT_UI, fg=INK, bg=SURFACE, width=12, anchor="w").grid(
                row=i, column=0, sticky="w", pady=3
            )
            tk.Entry(grid, textvariable=var, font=FONT_UI).grid(row=i, column=1, sticky="ew", pady=3)
        grid.columnconfigure(1, weight=1)
        return wrap

    def _page_config(self) -> tk.Frame:
        wrap = tk.Frame(self.body, bg=BG)
        card = self._card(wrap)
        # Scrollable form
        canvas = tk.Canvas(card, bg=SURFACE, highlightthickness=0)
        scroll = ttk.Scrollbar(card, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=SURFACE)
        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        pad = tk.Frame(inner, bg=SURFACE)
        pad.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(
            pad,
            text="Optional configuration",
            font=FONT_HEAD,
            fg=INK,
            bg=SURFACE,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            pad,
            text=(
                "Fill what you have now, or leave blank and finish later in Settings → Config & API keys.\n"
                "Secrets are stored only in your OS user-data config.env — never in the git folder."
            ),
            font=FONT_UI,
            fg=MUTED,
            bg=SURFACE,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(4, 12))

        # Lazy-load schema when page first shown
        self._config_inner = pad
        self._config_built = False
        return wrap

    def _ensure_config_form(self) -> None:
        if self._config_built:
            return
        self._config_built = True
        try:
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from voxoryl.env_config import form_schema

            schema = form_schema(include_values=True, empty_only=False)
        except Exception as exc:
            tk.Label(
                self._config_inner,
                text=f"Could not load .env.example fields yet ({exc}).\n"
                "Continue — bootstrap will create config.env.",
                font=FONT_UI,
                fg=MUTED,
                bg=SURFACE,
                justify="left",
            ).pack(anchor="w")
            return

        groups: dict[str, list[dict[str, Any]]] = {}
        for field in schema.get("fields") or []:
            groups.setdefault(str(field.get("group") or "Other"), []).append(field)

        for group, fields in groups.items():
            tk.Label(
                self._config_inner,
                text=group,
                font=FONT_HEAD,
                fg=ROYAL_DEEP,
                bg=SURFACE,
                anchor="w",
            ).pack(fill="x", pady=(10, 4))
            for field in fields:
                key = str(field["key"])
                var = tk.StringVar(value=str(field.get("value") or ""))
                self._env_vars[key] = var
                row = tk.Frame(self._config_inner, bg=SURFACE)
                row.pack(fill="x", pady=2)
                tk.Label(
                    row,
                    text=str(field.get("label") or key),
                    font=FONT_UI,
                    fg=INK,
                    bg=SURFACE,
                    width=22,
                    anchor="w",
                ).pack(side="left")
                show = "*" if field.get("secret") else ""
                tk.Entry(row, textvariable=var, font=FONT_UI, show=show).pack(
                    side="left", fill="x", expand=True
                )
                hint = str(field.get("hint") or "")
                if hint:
                    tk.Label(
                        self._config_inner,
                        text=hint,
                        font=("Segoe UI", 8) if platform.system() == "Windows" else ("Helvetica", 9),
                        fg=MUTED,
                        bg=SURFACE,
                        anchor="w",
                    ).pack(fill="x", padx=(0, 0))

    def _page_progress(self) -> tk.Frame:
        wrap = tk.Frame(self.body, bg=BG)
        card = self._card(wrap)
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(
            inner,
            text="Installing…",
            font=FONT_HEAD,
            fg=INK,
            bg=SURFACE,
            anchor="w",
        ).pack(fill="x")
        self.progress_status = tk.Label(
            inner,
            text="Starting bootstrap…",
            font=FONT_UI,
            fg=MUTED,
            bg=SURFACE,
            anchor="w",
        )
        self.progress_status.pack(fill="x", pady=(6, 8))
        self.progress_bar = ttk.Progressbar(inner, mode="indeterminate")
        self.progress_bar.pack(fill="x", pady=(0, 8))
        self.log_text = tk.Text(
            inner,
            height=18,
            wrap="word",
            font=FONT_MONO,
            bg="#0f172a",
            fg="#e2e8f0",
            insertbackground="#e2e8f0",
            relief="flat",
            padx=8,
            pady=8,
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        return wrap

    def _page_done(self) -> tk.Frame:
        wrap = tk.Frame(self.body, bg=BG)
        card = self._card(wrap)
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=28, pady=24)
        self.done_title = tk.Label(
            inner,
            text="You're ready",
            font=FONT_HEAD,
            fg=SUCCESS,
            bg=SURFACE,
            anchor="w",
        )
        self.done_title.pack(fill="x")
        self.done_body = tk.Label(
            inner,
            text="",
            font=FONT_UI,
            fg=INK,
            bg=SURFACE,
            justify="left",
            anchor="w",
        )
        self.done_body.pack(fill="x", pady=(12, 0))
        return wrap

    def _show(self, name: str) -> None:
        for p in self._pages.values():
            p.pack_forget()
        self._page = name
        self._pages[name].pack(fill="both", expand=True)
        titles = {
            "welcome": "Welcome",
            "license": "License",
            "location": "Location",
            "mode": "Install type",
            "custom": "Custom options",
            "config": "Configuration",
            "progress": "Installing",
            "done": "Done",
        }
        self.subtitle.configure(text=f"Setup Wizard · {titles.get(name, name)}")
        if name == "config":
            self._ensure_config_form()
        if name == "progress":
            self._start_install()
        if name == "done":
            self._fill_done()
        self._refresh_nav()

    def _nav_sequence(self) -> list[str]:
        seq = ["welcome", "license", "location", "mode"]
        if self.mode.get() == "custom":
            seq.append("custom")
        seq.extend(["config", "progress", "done"])
        return seq

    def _refresh_nav(self) -> None:
        seq = self._nav_sequence()
        idx = seq.index(self._page) if self._page in seq else 0
        self.back_btn.configure(state="normal" if idx > 0 and self._page not in ("progress", "done") else "disabled")
        self.cancel_btn.configure(state="disabled" if self._page in ("progress", "done") else "normal")

        self.next_btn.configure(command=self._next)
        if self._page == "done":
            self.next_btn.configure(text="Launch VOXORYL", style="Accent.TButton", state="normal")
        elif self._page == "progress":
            self.next_btn.configure(text="Please wait…", state="disabled")
        elif self._page == "config":
            self.next_btn.configure(text="Install", style="Accent.TButton", state="normal")
        else:
            can = True
            if self._page == "license" and not self.license_ok.get():
                can = False
            self.next_btn.configure(text="Next", style="TButton", state="normal" if can else "disabled")

    def _back(self) -> None:
        seq = self._nav_sequence()
        if self._page not in seq:
            return
        idx = seq.index(self._page)
        if idx > 0:
            self._show(seq[idx - 1])

    def _next(self) -> None:
        if self._page == "done":
            self._launch_app()
            return
        if self._page == "license" and not self.license_ok.get():
            messagebox.showinfo("License", "Please accept the license notice to continue.")
            return
        if self._page == "location":
            path = Path(self.app_dir.get().strip() or str(ROOT))
            if not path.is_dir():
                messagebox.showerror("Location", "Choose an existing application folder.")
                return
        if self._page == "config":
            self._save_env_form()
        seq = self._nav_sequence()
        idx = seq.index(self._page)
        if idx + 1 < len(seq):
            self._show(seq[idx + 1])

    def _cancel(self) -> None:
        if messagebox.askyesno("Cancel setup", "Exit the Setup Wizard?"):
            self.destroy()

    def _browse_app(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.app_dir.get() or str(ROOT))
        if chosen:
            self.app_dir.set(chosen)

    def _save_env_form(self) -> None:
        updates = {k: v.get() for k, v in self._env_vars.items()}
        if not updates:
            return
        try:
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from voxoryl.env_config import patch_env_values

            # only write non-empty so skip leaves defaults from example
            patch_env_values(updates, only_nonempty=True)
        except Exception as exc:
            messagebox.showwarning(
                "Config",
                f"Could not save config yet ({exc}). Bootstrap will create config.env; "
                "you can fill keys later in Settings.",
            )

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        short = line.strip()
        if short:
            self.progress_status.configure(text=short[:100])

    def _drain_log(self) -> None:
        try:
            while True:
                line = self._log_q.get_nowait()
                self._append_log(line)
        except queue.Empty:
            pass
        self.after(200, self._drain_log)

    def _start_install(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._install_ok = False
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.progress_bar.start(12)
        self._worker = threading.Thread(target=self._run_bootstrap, daemon=True)
        self._worker.start()

    def _maybe_copy_app(self) -> Path:
        """If user picked a different folder, copy app tree (best-effort)."""
        dest = Path(self.app_dir.get().strip() or str(ROOT)).expanduser().resolve()
        src = ROOT.resolve()
        if dest == src:
            return src
        if not dest.exists():
            dest.mkdir(parents=True, exist_ok=True)
        # If dest already looks like VOXORYL (has voxoryl package), use it
        if (dest / "voxoryl").is_dir() and (dest / "scripts" / "bootstrap_voxoryl.py").is_file():
            self._log_q.put(f"[setup] Using existing app at {dest}")
            return dest
        self._log_q.put(f"[setup] Copying application files → {dest} …")
        skip_dirs = {
            ".venv",
            ".git",
            "__pycache__",
            "data",
            "node_modules",
            ".mypy_cache",
            ".pytest_cache",
        }
        allow_dot = {".env.example", ".gitignore"}
        for item in src.iterdir():
            if item.name in skip_dirs:
                continue
            if item.name.startswith(".") and item.name not in allow_dot:
                continue
            target = dest / item.name
            try:
                if item.is_dir():
                    if target.exists():
                        continue
                    shutil.copytree(
                        item,
                        target,
                        ignore=shutil.ignore_patterns(
                            ".venv", "__pycache__", "*.pyc", ".git", "data"
                        ),
                    )
                else:
                    if not target.exists():
                        shutil.copy2(item, target)
            except OSError as exc:
                self._log_q.put(f"[setup] skip {item.name}: {exc}")
        return dest

    def _run_bootstrap(self) -> None:
        try:
            app_root = self._maybe_copy_app()
            py = _find_python()
            script = app_root / "scripts" / "bootstrap_voxoryl.py"
            if not script.is_file():
                self._log_q.put(f"[setup] Missing {script}")
                self.after(0, lambda: self._install_finished(False))
                return

            cmd = [py, str(script), "--no-launch"]
            if self.mode.get() == "custom":
                if not self.comp_ollama.get():
                    cmd.append("--skip-ollama")
                if not self.comp_models.get():
                    cmd.append("--skip-models")
                if self.chat_model.get().strip():
                    cmd.extend(["--chat-model", self.chat_model.get().strip()])
                if self.fast_model.get().strip():
                    cmd.extend(["--fast-model", self.fast_model.get().strip()])
                if self.vision_model.get().strip():
                    cmd.extend(["--vision-model", self.vision_model.get().strip()])
                if self.embed_model.get().strip():
                    cmd.extend(["--embed-model", self.embed_model.get().strip()])
                if not self.comp_venv.get():
                    # Still need deps; warn and continue — bootstrap always ensures venv
                    self._log_q.put(
                        "[setup] Note: venv/deps step is required for first run; continuing with bootstrap."
                    )

            self._log_q.put(f"[setup] Running: {' '.join(cmd)}")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                cmd,
                cwd=str(app_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                self._log_q.put(line.rstrip("\n"))
            code = proc.wait()
            # Re-apply env form after bootstrap seeded config.env
            try:
                if str(ROOT) not in sys.path:
                    sys.path.insert(0, str(ROOT))
                from voxoryl.env_config import patch_env_values

                updates = {k: v.get() for k, v in self._env_vars.items()}
                if updates:
                    patch_env_values(updates, only_nonempty=True)
                    self._log_q.put("[setup] Wrote optional config fields into user config.env")
            except Exception as exc:
                self._log_q.put(f"[setup] Config re-apply skipped: {exc}")

            self.after(0, lambda: self._install_finished(code == 0))
        except Exception as exc:
            self._log_q.put(f"[setup] ERROR: {exc}")
            self.after(0, lambda: self._install_finished(False))

    def _install_finished(self, ok: bool) -> None:
        self._install_ok = ok
        self.progress_bar.stop()
        if ok:
            self.progress_status.configure(text="Install finished.")
            self._show("done")
        else:
            self.progress_status.configure(text="Install reported errors — see log.")
            self.next_btn.configure(
                text="Continue anyway",
                state="normal",
                style="TButton",
                command=lambda: self._show("done"),
            )

    def _fill_done(self) -> None:
        ud = _user_data_hint()
        if self._install_ok:
            self.done_title.configure(text="You're ready", fg=SUCCESS)
            self.done_body.configure(
                text=(
                    f"VOXORYL is set up.\n\n"
                    f"User data & config.env:\n{ud}\n\n"
                    "Click Launch to open the voice widget.\n"
                    "You can add or change API keys anytime in Settings → Config & API keys."
                )
            )
        else:
            self.done_title.configure(text="Setup finished with warnings", fg="#b45309")
            self.done_body.configure(
                text=(
                    "Something in bootstrap reported an error (often Ollama or a model pull).\n"
                    "You can still launch — use cloud keys or install Ollama later.\n\n"
                    f"User data folder:\n{ud}"
                )
            )

    def _launch_app(self) -> None:
        app_root = Path(self.app_dir.get().strip() or str(ROOT)).expanduser().resolve()
        if not (app_root / "scripts" / "launch_voxoryl.py").is_file():
            app_root = ROOT
        py = _venv_python(app_root)
        if not py.is_file():
            py = Path(_find_python())
        launch = app_root / "scripts" / "launch_voxoryl.py"
        args = [str(py), str(launch)]
        if platform.system() == "Windows":
            args.append("--native")
        else:
            args.append("--console")
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if platform.system() == "Windows" else 0
            subprocess.Popen(args, cwd=str(app_root), creationflags=flags)
        except TypeError:
            subprocess.Popen(args, cwd=str(app_root))
        except OSError as exc:
            messagebox.showerror("Launch", str(exc))
            return
        self.destroy()


def main() -> int:
    if sys.version_info < (3, 11):
        print("Python 3.11+ required for VOXORYL Setup.", file=sys.stderr)
        return 1
    # Prefer Tcl/Tk; fail with clear message
    try:
        app = SetupWizard()
    except tk.TclError as exc:
        print(f"Could not open Setup Wizard GUI ({exc}).", file=sys.stderr)
        print("Falling back to terminal bootstrap…", file=sys.stderr)
        script = ROOT / "scripts" / "bootstrap_voxoryl.py"
        return subprocess.call([sys.executable, str(script)], cwd=str(ROOT))
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
