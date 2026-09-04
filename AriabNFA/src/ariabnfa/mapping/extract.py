"""Convert an Ariba payload into NFAData.

This is the seam. Everything upstream deals in Ariba JSON; everything downstream
deals in plain data. Nothing here imports python-docx, and nothing in docgen
imports this.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..config import AppConfig, MAPPING_FILE
from ..errors import MappingError
from ..model import (
    GRID_KEYS,
    Entity,
    LineItem,
    MappingReport,
    NFAData,
    VendorQuote,
    build_grid,
)
from .formatting import date_str, gst_from_basic, gst_from_total, plain_amount, to_decimal
from .ranking import rank_vendors
from .resolver import MISSING, coerce_text, is_empty, resolve_first, resolve_path

log = logging.getLogger(__name__)


def load_mapping(path: Path | None = None) -> dict[str, Any]:
    path = Path(path or MAPPING_FILE)
    try:
        # utf-8-sig, because this file is meant to be hand-edited and Notepad
        # writes a BOM - which utf-8 rejects with an error that says nothing
        # about the real problem.
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise MappingError(
            f"Mapping file not found: {path}",
            user_message=f"The field mapping file is missing:\n{path}",
        ) from None
    except json.JSONDecodeError as exc:
        raise MappingError(
            f"Invalid JSON in {path}: {exc}",
            user_message=(
                f"The field mapping file has a syntax error on line {exc.lineno}:\n{path}"
            ),
        ) from None


def _format_by_type(value: Any, kind: str) -> str:
    if kind == "date":
        return date_str(coerce_text(value))
    if kind == "money":
        amount = to_decimal(coerce_text(value))
        return f"Rs. {plain_amount(amount)}/-" if amount is not None else ""
    if kind == "multiline":
        return coerce_text(value, separator="\n")
    return coerce_text(value, separator=", ")


def _resolve_spec(payload: dict, spec: dict, report: MappingReport, key: str) -> str:
    value, used = resolve_first(payload, spec.get("paths", []))
    if value is MISSING or is_empty(value):
        default = spec.get("default", "")
        if default:
            report.used_path[key] = "<default>"
            return str(default)
        report.unresolved.append(key)
        return ""
    report.used_path[key] = used or ""
    return _format_by_type(value, spec.get("type", "text"))


# --------------------------------------------------------------------------- #
# Vendors
# --------------------------------------------------------------------------- #

def _responded_flag(raw: Any, spec: dict) -> bool:
    """Interpret whatever the realm uses to mean 'submitted an offer'.

    Defaults to True when the value is unrecognised: a vendor wrongly shown as
    a participant is visible in the comparison table and gets corrected, whereas
    one wrongly dropped disappears silently.
    """
    if is_empty(raw):
        return True
    if isinstance(raw, bool):
        return raw
    text = coerce_text(raw).strip().lower()
    if text in [v.lower() for v in spec.get("not_responded_values", [])]:
        return False
    if text in [v.lower() for v in spec.get("responded_values", [])]:
        return True
    return True


def extract_vendors(payload: dict, mapping: dict, report: MappingReport) -> list[VendorQuote]:
    """Build the vendor list, grouping per-line-item rows by vendor."""
    spec = mapping.get("vendors", {})
    fields = spec.get("fields", {})

    rows, used_root = resolve_rows(payload, spec.get("root_paths", []))
    if not rows:
        report.unresolved.append("vendors")
        return []
    report.used_path["vendors"] = used_root or ""

    grouped: OrderedDict[str, VendorQuote] = OrderedDict()
    line_counts: dict[str, set] = {}

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        name = coerce_text(resolve_first(row, fields.get("name", []))[0])
        if not name:
            continue

        vendor = grouped.get(name)
        if vendor is None:
            vendor = VendorQuote(name=name)
            grouped[name] = vendor
            line_counts[name] = set()

        basic = to_decimal(coerce_text(resolve_first(row, fields.get("basic", []))[0]))
        total = to_decimal(coerce_text(resolve_first(row, fields.get("total", []))[0]))
        gst = to_decimal(coerce_text(resolve_first(row, fields.get("gst", []))[0]))

        # Per-line-item rows accumulate; a single summary row simply sets it.
        vendor.basic = _add(vendor.basic, basic)
        vendor.total = _add(vendor.total, total)
        vendor.gst = _add(vendor.gst, gst)

        if not vendor.gstin:
            vendor.gstin = coerce_text(resolve_first(row, fields.get("gstin", []))[0]) or None
        if not vendor.invitation_id:
            vendor.invitation_id = coerce_text(
                resolve_first(row, fields.get("invitation_id", []))[0]) or None

        responded_raw, _ = resolve_first(row, fields.get("responded", []))
        if not _responded_flag(responded_raw, spec):
            vendor.responded = False

        line_id = coerce_text(resolve_first(row, fields.get("line_id", []))[0]) or str(index)
        line_counts[name].add(line_id)

    # A vendor who quoted fewer line items than the widest bidder is flagged
    # rather than silently compared against more complete offers.
    widest = max((len(ids) for ids in line_counts.values()), default=0)
    if widest > 1:
        for name, vendor in grouped.items():
            if 0 < len(line_counts[name]) < widest:
                vendor.partial_bid = True

    return list(grouped.values())


# --------------------------------------------------------------------------- #
# Line items and per-supplier pricing
# --------------------------------------------------------------------------- #

def resolve_rows(payload: dict, paths: list[str]) -> tuple[list, str | None]:
    """Resolve the first candidate that yields an actual list of rows.

    Ariba wraps collections as `{"payload": [...]}`. When the payload is empty,
    a bare `supplierBids` fallback would otherwise resolve to the envelope dict
    itself - non-empty, and mistaken for one unreadable bid. That produced a
    note telling the user to check their mapping when the truth was simply that
    nobody had bid.
    """
    for path in paths or []:
        value = resolve_path(payload, path)
        if isinstance(value, dict) and "payload" in value:
            value = value["payload"]
        if isinstance(value, list) and value:
            return value, path
    return [], None


def _is_priced_line(row: dict, price_term_ids: list[str]) -> bool:
    """True when a row is an actual line item rather than a section header.

    GET /events/{id}/items returns sections, headers and questions alongside the
    real lines; the presence of a price term is what distinguishes them.
    """
    wanted = {t.upper() for t in price_term_ids}
    for term in row.get("terms") or []:
        if str(term.get("fieldId", "")).upper() in wanted:
            return True
    return False


def extract_line_items(payload: dict, mapping: dict, report: MappingReport) -> list[LineItem]:
    spec = mapping.get("line_items", {})
    fields = spec.get("fields", {})
    price_terms = spec.get("price_term_ids", ["PRICE"])

    rows, used_root = resolve_rows(payload, spec.get("root_paths", []))
    if not rows:
        return []
    report.used_path["line_items"] = used_root or ""

    items: list[LineItem] = []
    for row in rows:
        if not isinstance(row, dict) or not _is_priced_line(row, price_terms):
            continue
        title = coerce_text(resolve_first(row, fields.get("title", []))[0])
        item_id = coerce_text(resolve_first(row, fields.get("item_id", []))[0])
        if not (title or item_id):
            continue
        items.append(LineItem(
            item_id=item_id,
            title=title or item_id,
            quantity=to_decimal(coerce_text(resolve_first(row, fields.get("quantity", []))[0])),
            uom=coerce_text(resolve_first(row, fields.get("uom", []))[0]),
            material_code=coerce_text(
                resolve_first(row, fields.get("material_code", []))[0]),
            # Verbatim: Ariba holds this as text carrying its own currency.
            last_po_price=coerce_text(
                resolve_first(row, fields.get("last_po_price", []))[0]),
            total_price=to_decimal(
                coerce_text(resolve_first(row, fields.get("total_price", []))[0])),
        ))
    return items


def attach_supplier_bids(
    payload: dict,
    mapping: dict,
    line_items: list[LineItem],
    report: MappingReport,
    vendors: list[VendorQuote] | None = None,
) -> None:
    """Fill each line item's per-vendor prices from the scenarios payload.

    Handles both shapes Ariba might return: one bid row per vendor per line, and
    one row per vendor carrying a nested list of line bids.
    """
    # With no line items there is nothing to match bids against, and the
    # item-wise section will not render, so neither note below would be true.
    if not line_items:
        return

    spec = mapping.get("supplier_bids", {})
    fields = spec.get("fields", {})

    bids, used_root = resolve_rows(payload, spec.get("root_paths", []))
    if not bids:
        report.notes.append(
            "No supplier bids have been submitted for this event, so vendor "
            "prices and the item-wise breakdown are empty."
        )
        return
    report.used_path["supplier_bids"] = used_root or ""

    by_id = {i.item_id: i for i in line_items if i.item_id}
    by_title = {i.title: i for i in line_items if i.title}
    # supplierBids names its bidder only by invitationId, so the id is
    # translated back to the company name the rest of the document uses.
    by_invitation = {
        v.invitation_id: v.name
        for v in (vendors or []) if v.invitation_id and v.name
    }
    unknown_bidders: set[str] = set()
    matched = 0

    for bid in bids:
        if not isinstance(bid, dict):
            continue
        vendor = coerce_text(resolve_first(bid, fields.get("vendor", []))[0])
        if not vendor:
            continue
        if vendor in by_invitation:
            vendor = by_invitation[vendor]
        elif by_invitation and vendor not in {v.name for v in (vendors or [])}:
            unknown_bidders.add(vendor)

        nested, _ = resolve_first(bid, fields.get("line_bids", []))
        rows = nested if isinstance(nested, list) and nested else [bid]

        for row in rows:
            if not isinstance(row, dict):
                continue
            item_id = coerce_text(resolve_first(row, fields.get("item_id", []))[0])
            amount = to_decimal(
                coerce_text(resolve_first(row, fields.get("extended_price", []))[0])
            )
            if amount is None:
                amount = to_decimal(
                    coerce_text(resolve_first(row, fields.get("unit_price", []))[0])
                )
            if amount is None:
                continue

            target = by_id.get(item_id) or by_title.get(item_id)
            if target is None:
                continue
            target.prices[vendor] = amount
            matched += 1

    if not matched:
        report.notes.append(
            "Supplier bids were returned but none matched a line item; check the "
            "supplier_bids paths in nfa_mapping.json."
        )
    if unknown_bidders:
        report.notes.append(
            "Bids were received from bidders not on the invitation list: "
            + ", ".join(sorted(unknown_bidders))
        )


def _add(current: Decimal | None, addition: Decimal | None) -> Decimal | None:
    if addition is None:
        return current
    return addition if current is None else current + addition


# --------------------------------------------------------------------------- #
# Top level
# --------------------------------------------------------------------------- #

def extract_nfa(
    payload: dict,
    *,
    config: AppConfig,
    mapping: dict | None = None,
    entity: Entity | None = None,
) -> NFAData:
    """Turn an Ariba payload into NFAData, ranked and ready to render."""
    mapping = mapping or load_mapping()
    report = MappingReport()

    event_spec = mapping.get("event", {})
    subject = _resolve_spec(payload, event_spec.get("subject", {}), report, "subject")
    event_id = _resolve_spec(payload, event_spec.get("event_id", {}), report, "event_id")
    doc_date = _resolve_date(payload, event_spec.get("doc_date", {}), report, "doc_date")

    # Vendors are ranked once, in assemble_nfa, and only after line-item prices
    # have been attached. Ranking here as well produced notes from the
    # pre-pricing state that survived into the document alongside the real ones
    # - "No vendor submitted a priced offer" printed next to "Only 2 vendors
    # submitted". Contradictory notes on an approval document are worse than no
    # notes, so there is exactly one ranking pass.
    vendors = extract_vendors(payload, mapping, report)

    # Only resolve fields the document actually has a row for. A shared mapping
    # file is edited by several people and will sometimes be a version ahead or
    # behind; entries for removed rows would otherwise be reported as fields
    # "needing input" that the user can neither see nor fill.
    scalars = mapping.get("scalars", {})
    values = {
        key: _resolve_spec(payload, spec, report, key)
        for key, spec in scalars.items() if key in GRID_KEYS
    }
    stale = sorted(set(scalars) - set(GRID_KEYS))
    if stale:
        log.info("Mapping defines fields this version has no row for: %s",
                 ", ".join(stale))

    line_items = extract_line_items(payload, mapping, report)
    attach_supplier_bids(payload, mapping, line_items, report, vendors)
    awarded_total = resolve_awarded_total(payload, mapping, report)

    # A vendor with no header total can still be totalled from its line bids,
    # so the comparison table works even when only item pricing came through.
    _seed_totals_from_line_items(vendors, line_items)

    return assemble_nfa(
        entity=entity or config.entity(),
        subject=subject,
        grid_values=values,
        all_vendors=vendors,
        line_items=line_items,
        awarded_total=awarded_total,
        doc_date=doc_date,
        config=config,
        event_id=event_id,
        report=report,
    )


def _seed_totals_from_line_items(
    vendors: list[VendorQuote], line_items: list[LineItem]
) -> None:
    totals: dict[str, Decimal] = {}
    for item in line_items:
        for vendor_name, amount in item.prices.items():
            if amount is not None:
                totals[vendor_name] = totals.get(vendor_name, Decimal("0")) + amount

    known = {v.name for v in vendors}
    for vendor in vendors:
        if vendor.basic is None and vendor.total is None and vendor.name in totals:
            vendor.basic = totals[vendor.name]

    # A bidder present in the line pricing but not in the invitation list still
    # belongs in the comparison - dropping it would understate the field.
    for name, amount in totals.items():
        if name not in known:
            vendors.append(VendorQuote(name=name, basic=amount))


def assemble_nfa(
    *,
    entity: Entity,
    subject: str,
    grid_values: dict[str, str],
    all_vendors: list[VendorQuote],
    config: AppConfig,
    line_items: list[LineItem] | None = None,
    awarded_total: Decimal | None = None,
    justification: str = "",
    doc_date=None,
    event_id: str = "",
    report: MappingReport | None = None,
) -> NFAData:
    """Rank the vendors and build a complete NFAData.

    Called both after extraction and again after the user edits the review form,
    so the ranking and the derived grid rows are produced in exactly one place.
    """
    report = report or MappingReport()
    values = dict(grid_values)

    ranking = rank_vendors(
        all_vendors,
        top_n=config.top_n_vendors,
        gst_rate=config.gst_rate_decimal,
        gst_inclusive=config.gst_inclusive,
    )
    report.notes = [n for n in report.notes if n not in ranking.notes]
    report.notes.extend(ranking.notes)

    # Total cost and the limited-enquiry list are derived rather than taken
    # independently, so the grid can never contradict the comparison table.
    #
    # Precedence for the awarded value: what Ariba actually awarded wins over a
    # figure computed from the ranked L1, because an award can differ from the
    # lowest bid (split awards, negotiated totals, partial scope).
    l1 = ranking.l1
    if awarded_total is not None:
        # Ariba's award and scenario totals are sums of extended prices, i.e.
        # tax-exclusive, exactly like a vendor's quoted value. They get the same
        # GST treatment, otherwise this row would print ex-GST while the
        # comparison table beneath it prints inclusive - the same award shown as
        # two different numbers on one page.
        gross = _apply_gst(awarded_total, config)
        values["total_cost"] = f"Rs. {plain_amount(gross)}/-"
        _drop(report, "total_cost")
        report.used_path.setdefault("total_cost", "<awarded value from Ariba>")
        if l1 is not None and l1.total is not None and gross != l1.total:
            report.notes.append(
                f"Awarded value ({plain_amount(gross)}) differs from the "
                f"L1 total ({plain_amount(l1.total)}); the awarded figure is used."
            )
    elif l1 is not None:
        values["total_cost"] = f"Rs. {plain_amount(l1.total)}/-"
        _drop(report, "total_cost")

    enquiry = [f"{v.name} - Offer Submitted" for v in ranking.all_ranked]
    enquiry += [f"{name} - Offer not submitted" for name in ranking.non_responders]
    enquiry += [f"{name} - Offer Submitted (price not readable)" for name in ranking.unpriced]
    if enquiry:
        values["limited_enquiry"] = "\n".join(enquiry)
        _drop(report, "limited_enquiry")

    return NFAData(
        entity=entity,
        subject=subject,
        grid=build_grid(values),
        vendors=ranking.ranked,
        all_vendors=all_vendors,
        non_responders=ranking.non_responders,
        line_items=line_items or [],
        awarded_total=awarded_total,
        justification=justification,
        doc_date=doc_date,
        gst_rate=config.gst_rate_decimal,
        event_id=event_id,
        pr_number=values.get("pr_number", ""),
        report=report,
    )


def resolve_awarded_total(
    payload: dict, mapping: dict, report: MappingReport
) -> Decimal | None:
    """The awarded total from the awards payload.

    A path may resolve to several amounts - one Totals row per awarded item.
    Those are summed, because the awarded value of the NFA is the whole award,
    and the fact that it was summed is recorded so the figure is never a silent
    aggregate of things the reader cannot see.
    """
    spec = mapping.get("awarded_value", {})

    # 1. A real award. Several values here are the lines of one award, so they
    #    are summed.
    value, used = resolve_first(payload, spec.get("paths", []))
    amounts = _numbers(value)
    if amounts:
        report.used_path["awarded_total"] = used or ""
        if len(amounts) > 1:
            report.notes.append(
                f"Awarded value is the sum of {len(amounts)} awarded line totals "
                f"from Ariba."
            )
        return sum(amounts, Decimal("0"))

    # 2. Nothing awarded yet. Fall back to a scenario's projected total - but
    #    scenarios are mutually exclusive alternatives (Best Bid, Best Savings),
    #    so summing them would invent a number that means nothing. One is taken,
    #    and the document says the figure is a projection, not an award.
    value, used = resolve_first(payload, spec.get("scenario_paths", []))
    amounts = _numbers(value)
    if not amounts:
        return None

    chosen = amounts[0]
    report.used_path["awarded_total"] = used or ""

    # Name the alternatives. A reader deciding on this figure should know that
    # other scenarios project different totals, rather than have them dropped.
    every = _numbers(resolve_first(payload, spec.get("scenario_all_paths", []))[0])
    others = [a for a in dict.fromkeys(every) if a != chosen]
    detail = (" Other scenarios project "
              + ", ".join(plain_amount(a) for a in others) + ".") if others else ""

    report.notes.append(
        "This event has not been awarded yet. The total shown is the projected "
        f"value from an Ariba award scenario, not an awarded figure.{detail}"
    )
    return chosen


def _apply_gst(amount: Decimal, config: AppConfig) -> Decimal:
    """Gross an Ariba total up to the tax-inclusive figure the NFA reports."""
    rate = config.gst_rate_decimal
    split = (gst_from_total(amount, rate) if config.gst_inclusive
             else gst_from_basic(amount, rate))
    return split[2] if split else amount


def _numbers(value) -> list[Decimal]:
    if value is MISSING or is_empty(value):
        return []
    raw = value if isinstance(value, list) else [value]
    return [d for d in (to_decimal(v) for v in raw) if d is not None]


def _resolve_date(
    payload: dict, spec: dict, report: MappingReport | None = None, key: str = ""
) -> date | None:
    """Resolve a date field to a real date, or None to fall back to today."""
    value, used = resolve_first(payload, spec.get("paths", []))
    if is_empty(value):
        return None
    raw = coerce_text(value).strip().rstrip("Z")
    try:
        resolved = datetime.fromisoformat(raw).date()
    except ValueError:
        return None
    if report is not None and key:
        report.used_path[key] = used or ""
    return resolved


def _drop(report: MappingReport, key: str) -> None:
    if key in report.unresolved:
        report.unresolved.remove(key)
        report.used_path[key] = "<derived from ranked vendors>"
