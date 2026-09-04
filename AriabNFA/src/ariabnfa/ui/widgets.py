"""Small reusable Tkinter pieces."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class ScrollableFrame(ttk.Frame):
    """A vertically scrolling container.

    Put content into `.body`. The review form is far taller than any sensible
    window, so this is what makes it usable rather than a wall of clipped rows.
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)

        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel())
        self.canvas.bind("<Leave>", lambda _e: self._unbind_wheel())

    def _on_body_configure(self, _event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)

    def fit_to_content(self, max_width: int | None = None,
                       max_height: int | None = None) -> tuple[int, int]:
        """Give the canvas a size request matching its content.

        A Canvas has no natural size, so a window containing one collapses to a
        few hundred pixels regardless of what is inside it. Setting an explicit
        request lets the parent window size itself sensibly, while the caps keep
        it within the screen and leave scrolling to handle the remainder.
        """
        self.update_idletasks()
        width = self.body.winfo_reqwidth()
        height = self.body.winfo_reqheight()
        if max_width is not None:
            width = min(width, max_width)
        if max_height is not None:
            height = min(height, max_height)
        self.canvas.configure(width=max(width, 1), height=max(height, 1))
        return width, height


class LogPane(ttk.Frame):
    """Read-only, auto-scrolling progress log."""

    def __init__(self, parent, height: int = 7, **kwargs):
        super().__init__(parent, **kwargs)
        self.text = tk.Text(self, height=height, wrap="word", state="disabled")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.text.tag_configure("error", foreground="#B00020")
        self.text.tag_configure("warn", foreground="#8A6D00")
        self.text.tag_configure("ok", foreground="#0A6A2F")

    def append(self, message: str, tag: str | None = None) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", message.rstrip() + "\n", tag or "")
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def contents(self) -> str:
        return self.text.get("1.0", "end").strip()


def labelled_entry(parent, label: str, row: int, *, width: int = 40, show: str | None = None):
    """A label in column 0 and an entry in column 1. Returns the StringVar."""
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
    var = tk.StringVar()
    entry = ttk.Entry(parent, textvariable=var, width=width, show=show)
    entry.grid(row=row, column=1, sticky="ew", pady=3)
    parent.columnconfigure(1, weight=1)
    return var, entry
