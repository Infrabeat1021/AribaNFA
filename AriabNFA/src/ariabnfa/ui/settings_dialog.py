"""Settings: connection details, commercial conventions, and credentials.

Secrets are written to Windows Credential Manager, never to config.json. If the
Credential Manager backend is unavailable the dialog says so plainly rather than
silently falling back to a file on disk.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..config import AppConfig, normalise_api_path, normalise_host
from ..errors import NFAError
from ..secrets_store import (
    KEY_API_KEY,
    KEY_BASIC,
    KEY_CLIENT_ID,
    KEY_CLIENT_SECRET,
    SecretsStore,
    encode_basic,
)
from .widgets import ScrollableFrame, labelled_entry

log = logging.getLogger(__name__)

MASK = "•"


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, config: AppConfig, secrets: SecretsStore):
        super().__init__(parent)
        self.title("Settings")
        self.transient(parent)
        self.resizable(True, True)

        self.config_obj = config
        self.secrets = secrets
        self.saved = False
        self._queue: queue.Queue = queue.Queue()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # The settings content is taller than a 720p screen, so it scrolls, and
        # the buttons live outside the scroll area. Save must always be
        # reachable - a dialog you cannot save is worse than no dialog.
        self._scroller = ScrollableFrame(self)
        self._scroller.grid(row=0, column=0, sticky="nsew")
        self._scroller.body.columnconfigure(0, weight=1)

        container = ttk.Frame(self._scroller.body, padding=14)
        container.grid(sticky="nsew")
        container.columnconfigure(0, weight=1)

        self._build_connection(container)
        self._build_credentials(container)
        self._build_commercial(container)
        self._build_output(container)

        ttk.Separator(self, orient="horizontal").grid(row=1, column=0, sticky="ew")
        self._footer = ttk.Frame(self, padding=(14, 10))
        self._footer.grid(row=2, column=0, sticky="ew")
        self._footer.columnconfigure(0, weight=1)
        self._build_buttons(self._footer)

        self._load()
        self._fit_to_screen()
        self.grab_set()
        self.after(100, self._drain)

    def _fit_to_screen(self) -> None:
        """Size the dialog to its content, but never larger than the screen."""
        self.update_idletasks()
        margin_w, margin_h = 80, 120        # leaves room for taskbar and borders
        max_w = self.winfo_screenwidth() - margin_w
        max_h = self.winfo_screenheight() - margin_h

        # The scrolling canvas has no natural size, so it is told what its
        # content needs before the window measures itself. Without this the
        # dialog collapses to a few hundred pixels and everything scrolls.
        scrollbar_width = self._scroller.scrollbar.winfo_reqwidth() + 4
        footer_height = self._footer.winfo_reqheight() + 8
        self._scroller.fit_to_content(
            max_width=max_w - scrollbar_width,
            max_height=max_h - footer_height,
        )
        self.update_idletasks()

        width = min(self.winfo_reqwidth(), max_w)
        height = min(self.winfo_reqheight(), max_h)
        x = max((self.winfo_screenwidth() - width) // 2, 0)
        y = max((self.winfo_screenheight() - height) // 3, 0)

        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(520, max_w), min(360, max_h))
        #: Recorded because geometry() reports 1x1 until the window is mapped.
        self.fitted_size = (width, height)

    # ------------------------------------------------------------------ #

    def _group(self, parent, title: str) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text=title, padding=10)
        frame.grid(sticky="ew", pady=(0, 10))
        frame.columnconfigure(1, weight=1)
        return frame

    def _build_connection(self, parent) -> None:
        frame = self._group(parent, "Ariba connection")
        self.realm, _ = labelled_entry(frame, "Realm", 0)
        self.oauth_base, _ = labelled_entry(frame, "OAuth host", 1)
        self.api_base, _ = labelled_entry(frame, "API host", 2)
        self.event_api_path, _ = labelled_entry(frame, "Event API path", 3)

        ttk.Label(frame, text="Environment").grid(row=4, column=0, sticky="w", pady=3)
        self.api_env = tk.StringVar()
        ttk.Combobox(
            frame, textvariable=self.api_env, values=["sandbox", "prod"],
            state="readonly", width=12,
        ).grid(row=4, column=1, sticky="w", pady=3)

        # Ariba rejects every request without these two.
        self.api_user, _ = labelled_entry(frame, "Integration user", 7)
        self.password_adapter, _ = labelled_entry(frame, "Password adapter", 8)

        ttk.Label(
            frame,
            text=("Enter hosts and the path only — not a full endpoint URL. "
                  "The OAuth host is data-centre specific; a wrong host looks "
                  "exactly like a wrong password."),
            foreground="#555", wraplength=460, justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # Showing the assembled URL makes a pasted endpoint or a missing slash
        # obvious here, instead of surfacing later as an unresolvable hostname.
        ttk.Label(frame, text="Will call:", foreground="#555").grid(
            row=6, column=0, sticky="nw", pady=(6, 0)
        )
        self._preview = tk.StringVar()
        preview = ttk.Label(frame, textvariable=self._preview, foreground="#0A6A2F",
                            wraplength=340, justify="left", font=("Consolas", 8))
        preview.grid(row=6, column=1, sticky="w", pady=(6, 0))

        for var in (self.realm, self.api_base, self.event_api_path, self.api_env,
                    self.api_user, self.password_adapter):
            var.trace_add("write", lambda *_a: self._update_preview())

    def _build_credentials(self, parent) -> None:
        frame = self._group(parent, "Credentials (from the SAP Ariba Developer Portal)")
        self.api_key, _ = labelled_entry(frame, "Application key (API key)", 0, show=MASK)
        self.client_id, _ = labelled_entry(frame, "OAuth Client ID", 1, show=MASK)
        self.client_secret, _ = labelled_entry(frame, "OAuth Client Secret", 2, show=MASK)

        ttk.Separator(frame, orient="horizontal").grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=8
        )
        self.basic_b64, _ = labelled_entry(
            frame, "Base64 credential (only if you have no ID/Secret)", 4, show=MASK
        )
        ttk.Label(
            frame,
            text=("Enter the Client ID and Secret — the app encodes them for you. "
                  "Use the Base64 box only if the portal gave you just that string; "
                  "an ID and Secret take precedence over it."),
            foreground="#555", wraplength=460, justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

        where = ("Windows Credential Manager" if self.secrets.persistent
                 else "this session only — Credential Manager is unavailable")
        ttk.Label(frame, text=f"Stored in: {where}",
                  foreground="#555" if self.secrets.persistent else "#B00020",
                  wraplength=460, justify="left").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

    def _build_commercial(self, parent) -> None:
        frame = self._group(parent, "Commercial conventions")
        self.gst_rate, _ = labelled_entry(frame, "GST rate (%)", 0, width=10)
        self.gst_inclusive = tk.BooleanVar()
        ttk.Checkbutton(
            frame, text="Ariba quotes already include GST", variable=self.gst_inclusive,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Label(
            frame,
            text=("If this is set wrongly every comparison row is misstated while still "
                  "looking plausible. Confirm it against a known event."),
            foreground="#555", wraplength=460, justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w")

    def _build_output(self, parent) -> None:
        frame = self._group(parent, "Output")
        self.output_dir, _ = labelled_entry(frame, "Save documents to", 0)
        ttk.Button(frame, text="Browse…", command=self._browse).grid(
            row=0, column=2, padx=(8, 0)
        )

    def _build_buttons(self, parent) -> None:
        parent.columnconfigure(0, weight=1)

        self.test_button = ttk.Button(parent, text="Test connection", command=self._test)
        self.test_button.grid(row=0, column=0, sticky="w")

        ttk.Button(parent, text="Cancel", command=self.destroy).grid(row=0, column=1, padx=6)
        self.save_button = ttk.Button(parent, text="Save settings", command=self._save)
        self.save_button.grid(row=0, column=2)
        self.save_button.focus_set()

        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())

    # ------------------------------------------------------------------ #

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(parent=self, initialdir=self.output_dir.get() or None)
        if chosen:
            self.output_dir.set(chosen)

    def _load(self) -> None:
        c = self.config_obj
        self.realm.set(c.realm)
        self.oauth_base.set(c.oauth_base)
        self.api_base.set(c.api_base)
        self.event_api_path.set(c.event_api_path)
        self.api_env.set(c.api_env)
        self.api_user.set(c.api_user)
        self.password_adapter.set(c.password_adapter)
        self.gst_rate.set(str(c.gst_rate))
        self.gst_inclusive.set(c.gst_inclusive)
        self.output_dir.set(c.output_dir)
        self.api_key.set(self.secrets.get(KEY_API_KEY) or "")
        self.client_id.set(self.secrets.get(KEY_CLIENT_ID) or "")
        self.client_secret.set(self.secrets.get(KEY_CLIENT_SECRET) or "")
        self.basic_b64.set(self.secrets.get(KEY_BASIC) or "")
        self._update_preview()

    def _update_preview(self) -> None:
        """Show the URL these settings will actually produce."""
        host = normalise_host(self.api_base.get())
        path = normalise_api_path(self.event_api_path.get())
        env = self.api_env.get().strip() or "sandbox"
        query = f"realm={self.realm.get().strip() or '…'}"
        user = self.api_user.get().strip()
        adapter = self.password_adapter.get().strip()
        if user:
            query += f"&user={user}"
        if adapter:
            query += f"&passwordAdapter={adapter}"
        self._preview.set(f"{host}{path}/{env}/events/{{eventId}}?{query}")

    def _apply_to_config(self) -> None:
        c = self.config_obj
        c.realm = self.realm.get().strip()
        c.oauth_base = self.oauth_base.get().strip()
        c.api_base = self.api_base.get().strip()
        c.event_api_path = self.event_api_path.get().strip()
        c.api_env = self.api_env.get().strip() or "sandbox"
        c.api_user = self.api_user.get().strip()
        c.password_adapter = self.password_adapter.get().strip()
        c.gst_rate = self.gst_rate.get().strip() or "18"
        c.gst_inclusive = bool(self.gst_inclusive.get())
        c.output_dir = self.output_dir.get().strip() or c.output_dir

        # Trim pasted full URLs back to a bare host and service path, then show
        # the cleaned values so it is obvious what will actually be called.
        c.normalise()
        self.oauth_base.set(c.oauth_base)
        self.api_base.set(c.api_base)
        self.event_api_path.set(c.event_api_path)
        self.realm.set(c.realm)
        self.api_user.set(c.api_user)              # may have been harvested
        self.password_adapter.set(c.password_adapter)
        self._update_preview()

    def _save(self) -> None:
        self._apply_to_config()
        persisted = True
        persisted &= self.secrets.set(KEY_API_KEY, self.api_key.get())
        persisted &= self.secrets.set(KEY_CLIENT_ID, self.client_id.get())
        persisted &= self.secrets.set(KEY_CLIENT_SECRET, self.client_secret.get())
        persisted &= self.secrets.set(KEY_BASIC, self.basic_b64.get())
        try:
            self.config_obj.save()
        except OSError as exc:
            messagebox.showerror("Settings", f"Could not save settings:\n{exc}", parent=self)
            return

        if not persisted:
            messagebox.showwarning(
                "Settings",
                "Settings saved, but the credentials could not be written to Windows "
                "Credential Manager. They will be kept for this session only and must "
                "be re-entered next time.",
                parent=self,
            )
        self.saved = True
        self.destroy()

    # ------------------------------------------------------------------ #
    # Test connection (threaded: it is a network call)
    # ------------------------------------------------------------------ #

    def _test(self) -> None:
        self._apply_to_config()
        api_key = self.api_key.get().strip()
        client_id = self.client_id.get().strip()
        client_secret = self.client_secret.get().strip()

        if client_id and client_secret:
            basic = encode_basic(client_id, client_secret)
        else:
            basic = self.basic_b64.get().strip()

        if not basic:
            messagebox.showwarning(
                "Test connection",
                "Enter the OAuth Client ID and Client Secret first "
                "(or the Base64 credential, if that is all you were given).",
                parent=self,
            )
            return

        self.test_button.configure(state="disabled", text="Testing…")
        config_snapshot = self.config_obj

        def worker():
            try:
                from ..ariba.client import AribaClient

                store = _EphemeralSecrets(basic, api_key)
                client = AribaClient(config_snapshot, secrets=store)
                self._queue.put(("ok", client.test_connection()))
            except NFAError as exc:
                self._queue.put(("error", exc.user_message))
            except Exception as exc:                     # noqa: BLE001
                log.exception("Test connection failed")
                self._queue.put(("error", f"Unexpected error: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                self.test_button.configure(state="normal", text="Test connection")
                if kind == "ok":
                    messagebox.showinfo("Test connection", payload, parent=self)
                else:
                    messagebox.showerror("Test connection", payload, parent=self)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain)


class _EphemeralSecrets:
    """Feeds the not-yet-saved values into a throwaway client for testing."""

    def __init__(self, basic: str, api_key: str):
        self._values = {KEY_BASIC: basic, KEY_API_KEY: api_key}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def basic_credential(self) -> str | None:
        return self._values.get(KEY_BASIC)

    def register_all_for_redaction(self) -> None:
        from ..logging_setup import redaction_filter

        redaction_filter.register(*[v for v in self._values.values() if v])
