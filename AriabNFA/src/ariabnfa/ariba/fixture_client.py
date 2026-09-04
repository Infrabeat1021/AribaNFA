"""Offline event source backed by JSON fixtures.

Satisfies the same EventSource contract as the live client, so offline mode is
a genuine end-to-end exercise of the app rather than a mock of it.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import FIXTURES_DIR
from ..errors import AribaNotFoundError

DEFAULT_FIXTURE = "event_sample.json"


class FixtureClient:
    """Serves payloads from `tests/fixtures`.

    An event ID that names a fixture (with or without the .json suffix) loads
    that file; anything else falls back to the sample event, so typing a real
    event ID in offline mode still produces a document.
    """

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = Path(fixtures_dir or FIXTURES_DIR)

    def available(self) -> list[str]:
        return sorted(p.stem for p in self.fixtures_dir.glob("event_*.json"))

    def _resolve(self, event_id: str) -> Path:
        candidates = []
        name = (event_id or "").strip()
        if name:
            stem = name[:-5] if name.lower().endswith(".json") else name
            candidates += [f"{stem}.json", f"event_{stem}.json"]
            # "sparse" and "sample" are the two shorthands worth supporting.
            candidates.append(f"event_{stem.lower()}.json")
        candidates.append(DEFAULT_FIXTURE)

        for candidate in candidates:
            path = self.fixtures_dir / candidate
            if path.exists():
                return path
        raise AribaNotFoundError(
            f"No fixture for '{event_id}' in {self.fixtures_dir}",
            user_message=(
                f"No offline sample data found in:\n{self.fixtures_dir}\n\n"
                "Expected at least event_sample.json."
            ),
        )

    def fetch_event(self, event_id: str) -> dict:
        path = self._resolve(event_id)
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        payload.setdefault("_source", f"fixture:{path.name}")
        return payload

    def list_event_ids(self, limit: int = 25) -> list[str]:
        return self.available()[:limit]

    def describe(self) -> str:
        return f"Offline sample data ({self.fixtures_dir})"
