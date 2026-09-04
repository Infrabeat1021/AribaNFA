"""Exception taxonomy.

Each error carries a `user_message` written for whoever is looking at the
window, while the technical detail goes to the log. The 401/403 split is
deliberate and load-bearing: "the credentials are wrong" is something the user
fixes in Settings, whereas "these credentials are not entitled to this API" needs
an administrator. Collapsing them into one "auth failed" wastes days.
"""

from __future__ import annotations


class NFAError(Exception):
    """Base class. `user_message` is what the UI shows."""

    default_message = "Something went wrong. See the log for details."

    def __init__(self, message: str = "", *, user_message: str | None = None):
        super().__init__(message or self.default_message)
        self._user_message = user_message

    @property
    def user_message(self) -> str:
        return self._user_message or self.default_message


class ConfigError(NFAError):
    default_message = "Setup is incomplete — open Settings and fill in the missing values."


class AribaError(NFAError):
    """Anything that went wrong talking to Ariba."""


class AribaAuthError(AribaError):
    default_message = (
        "Ariba rejected the credentials. Check the API Key and client credential "
        "in Settings."
    )


class AribaForbiddenError(AribaError):
    default_message = (
        "The credentials are valid but not entitled to this API or realm. "
        "Contact your Ariba administrator."
    )


class AribaNotFoundError(AribaError):
    default_message = (
        "That event was not found. Check the event ID, and whether you are "
        "pointed at sandbox or production."
    )


class AribaRateLimitError(AribaError):
    default_message = "Ariba's rate limit was reached. Wait a minute and try again."


class AribaServerError(AribaError):
    default_message = "Ariba returned a server error. Try again shortly."


class AribaNetworkError(AribaError):
    default_message = (
        "Could not reach Ariba. Check the network connection, VPN, or proxy settings."
    )


class MappingError(NFAError):
    default_message = "The field mapping file could not be read."


class DocumentError(NFAError):
    default_message = "The Word document could not be created."
