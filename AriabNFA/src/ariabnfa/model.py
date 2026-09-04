"""Plain data structures describing an NFA document.

Nothing in this module knows about Ariba or about python-docx. It is the seam
between fetching data and rendering it, which is what lets the document
pipeline be tested with no network and no credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

# The metadata grid, in document order. `key` is what the mapping layer and the
# review form use; `label` is what gets printed. Row letters are assigned from
# this order rather than written down, so adding or removing a row re-letters
# the rest automatically and no mapping key ever has to change.
GRID_DEFINITION: list[tuple[str, str]] = [
    ("order_type", "Type of Order"),
    ("user_department", "User Department"),
    ("equipment_area", "Equipment / Area"),
    ("plant", "Plant"),
    ("indentor", "Indentor"),
    ("pr_number", "PR number"),
    ("approved_budget", "Approved Budget"),
    ("total_cost", "Total Cost of this NFA"),
    ("oem_status", "OEM / NON-OEM / OTHERS"),
    ("single_tender", "Single tender"),
    ("limited_enquiry", "Limited Enquiry"),
]


def _row_letter(index: int) -> str:
    """A, B, ... Z, AA, AB - so the grid cannot run out of labels."""
    letter = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letter = chr(ord("A") + remainder) + letter
    return letter


GRID_FIELDS: list[tuple[str, str, str]] = [
    (_row_letter(i), key, label) for i, (key, label) in enumerate(GRID_DEFINITION)
]

GRID_KEYS = [key for _, key, _ in GRID_FIELDS]


def grid_letter(key: str) -> str:
    """The printed row letter for a field key."""
    for letter, candidate, _label in GRID_FIELDS:
        if candidate == key:
            return letter
    raise KeyError(key)


def grid_index(key: str) -> int:
    """Zero-based row position for a field key."""
    return GRID_KEYS.index(key)


@dataclass
class GridRow:
    """One row of the A-P metadata grid."""

    letter: str
    label: str
    value: str
    #: True when nothing could be resolved for this field. The builder renders
    #: these visibly rather than leaving a blank cell, because a blank cell in
    #: an approval document can be signed off without anyone noticing.
    missing: bool = False


@dataclass
class VendorQuote:
    """A vendor invited to the event, and their quote if they submitted one."""

    name: str
    basic: Decimal | None = None
    gst: Decimal | None = None
    total: Decimal | None = None
    gstin: str | None = None
    #: How Ariba identifies this vendor inside supplierBids, which carries no
    #: company name. Joining on it is what turns a bid into a named vendor.
    invitation_id: str | None = None
    responded: bool = True
    #: Bid on some but not all line items - ranked, but flagged, since an
    #: incomplete bid is not comparable like-for-like.
    partial_bid: bool = False
    #: 1, 2, 3 -> L1, L2, L3. Assigned by ranking.rank_vendors().
    rank: int | None = None
    #: Percentage above L1. None for L1 itself.
    variance_pct: Decimal | None = None

    @property
    def rank_label(self) -> str:
        return f"L{self.rank}" if self.rank else "-"


@dataclass
class LineItem:
    """One priced line of the event, with what each vendor quoted for it."""

    item_id: str
    title: str
    quantity: Decimal | None = None
    uom: str = ""
    #: The material code, which is what buyers recognise as the item number -
    #: `itemId` is Ariba's internal key.
    material_code: str = ""
    #: Held by Ariba as free text carrying its own currency ("40.00 - INR"), so
    #: it is shown verbatim rather than reformatted as a number.
    last_po_price: str = ""
    #: The line's Total Cost term, when the event carries one.
    total_price: Decimal | None = None
    #: vendor name -> extended price quoted for this line.
    prices: dict[str, Decimal] = field(default_factory=dict)

    def price_for(self, vendor_name: str) -> Decimal | None:
        return self.prices.get(vendor_name)

    @property
    def has_prices(self) -> bool:
        return any(v is not None for v in self.prices.values())


@dataclass
class MappingReport:
    """What the mapping layer could and could not resolve."""

    unresolved: list[str] = field(default_factory=list)
    used_path: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unresolved


@dataclass
class Entity:
    """A group company whose letterhead the NFA is issued on."""

    name: str
    address: str
    logo_path: Path | None = None
    #: False when the logo is a wordmark that already spells out the company
    #: name - printing it again beside the logo just reads as a duplicate.
    show_name: bool = True
    #: Printed width of the logo. Smaller means higher effective DPI, which
    #: matters when the source image is low resolution.
    logo_width_cm: float = 4.2


@dataclass
class NFAData:
    """Everything needed to render one NFA."""

    entity: Entity
    subject: str
    grid: list[GridRow]
    #: Ranked L1/L2/L3, at most three, responders only.
    vendors: list[VendorQuote]
    #: Every invited vendor as extracted, before ranking or trimming. This is
    #: what the review form edits; `vendors` is derived from it.
    all_vendors: list[VendorQuote] = field(default_factory=list)
    #: Invited vendors who did not submit an offer.
    non_responders: list[str] = field(default_factory=list)
    #: Priced lines of the event, for the item-wise breakdown section.
    line_items: list[LineItem] = field(default_factory=list)
    #: What Ariba awarded, when it has. Carried on the object so it survives a
    #: re-assembly after the user edits the form - the review form does not
    #: expose it, and recomputing it there would mean re-fetching.
    awarded_total: Decimal | None = None
    justification: str = ""
    doc_date: date | None = None
    gst_rate: Decimal = Decimal("18")
    event_id: str = ""
    pr_number: str = ""
    report: MappingReport = field(default_factory=MappingReport)

    def grid_values(self) -> dict[str, str]:
        """The grid as a key -> value dict, for editing and re-assembly."""
        by_letter = {row.letter: row.value for row in self.grid}
        return {key: by_letter.get(letter, "") for letter, key, _ in GRID_FIELDS}

    def grid_value(self, key: str) -> str | None:
        for letter, k, _ in GRID_FIELDS:
            if k == key:
                for row in self.grid:
                    if row.letter == letter:
                        return row.value
        return None


def build_grid(values: dict[str, str | None]) -> list[GridRow]:
    """Turn a key -> value mapping into the full A-P grid.

    Every letter always appears, in order. A key that is absent, None or blank
    is marked missing so the builder renders it visibly rather than as an empty
    cell that could be signed off unnoticed.
    """
    rows = []
    for letter, key, label in GRID_FIELDS:
        raw = values.get(key)
        text = "" if raw is None else str(raw).strip()
        rows.append(GridRow(letter=letter, label=label, value=text, missing=not text))
    return rows
