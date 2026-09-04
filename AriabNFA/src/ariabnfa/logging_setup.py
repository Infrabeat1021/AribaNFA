"""Logging, with mandatory secret redaction.

DEBUG logging of request URLs and headers is what makes this app debuggable.
Without redaction it would also make the log file a plaintext credential store -
and log files get emailed to IT support. So the filter is not optional and is
installed on the root logger, not on individual handlers.
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LOG_DIR

LOG_FILE = "ariabnfa.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 5
REDACTED = "***REDACTED***"

#: Patterns that catch secrets wherever they appear in a message.
_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r'("access_token"\s*:\s*")[^"]+'),
    re.compile(r"(Basic\s+)[A-Za-z0-9+/=]+", re.IGNORECASE),
    re.compile(r"(apiKey['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-]+", re.IGNORECASE),
]


class SecretRedactionFilter(logging.Filter):
    """Scrub known secret values and token-shaped strings from every record."""

    def __init__(self):
        super().__init__()
        self._literals: set[str] = set()

    def register(self, *values: str | None) -> None:
        """Register a literal secret (an API key, a base64 credential)."""
        for value in values:
            # Very short values would cause false-positive redaction everywhere.
            if value and len(value) >= 8:
                self._literals.add(value)

    def _scrub(self, text: str) -> str:
        for literal in self._literals:
            text = text.replace(literal, REDACTED)
        for pattern in _PATTERNS:
            text = pattern.sub(rf"\1{REDACTED}", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._scrub(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    self._scrub(a) if isinstance(a, str) else a for a in record.args
                )
        return True


redaction_filter = SecretRedactionFilter()


def setup_logging(*, verbose: bool = False, log_dir: Path | None = None) -> Path | None:
    """Configure root logging. Returns the log file path, or None if unwritable."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addFilter(redaction_filter)

    file_path: Path | None = None
    directory = Path(log_dir or LOG_DIR)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / LOG_FILE
        file_handler = RotatingFileHandler(
            file_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
        ))
        file_handler.addFilter(redaction_filter)
        root.addHandler(file_handler)
    except OSError:
        # A missing log file must never stop the app starting.
        file_path = None

    if verbose or sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.DEBUG if verbose else logging.WARNING)
        console.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        console.addFilter(redaction_filter)
        root.addHandler(console)

    # urllib3 logs full URLs at DEBUG; useful, and now safely redacted.
    logging.getLogger("urllib3").setLevel(logging.INFO)
    return file_path
