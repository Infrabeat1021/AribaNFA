"""The review form: everything the user checks or writes before generating.

Two design choices matter here.

Fields Ariba could not supply are highlighted rather than left looking normal,
so it is obvious at a glance what still needs input.

The vendor list is fully editable, including the submitted/not-submitted flag.
That is what keeps the app useful before the bid-data entitlement lands, and it
is the fallback whenever a particular event's pricing does not come through
cleanly. Ranking is recomputed from these rows on every generate, so the
comparison table and the derived grid rows can never drift apart.
"""

from __future__ import annotations

import tkinter as tk
from decimal import Decimal
from tkinter import ttk

from ..mapping.formatting import plain_amount, to_decimal
from ..model import GRID_FIELDS, NFAData, VendorQuote, grid_letter

MISSING_BG = "#FFF3CD"          # amber: resolved to nothing, needs attention
NORMAL_BG = "white"
SPARE_VENDOR_ROWS = 2


class ReviewForm(ttk.Frame):
    """Edits an NFAData in place-ish: `collect()` returns the edited pieces."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.columnconfigure(0, weight=1)

        self._grid_vars: dict[str, tk.StringVar] = {}
        self._grid_entries: dict[str, tk.Entry] = {}
        self._vendor_rows: list[dict] = []
        self._data: NFAData | None = None

        self._build_subject()
        self._build_grid()
        self._build_vendors()
        self._build_justification()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    def _section(self, title: str, hint: str = "") -> ttk.Frame:
        header = ttk.Label(self, text=title, font=("Segoe UI", 10, "bold"))
        header.grid(sticky="w", pady=(14, 2))
        if hint:
            ttk.Label(self, text=hint, foreground="#555").grid(sticky="w", pady=(0, 4))
        frame = ttk.Frame(self)
        frame.grid(sticky="ew")
        frame.columnconfigure(1, weight=1)
        return frame

    def _build_subject(self) -> None:
        frame = self._section("Subject", "The one-line description printed under the title.")
        self._subject = tk.Text(frame, height=2, wrap="word")
        self._subject.grid(row=0, column=0, columnspan=2, sticky="ew")
        frame.columnconfigure(0, weight=1)

    def _build_grid(self) -> None:
        first, last = GRID_FIELDS[0][0], GRID_FIELDS[-1][0]
        frame = self._section(
            f"Event details ({first}–{last})",
            "Highlighted fields could not be found in Ariba — fill them in here.",
        )
        for index, (letter, key, label) in enumerate(GRID_FIELDS):
            ttk.Label(frame, text=f"{letter}.").grid(row=index, column=0, sticky="w", padx=(0, 4))
            ttk.Label(frame, text=label, width=42, anchor="w").grid(
                row=index, column=1, sticky="w", padx=(0, 8), pady=1
            )
            var = tk.StringVar()
            entry = tk.Entry(frame, textvariable=var, relief="solid", borderwidth=1)
            entry.grid(row=index, column=2, sticky="ew", pady=1)
            self._grid_vars[key] = var
            self._grid_entries[key] = entry
        frame.columnconfigure(2, weight=1)

        derived = ", ".join(grid_letter(k) for k in ("total_cost", "limited_enquiry"))
        note = ttk.Label(
            frame,
            text=f"Rows {derived} are filled automatically from the vendor list below.",
            foreground="#555",
        )
        note.grid(row=len(GRID_FIELDS), column=1, columnspan=2, sticky="w", pady=(4, 0))

    def _build_vendors(self) -> None:
        frame = self._section(
            "Vendors",
            "Untick a vendor that did not submit an offer. Enter the basic (pre-GST) value; "
            "GST, total and L1/L2/L3 ranking are calculated.",
        )
        self._vendor_frame = frame

        headers = ["Submitted", "Vendor name", "Basic value (INR)", "Rank", "Total (incl. GST)"]
        for column, text in enumerate(headers):
            ttk.Label(frame, text=text, font=("Segoe UI", 9, "bold")).grid(
                row=0, column=column, sticky="w", padx=(0, 8), pady=(0, 3)
            )
        frame.columnconfigure(1, weight=1)

        self._vendor_body_row = 1
        ttk.Button(frame, text="Add vendor", command=self._add_vendor_row).grid(
            row=999, column=0, sticky="w", pady=(6, 0)
        )

    def _build_justification(self) -> None:
        frame = self._section(
            "Justification",
            "The only written section in the document. Explain the process and why this award.",
        )
        self._justification = tk.Text(frame, height=9, wrap="word")
        self._justification.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

    # ------------------------------------------------------------------ #
    # Vendor rows
    # ------------------------------------------------------------------ #

    def _add_vendor_row(self, vendor: VendorQuote | None = None) -> dict:
        row_index = self._vendor_body_row
        self._vendor_body_row += 1

        submitted = tk.BooleanVar(value=vendor.responded if vendor else True)
        name = tk.StringVar(value=vendor.name if vendor else "")
        basic = tk.StringVar(
            value=plain_amount(vendor.basic) if vendor and vendor.basic is not None else ""
        )

        check = ttk.Checkbutton(self._vendor_frame, variable=submitted)
        check.grid(row=row_index, column=0, sticky="w")

        name_entry = ttk.Entry(self._vendor_frame, textvariable=name)
        name_entry.grid(row=row_index, column=1, sticky="ew", padx=(0, 8), pady=1)

        basic_entry = ttk.Entry(self._vendor_frame, textvariable=basic, width=16, justify="right")
        basic_entry.grid(row=row_index, column=2, sticky="w", padx=(0, 8))

        rank_label = ttk.Label(self._vendor_frame, text=vendor.rank_label if vendor else "—",
                               width=6)
        rank_label.grid(row=row_index, column=3, sticky="w")

        total_text = plain_amount(vendor.total) if vendor and vendor.total is not None else "—"
        total_label = ttk.Label(self._vendor_frame, text=total_text, width=16, anchor="e")
        total_label.grid(row=row_index, column=4, sticky="w")

        record = {
            "submitted": submitted, "name": name, "basic": basic,
            "gstin": vendor.gstin if vendor else None,
            "partial": vendor.partial_bid if vendor else False,
            "rank_label": rank_label, "total_label": total_label,
        }
        self._vendor_rows.append(record)
        return record

    def _clear_vendor_rows(self) -> None:
        for record in self._vendor_rows:
            record["rank_label"].destroy()
            record["total_label"].destroy()
        for child in list(self._vendor_frame.winfo_children()):
            info = child.grid_info()
            if info and 1 <= int(info.get("row", 0)) < 999:
                child.destroy()
        self._vendor_rows.clear()
        self._vendor_body_row = 1

    # ------------------------------------------------------------------ #
    # Load / collect
    # ------------------------------------------------------------------ #

    def load(self, data: NFAData) -> None:
        self._data = data
        # A disabled Text silently discards inserts, so the widgets must be
        # editable before anything is written into them.
        self.set_enabled(True)

        self._subject.delete("1.0", "end")
        self._subject.insert("1.0", data.subject or "")

        values = data.grid_values()
        unresolved = set(data.report.unresolved)
        for _letter, key, _label in GRID_FIELDS:
            self._grid_vars[key].set(values.get(key, ""))
            entry = self._grid_entries[key]
            blank = not values.get(key, "").strip()
            entry.configure(background=MISSING_BG if (blank or key in unresolved) else NORMAL_BG)

        self._clear_vendor_rows()
        vendors = data.all_vendors or data.vendors
        ranked_by_name = {v.name: v for v in data.vendors}
        for vendor in vendors:
            ranked = ranked_by_name.get(vendor.name, vendor)
            record = self._add_vendor_row(vendor)
            record["rank_label"].configure(text=ranked.rank_label if ranked.rank else "—")
            record["total_label"].configure(
                text=plain_amount(ranked.total) if ranked.total is not None else "—"
            )
        for _ in range(SPARE_VENDOR_ROWS):
            self._add_vendor_row()

        self._justification.delete("1.0", "end")
        self._justification.insert("1.0", data.justification or "")

    def collect(self) -> dict:
        """Return the edited content: subject, grid values, vendors, justification."""
        vendors: list[VendorQuote] = []
        for record in self._vendor_rows:
            name = record["name"].get().strip()
            if not name:
                continue
            basic = to_decimal(record["basic"].get())
            vendors.append(VendorQuote(
                name=name,
                basic=basic,
                gstin=record["gstin"],
                responded=bool(record["submitted"].get()),
                partial_bid=bool(record["partial"]),
            ))

        return {
            "subject": self._subject.get("1.0", "end").strip(),
            "grid_values": {key: var.get().strip() for key, var in self._grid_vars.items()},
            "vendors": vendors,
            "justification": self._justification.get("1.0", "end").strip(),
        }

    def state_dict(self) -> dict:
        """Serialisable snapshot, for draft persistence."""
        collected = self.collect()
        collected["vendors"] = [
            {
                "name": v.name,
                "basic": str(v.basic) if v.basic is not None else None,
                "gstin": v.gstin,
                "responded": v.responded,
                "partial_bid": v.partial_bid,
            }
            for v in collected["vendors"]
        ]
        return collected

    def apply_state(self, state: dict) -> None:
        """Restore a saved draft over the currently loaded data."""
        if not state:
            return
        self.set_enabled(True)
        if state.get("subject"):
            self._subject.delete("1.0", "end")
            self._subject.insert("1.0", state["subject"])
        for key, value in (state.get("grid_values") or {}).items():
            if key in self._grid_vars:
                self._grid_vars[key].set(value)
                if value.strip():
                    self._grid_entries[key].configure(background=NORMAL_BG)
        if state.get("justification"):
            self._justification.delete("1.0", "end")
            self._justification.insert("1.0", state["justification"])

        saved_vendors = state.get("vendors")
        if saved_vendors:
            self._clear_vendor_rows()
            for item in saved_vendors:
                self._add_vendor_row(VendorQuote(
                    name=item.get("name", ""),
                    basic=_decimal_or_none(item.get("basic")),
                    gstin=item.get("gstin"),
                    responded=bool(item.get("responded", True)),
                    partial_bid=bool(item.get("partial_bid", False)),
                ))
            for _ in range(SPARE_VENDOR_ROWS):
                self._add_vendor_row()

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._subject.configure(state=state)
        self._justification.configure(state=state)
        for entry in self._grid_entries.values():
            entry.configure(state=state)


def _decimal_or_none(value) -> Decimal | None:
    return to_decimal(value) if value not in (None, "") else None
