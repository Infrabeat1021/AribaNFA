"""Field discovery: dump a real payload and flatten it to a readable path list.

The CSV is the point of this module. Custom fields are frequently keyed by
internal ID rather than the label shown in the Ariba UI, so the only practical
way to identify them is by the values they contain. The CSV opens in Excel,
sorts and filters, and puts a sample value beside every path.

Dumps can contain supplier pricing, so they are written under the user's profile
and are gitignored.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import DUMPS_DIR
from ..mapping.resolver import flatten

log = logging.getLogger(__name__)


@dataclass
class DumpResult:
    json_path: Path
    csv_path: Path
    row_count: int
    errors: dict[str, str]

    @property
    def directory(self) -> Path:
        return self.json_path.parent


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (name or "event"))[:60]


def write_paths_csv(payload: Any, path: Path) -> int:
    rows = flatten(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["path", "sample_value", "type", "occurrences"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def dump_event(source, event_id: str, out_dir: Path | None = None) -> DumpResult:
    """Fetch everything available for an event and write the diagnostics pair."""
    directory = Path(out_dir or DUMPS_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    if hasattr(source, "dump_event"):
        payload = source.dump_event(event_id)
    else:
        payload = source.fetch_event(event_id)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"event_{_safe(event_id)}_{stamp}"

    json_path = directory / f"{base}_raw.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    csv_path = directory / f"{base}_paths.csv"
    count = write_paths_csv(payload, csv_path)

    errors = payload.get("_errors", {}) if isinstance(payload, dict) else {}
    log.info("Dumped %s: %d distinct paths -> %s", event_id, count, csv_path)
    return DumpResult(json_path=json_path, csv_path=csv_path, row_count=count, errors=errors)
