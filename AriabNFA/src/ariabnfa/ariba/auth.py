"""OAuth 2.0 client-credentials token handling for SAP Ariba."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import requests

from ..errors import AribaAuthError, AribaNetworkError, AribaServerError

log = logging.getLogger(__name__)

TOKEN_PATH = "/v2/oauth/token"
#: Refresh this many seconds before the token actually expires, so a request
#: never goes out holding a token that dies in flight.
DEFAULT_SKEW = 60
DEFAULT_LIFETIME = 3600


class TokenProvider:
    """Fetches and caches an access token, refreshing it before it expires.

    The lock matters: the UI worker thread and any background call would
    otherwise race and fetch two tokens. `clock` is injectable so tests can
    fast-forward past expiry without sleeping.
    """

    def __init__(
        self,
        oauth_base: str,
        basic_b64: str,
        session: requests.Session,
        *,
        skew_seconds: int = DEFAULT_SKEW,
        clock: Callable[[], float] = time.monotonic,
        timeout: tuple[int, int] = (10, 30),
    ):
        self.oauth_base = (oauth_base or "").rstrip("/")
        self.basic_b64 = (basic_b64 or "").strip()
        self.session = session
        self.skew_seconds = skew_seconds
        self.clock = clock
        self.timeout = timeout

        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def token_url(self) -> str:
        return f"{self.oauth_base}{TOKEN_PATH}"

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def get_token(self) -> str:
        with self._lock:
            if self._token and self.clock() < self._expires_at:
                return self._token
            self._token, self._expires_at = self._fetch()
            return self._token

    def _fetch(self) -> tuple[str, float]:
        if not self.basic_b64:
            raise AribaAuthError(
                "No client credential configured",
                user_message="No Ariba client credential is saved. Open Settings to add it.",
            )
        log.debug("Requesting access token from %s", self.token_url)
        try:
            response = self.session.post(
                self.token_url,
                params={"grant_type": "client_credentials"},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Basic {self.basic_b64}",
                },
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            from .client import network_error_message

            raise AribaNetworkError(
                f"Token request failed: {exc}",
                user_message=network_error_message(exc, self.token_url),
            ) from exc

        if response.status_code in (400, 401, 403):
            log.debug("Token rejected: %s %s", response.status_code, response.text[:300])
            raise AribaAuthError(
                f"Token request returned {response.status_code}",
                user_message=(
                    "Ariba rejected the client credential.\n\n"
                    "Check the Base64 client credential in Settings, and confirm the "
                    "OAuth host matches your data centre - a wrong host looks exactly "
                    "like a wrong password."
                ),
            )
        if response.status_code >= 500:
            raise AribaServerError(f"Token endpoint returned {response.status_code}")
        if response.status_code != 200:
            raise AribaAuthError(f"Unexpected token response {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise AribaAuthError("Token response was not JSON") from exc

        token = payload.get("access_token")
        if not token:
            raise AribaAuthError("Token response contained no access_token")

        lifetime = _as_int(payload.get("expires_in"), DEFAULT_LIFETIME)
        expires_at = self.clock() + max(lifetime - self.skew_seconds, 1)
        log.info("Obtained access token, valid for %ss", lifetime)
        return token, expires_at


def _as_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
