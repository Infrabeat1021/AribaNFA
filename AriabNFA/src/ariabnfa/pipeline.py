"""Orchestration: fetch -> extract -> rank -> (user edits) -> build -> save.

Kept free of any UI so the same path can be driven from a test or the command
line as from the window.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from .ariba import EventSource
from .ariba.fixture_client import FixtureClient
from .config import AppConfig
from .docgen import NFABuilder, output_filename
from .errors import DocumentError
from .mapping.extract import extract_nfa, load_mapping
from .model import Entity, NFAData

log = logging.getLogger(__name__)


def make_source(config: AppConfig, *, offline: bool) -> EventSource:
    """Pick the event source. Offline mode is a first-class path, not a mock."""
    if offline:
        return FixtureClient()
    from .ariba.client import AribaClient      # imported lazily: needs credentials
    return AribaClient(config)


def fetch_nfa(
    source: EventSource,
    event_id: str,
    *,
    config: AppConfig,
    entity: Entity | None = None,
) -> NFAData:
    """Fetch an event and turn it into NFAData, ready for review."""
    log.info("Fetching event %s from %s", event_id, source.describe())
    payload = source.fetch_event(event_id)
    mapping_path = config.mapping_path
    if config.mapping_file:
        log.info("Using mapping file %s (shared: %s)", mapping_path, config.mapping_is_shared)
        if not config.mapping_is_shared:
            log.warning("Shared mapping %s is unreachable; using the local copy",
                        config.mapping_file)
    data = extract_nfa(payload, config=config,
                       mapping=load_mapping(mapping_path), entity=entity)
    if not data.event_id:
        data.event_id = event_id
    log.info(
        "Extracted event %s: %d ranked vendor(s), %d unresolved field(s)",
        data.event_id, len(data.vendors), len(data.report.unresolved),
    )
    if data.report.unresolved:
        log.debug("Unresolved fields: %s", ", ".join(data.report.unresolved))
    return data


def generate_document(data: NFAData, *, config: AppConfig, out_dir: Path | None = None) -> Path:
    """Build the .docx and write it to disk."""
    directory = Path(out_dir or config.output_dir)
    target = directory / output_filename(data)
    try:
        builder = NFABuilder(data)
        builder.build()
        saved = builder.save(target)
    except PermissionError:
        raise DocumentError(
            f"Cannot write {target}",
            user_message=(
                f"Could not save the document:\n{target}\n\n"
                "It may already be open in Word. Close it and try again."
            ),
        ) from None
    except OSError as exc:
        raise DocumentError(str(exc), user_message=f"Could not save the document:\n{exc}") from None

    log.info("Wrote %s (%d bytes)", saved, saved.stat().st_size)
    return saved


def open_path(path: Path) -> bool:
    """Open a file or folder with its shell association.

    Never fatal: by the time this runs the document is already saved, so a
    failure to launch Word is a nuisance, not a lost result.
    """
    try:
        if sys.platform == "win32":
            os.startfile(str(path))       # noqa: S606 - intended shell association
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        return True
    except OSError as exc:
        log.warning("Could not open %s: %s", path, exc)
        return False


#: Kept for readability at call sites that open the finished .docx.
open_in_word = open_path
