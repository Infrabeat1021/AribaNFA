"""A worked example modelled on the real SF6 Breaker NFA (PR 1020600469).

Used by the tests and by offline mode, so the whole pipeline can be exercised
before Ariba credentials exist. The L1 figures match the document on file, which
makes this a known-answer check once live data arrives.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from .model import Entity, NFAData, VendorQuote, build_grid
from .mapping.formatting import plain_amount
from .mapping.ranking import rank_vendors

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "letterheads"

INFRABEAT = Entity(
    name="InfraBeat",
    address="",
    logo_path=ASSETS / "infrabeat.png",
    show_name=False,          # the logo is a wordmark
    logo_width_cm=3.8,
)


def sample_vendors() -> list[VendorQuote]:
    """Four invited vendors: three priced offers and one non-responder."""
    return [
        VendorQuote(name="M/s Maranatha Engineers", basic=Decimal("39000"),
                    gstin="33BQFPP2161K1ZT"),
        VendorQuote(name="M/s Micronovaimpex", basic=Decimal("44500")),
        VendorQuote(name="M/s Asias Electric", basic=Decimal("47200")),
        VendorQuote(name="M/s Adroit Automation", responded=False),
    ]


def build_sample() -> NFAData:
    gst_rate = Decimal("18")
    all_vendors = sample_vendors()
    result = rank_vendors(all_vendors, gst_rate=gst_rate)
    l1 = result.l1

    # Row N lists every invited vendor with their participation status, and
    # rows K and O are taken from the ranked L1 - deriving them rather than
    # restating them is what stops the grid disagreeing with the table.
    enquiry_lines = [f"{v.name} - Offer Submitted" for v in result.all_ranked]
    enquiry_lines += [f"{name} - Offer not submitted" for name in result.non_responders]

    grid = build_grid({
        "order_type": "Service",
        "user_department": "O&M",
        "equipment_area": "Karnataka Site",
        "indentor": "Nimesh Malvi",
        "pr_number": "1020600469",
        "approved_budget": "As per user, Budget is available",
        "total_cost": f"Rs. {plain_amount(l1.total)}/-" if l1 else "",
        "oem_status": "Non-OEM",
        "single_tender": "-",
        "limited_enquiry": "\n".join(enquiry_lines),
    })

    data = NFAData(
        entity=INFRABEAT,
        subject=(
            "NFA for issuing Service Order for Repairing and Servicing of "
            "2000A/145kV (FSA1) SF6 Breaker at Solitaire BTN Solar Private "
            "Limited, Tamil Nadu."
        ),
        grid=grid,
        vendors=result.ranked,
        all_vendors=all_vendors,
        non_responders=result.non_responders,
        justification=(
            "Enquiry was floated to four bidders for repairing and servicing of the "
            "2000A/145kV SF6 Breaker. Three bidders submitted techno-commercial offers "
            "and the technical proposals were accepted by the user department. After "
            "negotiation, final no-regret offers were obtained and M/s Maranatha "
            "Engineers emerged as L1. The quoted price is within the available budget "
            "and is recommended for award."
        ),
        doc_date=date(2026, 1, 3),
        gst_rate=gst_rate,
        event_id="WS1020600469",
        pr_number="1020600469",
    )
    data.report.notes.extend(result.notes)
    return data
