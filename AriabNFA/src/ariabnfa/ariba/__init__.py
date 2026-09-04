"""SAP Ariba API access. Knows nothing about Word documents.

`EventSource` is the contract both the live client and the fixture client
satisfy. Keeping them interchangeable is what lets the whole app - UI,
threading, mapping, ranking, document generation - be exercised end to end with
no network and no credentials.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EventSource(Protocol):
    """Anything that can supply an Ariba event payload."""

    def fetch_event(self, event_id: str) -> dict:
        """Return the combined payload: {"event": ..., "items": ..., "award": ...}."""
        ...

    def list_event_ids(self, limit: int = 25) -> list[str]:
        """Recent event identifiers, newest first."""
        ...

    def describe(self) -> str:
        """Short human-readable description of where data is coming from."""
        ...
