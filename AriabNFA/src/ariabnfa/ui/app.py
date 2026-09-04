"""Main application window.

The threading contract is the important part of this file:

1. Network and document work runs on a daemon thread.
2. That thread NEVER touches a Tk widget. Tkinter is not thread-safe, and
   cross-thread widget calls fail intermittently and unreproducibly rather than
   raising cleanly - so this is enforced structurally, by giving the worker no
   widget references at all, not by remembering a convention.
3. The worker's only output is `(kind, payload)` messages on a Queue.
4. The main thread drains that queue from `after()`, and is the only place any
   widget is mutated.
5. Every worker path posts a terminal message, even on an unexpected exception.
   Without that, a crash in a daemon thread leaves the window stuck on a
   progress bar forever with no error.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ..ariba.discovery import dump_event
from ..config import DRAFTS_DIR, LOG_DIR, AppConfig
from ..errors import NFAError
from ..mapping.extract import assemble_nfa
from ..model import NFAData
from ..pipeline import fetch_nfa, generate_document, make_source, open_path
from ..secrets_store import SecretsStore
from .review_form import ReviewForm
from .settings_dialog import SettingsDialog
from .widgets import LogPane, ScrollableFrame

log = logging.getLogger(__name__)

AUTOSAVE_MS = 5000
POLL_MS = 100


class NFAApp(tk.Toplevel):
    """The main window.

    A Toplevel rather than a Tk root, so the process only ever has one Tcl
    interpreter. A second `Tk()` in the same process intermittently fails to
    load the ttk theme ("tk wasn't installed properly"), which is unpleasant in
    production and makes the window untestable alongside any other window.
    """

    def __init__(self, master, config: AppConfig, secrets: SecretsStore, *,
                 offline: bool = False):
        super().__init__(master)
        self.title("Ariba NFA Generator")
        self._size_to_screen(preferred=(1000, 780))

        self.config_obj = config
        self.secrets = secrets
        self.queue: queue.Queue = queue.Queue()
        self.data: NFAData | None = None
        self.last_document: Path | None = None
        self._busy = False

        self.offline = tk.BooleanVar(value=offline or not secrets.has_all())
        self.event_id = tk.StringVar()
        self.entity_name = tk.StringVar(value=(config.entity_names() or [""])[0])
        self.api_env = tk.StringVar(value=config.api_env)
        self.status = tk.StringVar(value="Ready.")

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._drain)
        self.after(AUTOSAVE_MS, self._autosave)

        if not secrets.has_all():
            self._log("No Ariba credentials saved — starting in offline mode. "
                      "Use Settings to connect.", "warn")

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #

    def _size_to_screen(self, preferred: tuple[int, int]) -> None:
        """Open at the preferred size, or the screen size if that is smaller.

        A fixed 1000x780 window does not fit a 1366x768 or 1280x720 laptop
        screen, and the footer - which holds Generate NFA - ends up below the
        taskbar where it cannot be clicked or scrolled to.
        """
        want_w, want_h = preferred
        max_w = self.winfo_screenwidth() - 60
        max_h = self.winfo_screenheight() - 90

        width, height = min(want_w, max_w), min(want_h, max_h)
        x = max((self.winfo_screenwidth() - width) // 2, 0)
        y = max((self.winfo_screenheight() - height) // 3, 0)

        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(820, max_w), min(520, max_h))
        #: Recorded because geometry() reports 1x1 until the window is mapped.
        self.fitted_size = (width, height)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_toolbar()
        self._build_form()
        self._build_status()
        self._build_footer()
        self._set_form_enabled(False)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(12, 10, 12, 6))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)

        ttk.Label(bar, text="Ariba event ID").grid(row=0, column=0, sticky="w", padx=(0, 8))
        entry = ttk.Entry(bar, textvariable=self.event_id)
        entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        entry.bind("<Return>", lambda _e: self.on_fetch())
        entry.focus_set()

        self.fetch_button = ttk.Button(bar, text="Fetch", command=self.on_fetch)
        self.fetch_button.grid(row=0, column=2)

        options = ttk.Frame(bar)
        options.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        ttk.Label(options, text="Letterhead").pack(side="left")
        ttk.Combobox(
            options, textvariable=self.entity_name, values=self.config_obj.entity_names(),
            state="readonly", width=34,
        ).pack(side="left", padx=(6, 16))

        ttk.Label(options, text="Environment").pack(side="left")
        ttk.Combobox(
            options, textvariable=self.api_env, values=["sandbox", "prod"],
            state="readonly", width=10,
        ).pack(side="left", padx=(6, 16))

        ttk.Checkbutton(
            options, text="Offline (use sample data)", variable=self.offline,
        ).pack(side="left")

    def _build_form(self) -> None:
        wrapper = ScrollableFrame(self)
        wrapper.grid(row=1, column=0, sticky="nsew", padx=12)
        self.scroller = wrapper

        self.form = ReviewForm(wrapper.body, padding=(0, 0, 12, 12))
        self.form.grid(sticky="ew")
        wrapper.body.columnconfigure(0, weight=1)

    def _build_status(self) -> None:
        frame = ttk.Frame(self, padding=(12, 6))
        frame.grid(row=2, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, textvariable=self.status).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=1, sticky="e")

        self.log_pane = LogPane(frame, height=6)
        self.log_pane.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def _build_footer(self) -> None:
        bar = ttk.Frame(self, padding=(12, 0, 12, 12))
        bar.grid(row=3, column=0, sticky="ew")
        bar.columnconfigure(3, weight=1)

        ttk.Button(bar, text="Settings…", command=self.on_settings).grid(row=0, column=0)
        ttk.Button(bar, text="Dump raw JSON…", command=self.on_dump).grid(row=0, column=1, padx=6)
        ttk.Button(bar, text="Open log folder", command=self.on_open_logs).grid(row=0, column=2)

        self.open_button = ttk.Button(
            bar, text="Open last document", command=self.on_open_last, state="disabled"
        )
        self.open_button.grid(row=0, column=4, padx=6)

        self.generate_button = ttk.Button(
            bar, text="Generate NFA", command=self.on_generate, state="disabled"
        )
        self.generate_button.grid(row=0, column=5)

    # ------------------------------------------------------------------ #
    # Main-thread helpers
    # ------------------------------------------------------------------ #

    def _log(self, message: str, tag: str | None = None) -> None:
        self.log_pane.append(message, tag)

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()
        if status:
            self.status.set(status)
        state = "disabled" if busy else "normal"
        self.fetch_button.configure(state=state)
        self.generate_button.configure(
            state="disabled" if (busy or self.data is None) else "normal"
        )

    def _set_form_enabled(self, enabled: bool) -> None:
        self.form.set_enabled(enabled)
        self.generate_button.configure(state="normal" if enabled else "disabled")

    def _start(self, target, *args) -> None:
        """Run `target` on a daemon thread. It gets no widget references."""
        if self._busy:
            return
        self._set_busy(True)
        threading.Thread(target=self._guard(target), args=args, daemon=True).start()

    def _guard(self, target):
        """Wrap a worker so it can never die silently."""
        def runner(*args):
            try:
                target(*args)
            except NFAError as exc:
                log.warning("%s", exc)
                self.queue.put(("error", exc.user_message))
            except Exception as exc:                     # noqa: BLE001
                log.exception("Unexpected worker failure")
                self.queue.put((
                    "error",
                    f"Unexpected error: {exc}\n\nThe full details are in the log file.",
                ))
        return runner

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def on_fetch(self) -> None:
        event_id = self.event_id.get().strip()
        if not event_id and not self.offline.get():
            messagebox.showwarning("Fetch", "Enter an Ariba event ID first.", parent=self)
            return

        self.config_obj.api_env = self.api_env.get()
        entity = self.config_obj.entity(self.entity_name.get())
        offline = bool(self.offline.get())

        self.log_pane.clear()
        self._log(f"Fetching {event_id or 'sample event'}…")
        self._start(self._worker_fetch, event_id, offline, entity)

    def _worker_fetch(self, event_id: str, offline: bool, entity) -> None:
        source = make_source(self.config_obj, offline=offline)
        self.queue.put(("log", (f"Source: {source.describe()}", None)))
        data = fetch_nfa(source, event_id, config=self.config_obj, entity=entity)
        self.queue.put(("fetched", data))

    def on_generate(self) -> None:
        if self.data is None:
            return
        edited = self.form.collect()
        if not edited["vendors"]:
            if not messagebox.askyesno(
                "Generate",
                "No vendors are listed, so the comparison table will be empty.\n\nContinue?",
                parent=self,
            ):
                return
        self._log("Building document…")
        self._start(self._worker_generate, edited, self.entity_name.get())

    def _worker_generate(self, edited: dict, entity_name: str) -> None:
        previous = self.data
        # Line items and the awarded total come from the fetch and are not
        # exposed by the review form, so they must be carried across the
        # re-assembly. Dropping them silently removed both item tables from the
        # document while every direct pipeline test still passed.
        data = assemble_nfa(
            entity=self.config_obj.entity(entity_name),
            subject=edited["subject"],
            grid_values=edited["grid_values"],
            all_vendors=edited["vendors"],
            line_items=previous.line_items if previous else [],
            awarded_total=previous.awarded_total if previous else None,
            justification=edited["justification"],
            doc_date=previous.doc_date if previous else None,
            config=self.config_obj,
            event_id=previous.event_id if previous else "",
        )
        path = generate_document(data, config=self.config_obj)
        self.queue.put(("generated", (data, path)))

    def on_dump(self) -> None:
        event_id = self.event_id.get().strip()
        if not event_id and not self.offline.get():
            messagebox.showwarning("Dump", "Enter an Ariba event ID first.", parent=self)
            return
        self._log("Dumping raw payload…")
        self._start(self._worker_dump, event_id, bool(self.offline.get()))

    def _worker_dump(self, event_id: str, offline: bool) -> None:
        source = make_source(self.config_obj, offline=offline)
        result = dump_event(source, event_id or "sample")
        self.queue.put(("dumped", result))

    def on_settings(self) -> None:
        dialog = SettingsDialog(self, self.config_obj, self.secrets)
        self.wait_window(dialog)
        if dialog.saved:
            self.api_env.set(self.config_obj.api_env)
            self._log("Settings saved.", "ok")
            if self.secrets.has_all():
                self.offline.set(False)

    def on_open_logs(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        open_path(LOG_DIR)

    def on_open_last(self) -> None:
        if self.last_document and self.last_document.exists():
            open_path(self.last_document)

    # ------------------------------------------------------------------ #
    # Queue draining - the only place widgets are touched after startup
    # ------------------------------------------------------------------ #

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                handler = getattr(self, f"_on_{kind}", None)
                if handler:
                    handler(payload)
                else:
                    log.debug("Unhandled message kind %r", kind)
        except queue.Empty:
            pass
        self.after(POLL_MS, self._drain)

    def _on_log(self, payload) -> None:
        message, tag = payload if isinstance(payload, tuple) else (payload, None)
        self._log(message, tag)

    def _on_status(self, text: str) -> None:
        self.status.set(text)

    def _on_error(self, message: str) -> None:
        self._set_busy(False, "Failed.")
        self._log(message.splitlines()[0], "error")
        messagebox.showerror("NFA Generator", message, parent=self)

    def _on_fetched(self, data: NFAData) -> None:
        self.data = data
        self.form.load(data)
        self._set_form_enabled(True)
        self.scroller.scroll_to_top()

        unresolved = len(data.report.unresolved)
        self._log(
            f"Loaded {data.event_id or 'event'}: {len(data.vendors)} ranked vendor(s), "
            f"{unresolved} field(s) needing input.",
            "ok" if not unresolved else "warn",
        )
        for note in data.report.notes:
            self._log(f"Note: {note}", "warn")

        self._offer_draft(data)
        self._set_busy(False, "Review the details, then Generate NFA.")

    def _on_generated(self, payload) -> None:
        data, path = payload
        self.data = data
        self.last_document = path
        self.open_button.configure(state="normal")
        self._set_busy(False, f"Saved to {path}")
        self._log(f"Saved: {path}", "ok")

        missing = [r.label for r in data.grid if r.missing]
        if missing:
            self._log(
                f"{len(missing)} field(s) printed as placeholders: {', '.join(missing[:4])}"
                + ("…" if len(missing) > 4 else ""),
                "warn",
            )
        self._save_draft()
        if self.config_obj.open_in_word:
            open_path(path)

    def _on_dumped(self, result) -> None:
        self._set_busy(False, "Dump complete.")
        self._log(f"{result.row_count} distinct paths -> {result.csv_path}", "ok")
        for name, error in (result.errors or {}).items():
            self._log(f"No {name} data: {error}", "warn")
        if messagebox.askyesno(
            "Dump complete",
            f"Wrote {result.row_count} distinct field paths.\n\n"
            f"{result.csv_path}\n\nOpen the folder now?",
            parent=self,
        ):
            open_path(result.directory)

    # ------------------------------------------------------------------ #
    # Draft persistence
    # ------------------------------------------------------------------ #

    def _draft_path(self) -> Path | None:
        if not self.data:
            return None
        key = "".join(ch if ch.isalnum() else "_" for ch in (self.data.event_id or "event"))
        return DRAFTS_DIR / f"{key}.json"

    def _save_draft(self) -> None:
        path = self._draft_path()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.form.state_dict(), indent=2), encoding="utf-8")
        except OSError as exc:
            log.debug("Could not save draft: %s", exc)

    def _offer_draft(self, data: NFAData) -> None:
        path = self._draft_path()
        if not path or not path.exists():
            return
        if not messagebox.askyesno(
            "Restore draft",
            f"A saved draft exists for {data.event_id}.\n\n"
            "Restore your previous edits over the data just fetched from Ariba?",
            parent=self,
        ):
            return
        try:
            self.form.apply_state(json.loads(path.read_text(encoding="utf-8")))
            self._log("Restored saved draft.", "ok")
        except (OSError, json.JSONDecodeError) as exc:
            self._log(f"Could not read the saved draft: {exc}", "warn")

    def _autosave(self) -> None:
        if self.data and not self._busy:
            self._save_draft()
        self.after(AUTOSAVE_MS, self._autosave)

    def _on_close(self) -> None:
        self._save_draft()
        master = self.master
        self.destroy()
        # Closing the main window ends the program, so tear the root down too.
        if master is not None and getattr(master, "_ariabnfa_owns_root", False):
            master.destroy()


def run(config: AppConfig, secrets: SecretsStore, *, offline: bool = False) -> None:
    root = tk.Tk()
    root.withdraw()
    root._ariabnfa_owns_root = True
    NFAApp(root, config, secrets, offline=offline)
    root.mainloop()
