"""Resolve values out of Ariba JSON by declared paths.

Field names differ between realms, and custom fields are often keyed by internal
ID rather than the label shown in the Ariba UI. So nothing here assumes a shape:
each NFA field declares a list of candidate paths and the first one that yields
a non-empty value wins.

Path grammar:
    a.b            nested keys
    a[0].b         list index
    a[*].b         every element of a list, flattened
    a[k=v].b       the element(s) of a list whose key `k` equals `v`

The `[k=v]` form exists because Ariba returns item terms and custom fields as
lists of descriptor objects rather than keyed maps - a price lives at
`terms[fieldId=PRICE].value`, not at `terms.PRICE.value`.

`null`, `""`, `[]` and `{}` all count as "not found" and fall through to the next
candidate. That matters because Ariba routinely returns a key with a null value
rather than omitting the key.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[([^\]]*)\]")


class _Missing:
    """Sentinel distinct from None, so a genuine null can be told apart from
    a path that did not resolve at all."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


def is_empty(value: Any) -> bool:
    """True for values that should fall through to the next candidate path."""
    if value is MISSING or value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def parse_path(path: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    for key, bracket in _TOKEN_RE.findall(path):
        if key:
            tokens.append(("key", key))
            continue
        if bracket == "*":
            tokens.append(("wildcard", None))
        elif "=" in bracket:
            field, _, wanted = bracket.partition("=")
            tokens.append(("filter", (field.strip(), wanted.strip().strip("'\""))))
        else:
            try:
                tokens.append(("index", int(bracket)))
            except ValueError:
                # An unparseable selector must not resolve to something wrong.
                tokens.append(("filter", ("__never__", bracket)))
    return tokens


def _walk(value: Any, tokens: list[tuple[str, Any]]) -> Any:
    if not tokens:
        return value
    (kind, arg), rest = tokens[0], tokens[1:]

    if kind == "key":
        if isinstance(value, dict) and arg in value:
            return _walk(value[arg], rest)
        return MISSING

    if kind == "index":
        if isinstance(value, list) and -len(value) <= arg < len(value):
            return _walk(value[arg], rest)
        return MISSING

    if kind == "filter":
        field, wanted = arg
        if not isinstance(value, list):
            return MISSING
        matches = [
            item for item in value
            if isinstance(item, dict) and str(item.get(field, "")).strip() == wanted
        ]
        if not matches:
            return MISSING
        if len(matches) == 1:
            return _walk(matches[0], rest)
        value = matches           # several matched: behave like a wildcard

    # wildcard (and multi-match filter)
    if not isinstance(value, list):
        return MISSING
    collected: list[Any] = []
    for item in value:
        result = _walk(item, rest)
        if is_empty(result):
            continue
        collected.extend(result) if isinstance(result, list) else collected.append(result)
    return collected or MISSING


def resolve_path(document: Any, path: str) -> Any:
    """Resolve a single path. Returns MISSING if it does not resolve."""
    return _walk(document, parse_path(path))


def resolve_first(document: Any, paths: list[str]) -> tuple[Any, str | None]:
    """Try each path in order; return (value, path_that_worked).

    Returns (MISSING, None) when nothing resolved. The winning path is returned
    so the app can report which candidate actually supplied each value — that
    feedback is what makes tuning the mapping quick.
    """
    for path in paths or []:
        value = resolve_path(document, path)
        if not is_empty(value):
            return value, path
    return MISSING, None


def coerce_text(value: Any, *, separator: str = "\n") -> str:
    """Render a resolved value as document text."""
    if is_empty(value):
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        # Deduplicated, order preserved. A term repeated identically on every
        # line item - Plant is the same on all of them - would otherwise render
        # as "ITBA Ariba Technologies, ITBA Ariba Technologies".
        parts: list[str] = []
        for item in value:
            if is_empty(item):
                continue
            text = coerce_text(item, separator=separator)
            if text and text not in parts:
                parts.append(text)
        return separator.join(parts)
    if isinstance(value, dict):
        # Objects usually carry the display value under one of these keys.
        # Ariba wraps term values: text as {"simpleValue": ...}, money as
        # {"moneyValue": {"amount": ...}}, quantities as {"quantityValue": ...}.
        for key in ("simpleValue", "name", "displayName", "value", "label",
                    "amount", "code"):
            if key in value and not is_empty(value[key]):
                return coerce_text(value[key], separator=separator)
        for key in ("moneyValue", "quantityValue"):
            nested = value.get(key)
            if isinstance(nested, dict) and not is_empty(nested.get("amount")):
                return coerce_text(nested["amount"], separator=separator)
        return ""
    return str(value).strip()


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def flatten(document: Any, *, max_samples: int = 1) -> list[dict[str, Any]]:
    """Flatten a payload to leaf paths with sample values.

    List indices are normalised to `[*]` and repeats counted, so a 200-item
    event produces a readable list of distinct fields rather than 200 near
    duplicates. The sample value is the point of the exercise: opaque custom
    field IDs are identified by what they contain, not by their name.
    """
    found: dict[str, dict[str, Any]] = {}

    def visit(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if not node:
                record(path, node)
            for key, value in node.items():
                visit(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            if not node:
                record(path, node)
            for item in node:
                visit(item, f"{path}[*]")
        else:
            record(path, node)

    def record(path: str, value: Any) -> None:
        entry = found.get(path)
        if entry is None:
            entry = {
                "path": path,
                "type": type(value).__name__,
                "occurrences": 0,
                "samples": [],
            }
            found[path] = entry
        entry["occurrences"] += 1
        if not is_empty(value) and len(entry["samples"]) < max_samples:
            text = str(value)
            entry["samples"].append(text[:120])
            entry["type"] = type(value).__name__

    visit(document, "")
    rows = []
    for entry in found.values():
        rows.append({
            "path": entry["path"],
            "sample_value": " | ".join(entry["samples"]),
            "type": entry["type"],
            "occurrences": entry["occurrences"],
        })
    rows.sort(key=lambda r: r["path"])
    return rows
