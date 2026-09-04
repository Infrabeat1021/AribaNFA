"""Credential storage backed by Windows Credential Manager.

Chosen over the alternatives deliberately:

* Environment variables leak to every child process, are readable with
  `Get-ChildItem env:`, and in practice end up pasted into a .bat file in the
  project folder - the exact "secret in source" outcome to avoid. Kept only as
  a test override.
* A plaintext config file leaves org-wide Ariba credentials at rest on a
  corporate endpoint, which fails a security review.

Credential Manager is DPAPI-encrypted per user and revocable through a built-in
Windows UI. If its backend is unavailable, secrets are held in memory for the
session only and the caller is told - they are never quietly written to disk.
"""

from __future__ import annotations

import base64
import logging
import os

from . import APP_NAME
from .logging_setup import redaction_filter

log = logging.getLogger(__name__)

KEY_BASIC = "ariba_basic_b64"
KEY_API_KEY = "ariba_api_key"
KEY_CLIENT_ID = "ariba_client_id"
KEY_CLIENT_SECRET = "ariba_client_secret"

ENV_OVERRIDES = {
    KEY_BASIC: "ARIBA_BASIC_B64",
    KEY_API_KEY: "ARIBA_API_KEY",
    KEY_CLIENT_ID: "ARIBA_CLIENT_ID",
    KEY_CLIENT_SECRET: "ARIBA_CLIENT_SECRET",
}


def encode_basic(client_id: str, client_secret: str) -> str:
    """Base64-encode `clientId:clientSecret` for the OAuth Basic header."""
    raw = f"{(client_id or '').strip()}:{(client_secret or '').strip()}"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


class SecretsStore:
    """Resolution order: environment override, then Credential Manager."""

    def __init__(self, service: str = APP_NAME):
        self.service = service
        self._memory: dict[str, str] = {}
        self._backend_ok = True
        self._keyring = None
        try:
            import keyring

            self._keyring = keyring
            keyring.get_keyring()
        except Exception as exc:            # noqa: BLE001 - any backend failure
            self._backend_ok = False
            log.warning("Credential Manager unavailable (%s); secrets are session-only", exc)

    @property
    def persistent(self) -> bool:
        """False when secrets will be lost on exit."""
        return self._backend_ok

    def get(self, name: str) -> str | None:
        env_name = ENV_OVERRIDES.get(name)
        if env_name and os.environ.get(env_name):
            value = os.environ[env_name]
            redaction_filter.register(value)
            return value

        if name in self._memory:
            return self._memory[name]

        if self._backend_ok and self._keyring:
            try:
                value = self._keyring.get_password(self.service, name)
            except Exception as exc:        # noqa: BLE001
                log.warning("Could not read '%s' from Credential Manager: %s", name, exc)
                return None
            if value:
                redaction_filter.register(value)
            return value
        return None

    def set(self, name: str, value: str) -> bool:
        """Store a secret. Returns False when it could only be held in memory."""
        value = (value or "").strip()
        if not value:
            return self.delete(name)
        redaction_filter.register(value)

        if self._backend_ok and self._keyring:
            try:
                self._keyring.set_password(self.service, name, value)
                self._memory.pop(name, None)
                return True
            except Exception as exc:        # noqa: BLE001
                log.warning("Could not save '%s' to Credential Manager: %s", name, exc)
                self._backend_ok = False

        self._memory[name] = value
        return False

    def delete(self, name: str) -> bool:
        self._memory.pop(name, None)
        if self._backend_ok and self._keyring:
            try:
                self._keyring.delete_password(self.service, name)
            except Exception:               # noqa: BLE001 - absent is not an error
                pass
        return True

    def basic_credential(self) -> str | None:
        """The Base64 `clientId:clientSecret` used for the OAuth Basic header.

        The Ariba developer portal shows both a Client ID / Client Secret pair
        and a pre-encoded string, and which one someone has to hand varies. A
        stored ID and secret win, because they are the values a person can
        actually check against the portal; the encoded string is accepted for
        anyone who only ever received that.
        """
        client_id = self.get(KEY_CLIENT_ID)
        client_secret = self.get(KEY_CLIENT_SECRET)
        if client_id and client_secret:
            encoded = encode_basic(client_id, client_secret)
            redaction_filter.register(encoded)
            return encoded
        return self.get(KEY_BASIC)

    def has_all(self) -> bool:
        return bool(self.basic_credential() and self.get(KEY_API_KEY))

    def register_all_for_redaction(self) -> None:
        """Make sure every stored secret is scrubbed from logs."""
        for name in (KEY_BASIC, KEY_API_KEY, KEY_CLIENT_ID, KEY_CLIENT_SECRET):
            redaction_filter.register(self.get(name))
        redaction_filter.register(self.basic_credential())
