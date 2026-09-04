"""Rank participating vendors L1 / L2 / L3 by total quoted value.

A mis-ranked L1 on an approval document is the most damaging bug this tool
could have, so every edge case here is explicit rather than incidental:
non-responders are excluded, ties share a rank, partial bids are flagged, and
vendors who responded without a usable price are surfaced rather than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..model import VendorQuote
from .formatting import gst_from_basic, gst_from_total, to_decimal, variance_pct


@dataclass
class RankResult:
    #: Vendors with rank <= top_n, in rank order. May exceed top_n if tied.
    ranked: list[VendorQuote] = field(default_factory=list)
    #: Every responder with a usable price, ranked.
    all_ranked: list[VendorQuote] = field(default_factory=list)
    #: Names of invited vendors who did not submit an offer.
    non_responders: list[str] = field(default_factory=list)
    #: Responders whose quote could not be read as a number.
    unpriced: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def l1(self) -> VendorQuote | None:
        return self.ranked[0] if self.ranked else None


def normalise_amounts(
    vendor: VendorQuote,
    *,
    gst_rate: Decimal = Decimal("18"),
    gst_inclusive: bool = False,
) -> VendorQuote:
    """Fill in basic / gst / total consistently from whichever one is known.

    `gst_inclusive` says how to read a vendor's quoted figure when only one
    amount is available. Getting this backwards misstates every row while
    looking entirely plausible, so it is a config decision, never a guess.
    """
    basic, gst, total = vendor.basic, vendor.gst, vendor.total

    if basic is not None and total is not None:
        if gst is None:
            gst = total - basic
    elif basic is not None:
        split = gst_from_basic(basic, gst_rate)
        if split:
            basic, gst, total = split
    elif total is not None:
        if gst_inclusive:
            split = gst_from_total(total, gst_rate)
        else:
            # The single figure is tax-exclusive; treat it as the basic value.
            split = gst_from_basic(total, gst_rate)
        if split:
            basic, gst, total = split

    vendor.basic, vendor.gst, vendor.total = basic, gst, total
    return vendor


def rank_vendors(
    vendors: list[VendorQuote],
    *,
    top_n: int = 3,
    gst_rate: Decimal = Decimal("18"),
    gst_inclusive: bool = False,
) -> RankResult:
    """Rank responders by total value ascending and compute variance vs L1."""
    result = RankResult()

    responders: list[VendorQuote] = []
    for vendor in vendors:
        if not vendor.responded:
            result.non_responders.append(vendor.name)
            continue
        normalise_amounts(vendor, gst_rate=gst_rate, gst_inclusive=gst_inclusive)
        if to_decimal(vendor.total) is None:
            result.unpriced.append(vendor.name)
            continue
        responders.append(vendor)

    if not responders:
        result.notes.append("No vendor submitted a priced offer.")
        return result

    responders.sort(key=lambda v: (v.total, v.name))

    # Standard competition ranking: equal totals share a rank, and the next
    # distinct total skips the ranks consumed by the tie.
    previous_total: Decimal | None = None
    previous_rank = 0
    for position, vendor in enumerate(responders, start=1):
        if previous_total is not None and vendor.total == previous_total:
            vendor.rank = previous_rank
        else:
            vendor.rank = position
            previous_rank = position
            previous_total = vendor.total

    l1_total = responders[0].total
    for vendor in responders:
        vendor.variance_pct = None if vendor.rank == 1 else variance_pct(vendor.total, l1_total)

    result.all_ranked = responders
    result.ranked = [v for v in responders if v.rank and v.rank <= top_n]

    _add_notes(result, responders, top_n)
    return result


def _add_notes(result: RankResult, responders: list[VendorQuote], top_n: int) -> None:
    tied_at_one = [v for v in responders if v.rank == 1]
    if len(tied_at_one) > 1:
        names = ", ".join(v.name for v in tied_at_one)
        result.notes.append(f"Tie at L1 between {names} - award requires a manual decision.")

    if len(responders) < top_n:
        result.notes.append(
            f"Only {len(responders)} vendor(s) submitted a priced offer; "
            f"fewer than the {top_n} normally compared."
        )

    partial = [v.name for v in result.ranked if v.partial_bid]
    if partial:
        result.notes.append(
            "Partial bid (not all line items quoted): " + ", ".join(partial)
        )

    if result.unpriced:
        result.notes.append(
            "Responded without a readable price: " + ", ".join(result.unpriced)
        )
