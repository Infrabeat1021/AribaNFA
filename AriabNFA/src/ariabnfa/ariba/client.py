"""Live SAP Ariba client.

Two things here are load-bearing:

* The realm is injected centrally in `_request`, never at call sites. It is a
  query parameter on every endpoint, and forgetting it on one call produces a
  confusing failure much later.
* 401 and 403 map to different exceptions. "Wrong credential" is fixed in
  Settings; "not entitled to this API" needs an administrator. Collapsing them
  into one error wastes days.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import AppConfig
from ..errors import (
    AribaAuthError,
    AribaForbiddenError,
    AribaNetworkError,
    AribaNotFoundError,
    AribaRateLimitError,
    AribaServerError,
    ConfigError,
)
from ..secrets_store import KEY_API_KEY, KEY_BASIC, SecretsStore
from .auth import TokenProvider

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60
#: $expand values worth trying, most complete first.
EXPAND_VARIANTS = ["all", "items,supplierInvitations,customFields", "none"]


def network_error_message(exc: Exception, url: str) -> str:
    """Explain a connection failure in terms of what the user can actually change.

    A hostname that does not resolve is almost always a typo in Settings, not a
    network outage - and telling someone to check their VPN when the real cause
    is a pasted URL sends them looking in completely the wrong place.
    """
    detail = str(exc)
    host = urlsplit(url).netloc or url

    if "getaddrinfo" in detail or "NameResolutionError" in detail or "Failed to resolve" in detail:
        return (
            f"The host '{host}' does not exist.\n\n"
            "This is a settings problem, not a network problem. Check Settings for:\n"
            "  • API host — should be just the host, e.g. https://openapi.au.cloud.ariba.com\n"
            "  • Event API path — should be only the service path, e.g. /api/sourcing-event/v2\n\n"
            "A full endpoint URL pasted into either box produces exactly this error."
        )
    if "timed out" in detail.lower() or "ReadTimeout" in detail or "ConnectTimeout" in detail:
        return (
            f"The request to '{host}' timed out.\n\n"
            "Ariba may be slow, or a firewall may be silently dropping the connection. "
            "Try again; if it persists, check whether this host is reachable from your network."
        )
    if "SSL" in detail or "CERTIFICATE" in detail.upper():
        return (
            f"The secure connection to '{host}' could not be established.\n\n"
            "If your network inspects TLS traffic, the corporate CA certificate must be "
            "set as the CA bundle in Settings."
        )
    return (
        f"Could not reach '{host}'.\n\n"
        "Check the network connection, VPN, or proxy settings."
    )


def build_session(config: AppConfig) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.trust_env = True                 # picks up HTTPS_PROXY if ever set
    if config.proxies:
        session.proxies.update(config.proxies)
    if config.ca_bundle:
        session.verify = config.ca_bundle
    return session


class AribaClient:
    """Talks to the Ariba Event Management API."""

    def __init__(
        self,
        config: AppConfig,
        secrets: SecretsStore | None = None,
        session: requests.Session | None = None,
    ):
        self.config = config
        self.secrets = secrets or SecretsStore()
        self.session = session or build_session(config)
        self.secrets.register_all_for_redaction()
        self.tokens = TokenProvider(
            config.oauth_base,
            self._basic_credential(),
            self.session,
        )

    def _basic_credential(self) -> str:
        """Prefer a stored Client ID/Secret pair; fall back to a pasted Base64."""
        getter = getattr(self.secrets, "basic_credential", None)
        if callable(getter):
            return getter() or ""
        return self.secrets.get(KEY_BASIC) or ""

    # ------------------------------------------------------------------ #
    # Plumbing
    # ------------------------------------------------------------------ #

    def _api_key(self) -> str:
        key = self.secrets.get(KEY_API_KEY)
        if not key:
            raise ConfigError(
                "No API key configured",
                user_message="No Ariba API key is saved. Open Settings to add it.",
            )
        return key

    def _require_realm(self) -> str:
        if not self.config.realm:
            raise ConfigError(
                "No realm configured",
                user_message="No Ariba realm is set. Open Settings and enter it.",
            )
        return self.config.realm

    def _request(self, path: str, params: dict | None = None, *, _retried: bool = False) -> Any:
        url = f"{self.config.event_api_base}{path}"
        query = dict(params or {})
        self._require_realm()
        # realm, user and passwordAdapter are required on every endpoint, so
        # they are injected centrally rather than at each call site.
        query.update(self.config.request_params())

        headers = {
            "apiKey": self._api_key(),
            "Authorization": f"Bearer {self.tokens.get_token()}",
            "Accept": "application/json",
        }

        log.debug("GET %s params=%s", url, query)
        try:
            response = self.session.get(
                url, params=query, headers=headers,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
        except requests.exceptions.RequestException as exc:
            raise AribaNetworkError(
                f"GET {url} failed: {exc}",
                user_message=network_error_message(exc, url),
            ) from exc

        log.debug("-> %s (%s bytes)", response.status_code, len(response.content))

        if response.status_code == 401 and not _retried:
            # The token may have been revoked early; refresh once, then give up.
            log.info("401 from %s - refreshing token and retrying once", path)
            self.tokens.invalidate()
            return self._request(path, params, _retried=True)

        self._raise_for_status(response, path)

        try:
            return response.json()
        except ValueError as exc:
            raise AribaServerError(
                f"Non-JSON response from {path}",
                user_message="Ariba returned an unexpected (non-JSON) response.",
            ) from exc

    @staticmethod
    def _ariba_message(response: requests.Response) -> str:
        """Ariba's own explanation, which is usually the most useful thing here."""
        try:
            payload = response.json()
        except ValueError:
            return ""
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error.get("description") or "").strip()
            if isinstance(error, str):
                return error.strip()
            return str(payload.get("message") or "").strip()
        return ""

    def _raise_for_status(self, response: requests.Response, path: str) -> None:
        code = response.status_code
        if code < 400:
            return
        body = response.text[:400]
        log.debug("Error body from %s: %s", path, body)

        if code == 400:
            detail = self._ariba_message(response)
            hint = ""
            if "user parameter" in detail.lower():
                hint = (
                    "\n\nAriba requires an integration user on every request. "
                    "Set 'Integration user' (and 'Password adapter') in Settings."
                )
            elif "realm" in detail.lower():
                hint = "\n\nCheck the Realm in Settings."
            raise AribaServerError(
                f"400 from {path}: {detail or body}",
                user_message=(
                    f"Ariba rejected the request:\n\n{detail or 'Bad request.'}{hint}"
                ),
            )

        if code == 401:
            raise AribaAuthError(f"401 from {path}")
        if code == 403:
            raise AribaForbiddenError(
                f"403 from {path}",
                user_message=(
                    "The credentials are valid, but this application is not entitled "
                    f"to {path} on realm '{self.config.realm}'.\n\n"
                    "Your Ariba administrator needs to grant access."
                ),
            )
        if code == 404:
            raise AribaNotFoundError(f"404 from {path}")
        if code == 429:
            raise AribaRateLimitError(f"429 from {path}")
        if code >= 500:
            raise AribaServerError(f"{code} from {path}")
        raise AribaServerError(
            f"{code} from {path}",
            user_message=f"Ariba returned HTTP {code} for {path}.",
        )

    # ------------------------------------------------------------------ #
    # Endpoints
    # ------------------------------------------------------------------ #

    def test_connection(self) -> str:
        """Fetch a token only. Isolates credentials from entitlement problems."""
        self.tokens.invalidate()
        self.tokens.get_token()
        return (
            f"Token obtained successfully.\n\n"
            f"OAuth host: {self.config.oauth_base}\n"
            f"API host:   {self.config.api_base}\n"
            f"Realm:      {self.config.realm or '(not set)'}\n"
            f"Environment: {self.config.api_env}"
        )

    def list_event_ids(self, limit: int = 25) -> list[str]:
        payload = self._request("/events/identifiers", {"$top": limit})
        rows = payload if isinstance(payload, list) else payload.get("value", payload.get("items", []))
        ids = []
        for row in rows or []:
            if isinstance(row, dict):
                value = row.get("internalId") or row.get("id") or row.get("eventId")
                if value:
                    ids.append(str(value))
            elif row:
                ids.append(str(row))
        return ids[:limit]

    def get_event(self, event_id: str, expand: str = "all") -> dict:
        params = {"$expand": expand} if expand and expand != "none" else {}
        payload = self._request(f"/events/{event_id}", params)
        return payload if isinstance(payload, dict) else {"value": payload}

    def get_event_items(self, event_id: str) -> Any:
        return self._request(f"/events/{event_id}/items")

    def get_award(self, event_id: str) -> Any:
        return self._request(f"/events/{event_id}/awards")

    def get_supplier_bids(self, event_id: str) -> Any:
        """Per-supplier, per-line bids — the real source of vendor pricing.

        Each entry identifies its bidder by `invitationId`, not by name, so the
        vendor is resolved by joining back to the event's supplierInvitations.
        Entries also include a per-bidder "Totals" roll-up row, which is not a
        line item and must not be counted as one.
        """
        return self._request(f"/events/{event_id}/supplierBids")

    def get_scenarios(self, event_id: str) -> Any:
        """Award scenarios, which carry the per-supplier, per-line-item bids.

        This is where line-item pricing lives: each scenario has a
        `supplierBids` array. The event itself only describes the terms - a
        line item's PRICE field is a definition, shared across participants,
        not a quoted figure.
        """
        return self._request(f"/events/{event_id}/scenarios")

    def fetch_event(self, event_id: str) -> dict:
        """Assemble the payload the mapping layer expects.

        Items and award data are best-effort: whether they are reachable depends
        on the realm's entitlements, and a missing award is recorded rather than
        raised so the rest of the NFA can still be produced.
        """
        event_id = (event_id or "").strip()
        if not event_id:
            raise AribaNotFoundError(
                "Empty event id", user_message="Enter an Ariba event ID first."
            )

        payload: dict[str, Any] = {"event": self.get_event(event_id), "_errors": {}}

        for name, call in (
            ("items", self.get_event_items),
            ("supplierBids", self.get_supplier_bids),
            ("award", self.get_award),
            ("scenarios", self.get_scenarios),
        ):
            try:
                payload[name] = call(event_id)
            except (AribaNotFoundError, AribaForbiddenError) as exc:
                # Expected on realms without the relevant entitlement.
                log.info("No %s data for %s: %s", name, event_id, exc)
                payload["_errors"][name] = str(exc)

        return payload

    def dump_event(self, event_id: str) -> dict:
        """Fetch every variant worth inspecting, for field discovery."""
        dump: dict[str, Any] = {"_event_id": event_id, "_errors": {}}
        for expand in EXPAND_VARIANTS:
            try:
                dump[f"event[$expand={expand}]"] = self.get_event(event_id, expand)
            except Exception as exc:                     # noqa: BLE001 - diagnostic
                dump["_errors"][f"expand={expand}"] = str(exc)
        for name, call in (
            ("items", self.get_event_items),
            ("supplierBids", self.get_supplier_bids),
            ("awards", self.get_award),
            ("scenarios", self.get_scenarios),
        ):
            try:
                dump[name] = call(event_id)
            except Exception as exc:                     # noqa: BLE001 - diagnostic
                dump["_errors"][name] = str(exc)
        return dump

    def describe(self) -> str:
        return f"Ariba {self.config.api_env} (realm {self.config.realm or 'not set'})"
