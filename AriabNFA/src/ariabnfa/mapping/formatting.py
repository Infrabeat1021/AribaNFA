"""Money, number and date formatting for Indian procurement documents.

Every function here is None-safe and returns a placeholder rather than raising.
A formatting error part-way through a build would lose the whole document, and
the value that triggered it is exactly the value someone needs to see is wrong.

`locale.setlocale` is deliberately not used: it is process-global and depends on
a machine locale we cannot rely on. Grouping is done by hand instead.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

PLACEHOLDER = "—"

_ONES = [
    "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
    "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
    "Sixteen", "Seventeen", "Eighteen", "Nineteen",
]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety"]


def to_decimal(value) -> Decimal | None:
    """Best-effort conversion to Decimal. Returns None if it isn't a number.

    Accepts the shapes Ariba actually returns: numbers, numeric strings, and
    strings carrying currency symbols, commas or a trailing '/-'.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        # Pick out the first numeric token rather than stripping punctuation:
        # figures arrive as "Rs. 46,020/-", where both the leading "Rs." dot and
        # the trailing "/-" would otherwise corrupt the number.
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if not match:
            return None
        try:
            return Decimal(match.group())
        except InvalidOperation:
            return None
    return None


def _group_indian(digits: str) -> str:
    """Group an integer digit string 2-2-3, e.g. 12345678 -> 1,23,45,678."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def inr_figure(value, *, prefix: str = "Rs. ", suffix: str = "/-") -> str:
    """Format as an Indian-grouped rupee figure, e.g. 'Rs. 46,020/-'."""
    amount = to_decimal(value)
    if amount is None:
        return PLACEHOLDER
    quantised = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    negative = quantised < 0
    quantised = abs(quantised)
    integral = int(quantised)
    paise = int((quantised - integral) * 100)
    text = _group_indian(str(integral))
    if paise:
        text = f"{text}.{paise:02d}"
    return f"{'-' if negative else ''}{prefix}{text}{suffix}"


def plain_amount(value) -> str:
    """Indian-grouped figure with no prefix or suffix, for table cells."""
    return inr_figure(value, prefix="", suffix="")


def _two_digits(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")


def _three_digits(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_two_digits(rest))
    return " ".join(parts)


def _indian_words(n: int) -> str:
    """Integer to words using the Indian system (lakh / crore)."""
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1_000)
    if crore:
        # Counts above 99 crore are themselves grouped Indian-style.
        prefix = _indian_words(crore) if crore >= 100 else _three_digits(crore)
        parts.append(f"{prefix} Crore")
    if lakh:
        parts.append(f"{_three_digits(lakh)} Lakh")
    if thousand:
        parts.append(f"{_three_digits(thousand)} Thousand")
    if n:
        parts.append(_three_digits(n))
    return " ".join(parts)


def inr_words(value, *, currency: str = "INR", closing: str = "Only") -> str:
    """Amount in words, e.g. 'INR Forty Six Thousand Twenty Only'."""
    amount = to_decimal(value)
    if amount is None:
        return PLACEHOLDER
    quantised = abs(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    integral = int(quantised)
    paise = int((quantised - integral) * 100)
    words = _indian_words(integral)
    if paise:
        words = f"{words} and {_two_digits(paise)} Paise"
    sign = "Minus " if amount < 0 else ""
    return " ".join(p for p in (currency, sign + words, closing) if p).strip()


def gst_from_basic(basic, rate=Decimal("18")) -> tuple[Decimal, Decimal, Decimal] | None:
    """Split a tax-exclusive amount into (basic, gst, total)."""
    base = to_decimal(basic)
    pct = to_decimal(rate)
    if base is None or pct is None:
        return None
    tax = (base * pct / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return base, tax, base + tax


def gst_from_total(total, rate=Decimal("18")) -> tuple[Decimal, Decimal, Decimal] | None:
    """Split a tax-inclusive amount into (basic, gst, total)."""
    gross = to_decimal(total)
    pct = to_decimal(rate)
    if gross is None or pct is None:
        return None
    base = (gross * 100 / (100 + pct)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return base, gross - base, gross


def variance_pct(value, base) -> Decimal | None:
    """Percentage by which `value` exceeds `base`. None if not computable."""
    v, b = to_decimal(value), to_decimal(base)
    if v is None or b is None or b == 0:
        return None
    return ((v - b) / b * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def variance_str(pct: Decimal | None) -> str:
    if pct is None:
        return PLACEHOLDER
    if pct == 0:
        return "—"
    return f"{'+' if pct > 0 else ''}{pct}%"


def date_str(value, fmt: str = "%d.%m.%Y") -> str:
    """Format a date as DD.MM.YYYY, matching the reference NFA."""
    if value is None or value == "":
        return PLACEHOLDER
    if isinstance(value, datetime):
        return value.strftime(fmt)
    if isinstance(value, date):
        return value.strftime(fmt)
    if isinstance(value, str):
        raw = value.strip().rstrip("Z")
        try:
            return datetime.fromisoformat(raw).strftime(fmt)
        except ValueError:
            # Unrecognised shape: show it as-is rather than losing the value.
            return value.strip()
    return str(value)
